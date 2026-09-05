"""Unit tests for modules.subscriptions.stripe_api (mocked SDK, no real calls)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
import stripe
from stripe._stripe_object import StripeObject

from modules.subscriptions import stripe_api


def _stripe_object(data: dict) -> StripeObject:
    """A real StripeObject, not a dict.

    The SDK returns these, and `.get()` on one raises AttributeError rather
    than behaving like a dict — a plain-dict fixture would hide any code that
    forgets to call `.to_dict()` first. That exact bug cost a debugging session
    in the deleted web/ package, so the fixtures here stay faithful.
    """
    return StripeObject.construct_from(data, "sk_test_stub")


def _subscription(**overrides) -> dict:
    data = {
        "id": "sub_123",
        "status": "active",
        "created": 1788000000,
        "current_period_end": 1790592000,
        "customer": {
            "id": "cus_123",
            "name": "Jane Doe",
            "email": "jane@example.com",
        },
        "items": {"data": [{"price": {"unit_amount": 3000, "currency": "usd"}}]},
    }
    data.update(overrides)
    return data


@pytest.fixture(autouse=True)
def _configured_key():
    """Settings is a frozen dataclass, so per-attribute patching raises
    FrozenInstanceError — swap the whole object, as test_x_circuit_breaker.py
    does (see CLAUDE.md 2026-07-29)."""
    with patch.object(stripe_api, "settings", SimpleNamespace(STRIPE_SECRET_KEY="sk_test_stub")):
        yield


def _payment(**overrides) -> dict:
    """A one-time PaymentIntent, as a recurring Payment Link never produces.

    Shape differs from a Subscription: amount is top-level, there is no
    items[]/price, and no current_period_end.
    """
    data = {
        "id": "pi_123",
        "status": "succeeded",
        "created": 1788000000,
        "amount": 2000,
        "amount_received": 2000,
        "currency": "usd",
        "customer": {"id": "cus_9", "name": "John Roe", "email": "john@example.com"},
    }
    data.update(overrides)
    return data


def _empty_payments():
    """No one-time payments — for tests that only care about subscriptions."""
    return _stripe_object({"data": []})


def _empty_subscriptions():
    return _stripe_object({"data": []})


@pytest.mark.asyncio
async def test_list_recent_subscriptions_requests_active_and_expands_customer() -> None:
    listing = _stripe_object({"data": [_stripe_object(_subscription())]})

    with patch.object(stripe.Subscription, "list", return_value=listing) as mock_list,          patch.object(stripe.PaymentIntent, "list", return_value=_empty_payments()):
        result = await stripe_api.list_recent_subscriptions(limit=5)

    mock_list.assert_called_once_with(
        status="active", limit=5, expand=["data.customer"]
    )
    assert len(result) == 1
    assert result[0].subscription_id == "sub_123"
    assert result[0].customer_id == "cus_123"
    assert result[0].name == "Jane Doe"
    assert result[0].email == "jane@example.com"
    assert result[0].amount_cents == 3000


@pytest.mark.asyncio
async def test_summary_handles_an_unexpanded_customer_id() -> None:
    """Stripe returns a bare id string when the customer isn't expanded."""
    listing = _stripe_object(
        {"data": [_stripe_object(_subscription(customer="cus_bare"))]}
    )

    with patch.object(stripe.Subscription, "list", return_value=listing),          patch.object(stripe.PaymentIntent, "list", return_value=_empty_payments()):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].customer_id == "cus_bare"
    assert result[0].name is None
    assert result[0].email is None


@pytest.mark.asyncio
async def test_summary_survives_a_customer_with_no_name() -> None:
    """Drives the branch where the Cog has to ask an admin for the name."""
    listing = _stripe_object(
        {"data": [_stripe_object(_subscription(customer={"id": "cus_1", "email": "a@b.c"}))]}
    )

    with patch.object(stripe.Subscription, "list", return_value=listing),          patch.object(stripe.PaymentIntent, "list", return_value=_empty_payments()):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].name is None
    assert result[0].email == "a@b.c"


@pytest.mark.asyncio
async def test_summary_survives_a_subscription_with_no_items() -> None:
    listing = _stripe_object({"data": [_stripe_object(_subscription(items={"data": []}))]})

    with patch.object(stripe.Subscription, "list", return_value=listing),          patch.object(stripe.PaymentIntent, "list", return_value=_empty_payments()):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].amount_cents is None


@pytest.mark.asyncio
async def test_label_is_readable_and_survives_missing_fields() -> None:
    listing = _stripe_object({"data": [_stripe_object(_subscription())]})

    with patch.object(stripe.Subscription, "list", return_value=listing),          patch.object(stripe.PaymentIntent, "list", return_value=_empty_payments()):
        result = await stripe_api.list_recent_subscriptions()

    label = result[0].label()
    assert "Jane Doe" in label
    assert "$30.00" in label


@pytest.mark.asyncio
async def test_get_subscription_retrieves_by_id() -> None:
    with patch.object(
        stripe.Subscription, "retrieve", return_value=_stripe_object(_subscription())
    ) as mock_retrieve:
        result = await stripe_api.get_subscription("sub_123")

    mock_retrieve.assert_called_once_with("sub_123", expand=["customer"])
    assert result.status == "active"


@pytest.mark.asyncio
async def test_missing_key_raises_not_configured() -> None:
    """An unset key must be a clean, typed failure — the Cog turns it into a
    "Stripe isn't configured" message rather than a traceback."""
    with patch.object(stripe_api, "settings", SimpleNamespace(STRIPE_SECRET_KEY="")):
        with pytest.raises(stripe_api.StripeNotConfiguredError):
            await stripe_api.list_recent_subscriptions()
        with pytest.raises(stripe_api.StripeNotConfiguredError):
            await stripe_api.get_subscription("sub_123")


@pytest.mark.asyncio
async def test_stripe_errors_are_wrapped_so_callers_need_no_stripe_import() -> None:
    """Only when BOTH sources fail — a partial failure still returns results."""
    with patch.object(
        stripe.Subscription, "list", side_effect=stripe.error.APIConnectionError("down")
    ), patch.object(
        stripe.PaymentIntent, "list", side_effect=stripe.error.APIConnectionError("down")
    ):
        with pytest.raises(stripe_api.StripeApiError):
            await stripe_api.list_recent_subscriptions()


# --- one-time payments (the Payment Links are not always recurring) -----------


@pytest.mark.asyncio
async def test_a_one_time_payment_is_offered_even_with_no_subscriptions() -> None:
    """The reported bug: a real one-time purchase creates no Subscription, so
    listing only subscriptions reported "no active subscriptions to link"."""
    with patch.object(stripe.Subscription, "list", return_value=_empty_subscriptions()), \
         patch.object(
             stripe.PaymentIntent, "list",
             return_value=_stripe_object({"data": [_stripe_object(_payment())]}),
         ):
        result = await stripe_api.list_recent_subscriptions()

    assert len(result) == 1
    assert result[0].subscription_id == "pi_123"
    assert result[0].kind == stripe_api.KIND_PAYMENT
    assert result[0].is_one_time is True
    assert result[0].amount_cents == 2000
    assert result[0].name == "John Roe"
    assert result[0].current_period_end is None


@pytest.mark.asyncio
async def test_unsuccessful_payments_are_never_offered() -> None:
    """An abandoned or failed checkout must not look confirmable."""
    with patch.object(stripe.Subscription, "list", return_value=_empty_subscriptions()), \
         patch.object(
             stripe.PaymentIntent, "list",
             return_value=_stripe_object({"data": [
                 _stripe_object(_payment(id="pi_bad", status="requires_payment_method")),
                 _stripe_object(_payment(id="pi_good")),
             ]}),
         ):
        result = await stripe_api.list_recent_subscriptions()

    assert [r.subscription_id for r in result] == ["pi_good"]


@pytest.mark.asyncio
async def test_both_kinds_merge_newest_first() -> None:
    with patch.object(
        stripe.Subscription, "list",
        return_value=_stripe_object({"data": [_stripe_object(_subscription(created=1000))]}),
    ), patch.object(
        stripe.PaymentIntent, "list",
        return_value=_stripe_object({"data": [_stripe_object(_payment(created=2000))]}),
    ):
        result = await stripe_api.list_recent_subscriptions()

    assert [r.subscription_id for r in result] == ["pi_123", "sub_123"]
    assert [r.kind for r in result] == [
        stripe_api.KIND_PAYMENT,
        stripe_api.KIND_SUBSCRIPTION,
    ]


@pytest.mark.asyncio
async def test_one_source_failing_still_offers_the_other() -> None:
    """An admin looking at a real purchase must still be able to confirm it."""
    with patch.object(
        stripe.Subscription, "list", side_effect=stripe.error.APIConnectionError("down")
    ), patch.object(
        stripe.PaymentIntent, "list",
        return_value=_stripe_object({"data": [_stripe_object(_payment())]}),
    ):
        result = await stripe_api.list_recent_subscriptions()

    assert [r.subscription_id for r in result] == ["pi_123"]


@pytest.mark.asyncio
async def test_labels_distinguish_the_two_kinds() -> None:
    """They mean different things for how long access lasts, so the admin
    picking one must be able to tell them apart."""
    with patch.object(
        stripe.Subscription, "list",
        return_value=_stripe_object({"data": [_stripe_object(_subscription())]}),
    ), patch.object(
        stripe.PaymentIntent, "list",
        return_value=_stripe_object({"data": [_stripe_object(_payment())]}),
    ):
        result = await stripe_api.list_recent_subscriptions()

    labels = {r.kind: r.label() for r in result}
    assert "one-time" in labels[stripe_api.KIND_PAYMENT]
    assert "monthly" in labels[stripe_api.KIND_SUBSCRIPTION]
    assert "$20.00" in labels[stripe_api.KIND_PAYMENT]


@pytest.mark.asyncio
async def test_a_guest_checkout_payment_still_identifies_the_buyer() -> None:
    """No customer object at all — billing details on the charge are the only
    identifying information, and the admin needs something to match on.

    Uses `latest_charge`, which is what stripe 15.x actually returns; the old
    `charges.data` field was removed from the object, so a fixture using it
    would pass here while the fallback never fired in production.
    """
    payment = _payment(
        customer=None,
        receipt_email=None,
        latest_charge={"billing_details": {"name": "Guest Buyer",
                                           "email": "guest@example.com"}},
    )
    with patch.object(stripe.Subscription, "list", return_value=_empty_subscriptions()), \
         patch.object(
             stripe.PaymentIntent, "list",
             return_value=_stripe_object({"data": [_stripe_object(payment)]}),
         ):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].name == "Guest Buyer"
    assert result[0].email == "guest@example.com"
    assert result[0].customer_id == ""


@pytest.mark.asyncio
async def test_get_subscription_routes_a_payment_id_to_payment_intents() -> None:
    """A pi_ id would 404 against Subscription.retrieve."""
    with patch.object(
        stripe.PaymentIntent, "retrieve", return_value=_stripe_object(_payment())
    ) as mock_pi, patch.object(stripe.Subscription, "retrieve") as mock_sub:
        result = await stripe_api.get_subscription("pi_123")

    mock_pi.assert_called_once_with("pi_123", expand=["customer", "latest_charge"])
    mock_sub.assert_not_called()
    assert result.kind == stripe_api.KIND_PAYMENT


def test_is_one_time_id_splits_on_the_stripe_prefix() -> None:
    assert stripe_api.is_one_time_id("pi_123") is True
    assert stripe_api.is_one_time_id("sub_123") is False


@pytest.mark.asyncio
async def test_legacy_charges_shape_still_read() -> None:
    """Pre-2022 API versions nest the charge under charges.data instead."""
    payment = _payment(
        customer=None,
        receipt_email=None,
        charges={"data": [{"billing_details": {"name": "Old Buyer",
                                               "email": "old@example.com"}}]},
    )
    with patch.object(stripe.Subscription, "list", return_value=_empty_subscriptions()),          patch.object(
             stripe.PaymentIntent, "list",
             return_value=_stripe_object({"data": [_stripe_object(payment)]}),
         ):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].name == "Old Buyer"


@pytest.mark.asyncio
async def test_an_unexpanded_latest_charge_id_is_not_mistaken_for_a_charge() -> None:
    """latest_charge is a bare id string when not expanded; reading fields off
    it would throw rather than fall through to no-name."""
    payment = _payment(customer=None, receipt_email=None, latest_charge="ch_123")
    with patch.object(stripe.Subscription, "list", return_value=_empty_subscriptions()),          patch.object(
             stripe.PaymentIntent, "list",
             return_value=_stripe_object({"data": [_stripe_object(payment)]}),
         ):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].name is None
    # Still offerable — the admin can match it by amount and date.
    assert result[0].subscription_id == "pi_123"


@pytest.mark.asyncio
async def test_payment_list_expands_the_charge_it_reads_from() -> None:
    """The fallback above is dead code unless latest_charge is expanded."""
    with patch.object(stripe.Subscription, "list", return_value=_empty_subscriptions()),          patch.object(
             stripe.PaymentIntent, "list", return_value=_empty_payments()
         ) as mock_list:
        await stripe_api.list_recent_subscriptions(limit=7)

    assert mock_list.call_args.kwargs["expand"] == ["data.customer", "data.latest_charge"]
    assert mock_list.call_args.kwargs["limit"] == 7
