"""Unit tests for modules.subscriptions.stripe_api (mocked SDK, no real calls)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _payment(**overrides) -> dict:
    """A one-time PaymentIntent.

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


def _checkout(**overrides) -> dict:
    """A completed Checkout Session from a one-time Payment Link, unexpanded."""
    data = {
        "id": "cs_1",
        "mode": "payment",
        "status": "complete",
        "payment_status": "paid",
        "payment_intent": "pi_1",
        "subscription": None,
        "customer": None,  # guest checkout
        "customer_details": {"name": "Guest Buyer", "email": "guest@example.com"},
        "amount_total": 2000,
        "currency": "usd",
        "created": 1788000000,
        "payment_link": "plink_sub",
    }
    data.update(overrides)
    return data


def _listing(*items) -> StripeObject:
    return _stripe_object({"data": [_stripe_object(i) for i in items]})


@pytest.fixture(autouse=True)
def _configured_key():
    """Settings is a frozen dataclass, so per-attribute patching raises
    FrozenInstanceError — swap the whole object, as test_x_circuit_breaker.py
    does (see CLAUDE.md 2026-07-29)."""
    with patch.object(stripe_api, "settings", SimpleNamespace(STRIPE_SECRET_KEY="sk_test_stub")):
        yield


# --- get_subscription (the refresh job) ------------------------------------------


@pytest.mark.asyncio
async def test_get_subscription_retrieves_by_id() -> None:
    with patch.object(
        stripe.Subscription, "retrieve", return_value=_stripe_object(_subscription())
    ) as mock_retrieve:
        result = await stripe_api.get_subscription("sub_123")

    mock_retrieve.assert_called_once_with("sub_123", expand=["customer"])
    assert result.status == "active"
    assert result.name == "Jane Doe"
    assert result.amount_cents == 3000


@pytest.mark.asyncio
async def test_summary_handles_an_unexpanded_customer_id() -> None:
    """Stripe returns a bare id string when the customer isn't expanded."""
    with patch.object(
        stripe.Subscription, "retrieve",
        return_value=_stripe_object(_subscription(customer="cus_bare")),
    ):
        result = await stripe_api.get_subscription("sub_123")

    assert result.customer_id == "cus_bare"
    assert result.name is None
    assert result.email is None


@pytest.mark.asyncio
async def test_summary_survives_a_subscription_with_no_items() -> None:
    with patch.object(
        stripe.Subscription, "retrieve",
        return_value=_stripe_object(_subscription(items={"data": []})),
    ):
        result = await stripe_api.get_subscription("sub_123")

    assert result.amount_cents is None


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


@pytest.mark.asyncio
async def test_a_guest_payment_still_identifies_the_buyer() -> None:
    """No customer object — billing details on `latest_charge` (what stripe 15.x
    returns; `charges.data` was removed) are the only identifying information."""
    payment = _payment(
        customer=None,
        receipt_email=None,
        latest_charge={"billing_details": {"name": "Guest Buyer", "email": "guest@example.com"}},
    )
    with patch.object(stripe.PaymentIntent, "retrieve", return_value=_stripe_object(payment)):
        result = await stripe_api.get_subscription("pi_123")

    assert result.name == "Guest Buyer"
    assert result.email == "guest@example.com"
    assert result.customer_id == ""


@pytest.mark.asyncio
async def test_legacy_charges_shape_still_read() -> None:
    """Pre-2022 API versions nest the charge under charges.data instead."""
    payment = _payment(
        customer=None,
        receipt_email=None,
        charges={"data": [{"billing_details": {"name": "Old Buyer", "email": "old@example.com"}}]},
    )
    with patch.object(stripe.PaymentIntent, "retrieve", return_value=_stripe_object(payment)):
        result = await stripe_api.get_subscription("pi_123")

    assert result.name == "Old Buyer"


@pytest.mark.asyncio
async def test_an_unexpanded_latest_charge_id_is_not_mistaken_for_a_charge() -> None:
    payment = _payment(customer=None, receipt_email=None, latest_charge="ch_123")
    with patch.object(stripe.PaymentIntent, "retrieve", return_value=_stripe_object(payment)):
        result = await stripe_api.get_subscription("pi_123")

    assert result.name is None


def test_is_one_time_id_splits_on_the_stripe_prefix() -> None:
    assert stripe_api.is_one_time_id("pi_123") is True
    assert stripe_api.is_one_time_id("sub_123") is False


@pytest.mark.asyncio
async def test_missing_key_raises_not_configured() -> None:
    """An unset key must be a clean, typed failure — the Cog turns it into a
    "Stripe isn't configured" message rather than a traceback."""
    with patch.object(stripe_api, "settings", SimpleNamespace(STRIPE_SECRET_KEY="")):
        assert stripe_api.is_configured() is False
        with pytest.raises(stripe_api.StripeNotConfiguredError):
            await stripe_api.list_checkouts(["plink_1"])
        with pytest.raises(stripe_api.StripeNotConfiguredError):
            await stripe_api.list_payment_links()
        with pytest.raises(stripe_api.StripeNotConfiguredError):
            await stripe_api.get_subscription("sub_123")


# --- checkouts per Payment Link (Confirm's dropdown) -------------------------------


@pytest.mark.asyncio
async def test_checkouts_are_listed_only_for_the_given_links() -> None:
    """The whole point: a server's dropdown is built from its own links only."""
    with patch.object(
        stripe.checkout.Session, "list", return_value=_listing(_checkout())
    ) as mock_list:
        result = await stripe_api.list_checkouts(["plink_sub"], limit=5)

    mock_list.assert_called_once_with(payment_link="plink_sub", status="complete", limit=5)
    assert len(result) == 1
    summary = result[0]
    assert summary.subscription_id == "pi_1"  # stored like before, so the unique index holds
    assert summary.kind == stripe_api.KIND_PAYMENT
    assert summary.status == stripe_api.PAYMENT_SUCCEEDED
    assert summary.name == "Guest Buyer" and summary.email == "guest@example.com"
    assert summary.customer_id == ""
    assert summary.amount_cents == 2000
    assert summary.payment_link_id == "plink_sub"
    assert "one-time" in summary.label() and "$20.00" in summary.label()


@pytest.mark.asyncio
async def test_unpaid_checkouts_are_never_offered() -> None:
    with patch.object(
        stripe.checkout.Session, "list",
        return_value=_listing(
            _checkout(id="cs_unpaid", payment_status="unpaid", payment_intent="pi_bad"),
            _checkout(id="cs_free", payment_status="no_payment_required", payment_intent=None),
            _checkout(),
        ),
    ):
        result = await stripe_api.list_checkouts(["plink_sub"])

    assert [r.subscription_id for r in result] == ["pi_1"]


@pytest.mark.asyncio
async def test_a_recurring_checkout_stores_its_subscription_id() -> None:
    session = _checkout(mode="subscription", subscription="sub_9", payment_intent=None)
    with patch.object(stripe.checkout.Session, "list", return_value=_listing(session)):
        result = await stripe_api.list_checkouts(["plink_sub"])

    assert result[0].subscription_id == "sub_9"
    assert result[0].kind == stripe_api.KIND_SUBSCRIPTION
    assert "monthly" in result[0].label()


@pytest.mark.asyncio
async def test_several_links_merge_newest_first() -> None:
    by_link = {
        "plink_a": _listing(_checkout(payment_intent="pi_old", created=1000, payment_link="plink_a")),
        "plink_b": _listing(_checkout(payment_intent="pi_new", created=2000, payment_link="plink_b")),
    }
    with patch.object(
        stripe.checkout.Session, "list", side_effect=lambda **kw: by_link[kw["payment_link"]]
    ):
        result = await stripe_api.list_checkouts(["plink_a", "plink_b"])

    assert [r.subscription_id for r in result] == ["pi_new", "pi_old"]


@pytest.mark.asyncio
async def test_one_link_failing_still_offers_the_others() -> None:
    """An admin looking at a real purchase must still be able to confirm it."""

    def listing(**kw):
        if kw["payment_link"] == "plink_bad":
            raise stripe.error.APIConnectionError("down")
        return _listing(_checkout())

    with patch.object(stripe.checkout.Session, "list", side_effect=listing):
        result = await stripe_api.list_checkouts(["plink_bad", "plink_sub"])

    assert [r.subscription_id for r in result] == ["pi_1"]


@pytest.mark.asyncio
async def test_every_link_failing_is_wrapped_so_callers_need_no_stripe_import() -> None:
    with patch.object(
        stripe.checkout.Session, "list", side_effect=stripe.error.APIConnectionError("down")
    ):
        with pytest.raises(stripe_api.StripeApiError):
            await stripe_api.list_checkouts(["plink_a"])


@pytest.mark.asyncio
async def test_no_links_means_no_stripe_call() -> None:
    with patch.object(stripe.checkout.Session, "list") as mock_list:
        assert await stripe_api.list_checkouts([]) == []
    mock_list.assert_not_called()


# --- Payment Link index ------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_payment_links_maps_url_to_id_across_pages() -> None:
    page = MagicMock()
    page.auto_paging_iter.return_value = iter([
        _stripe_object({"id": "plink_1", "url": "https://buy.stripe.com/aaa"}),
        _stripe_object({"id": "plink_2", "url": "https://buy.stripe.com/bbb"}),
    ])
    with patch.object(stripe.PaymentLink, "list", return_value=page) as mock_list:
        links = await stripe_api.list_payment_links()

    mock_list.assert_called_once_with(limit=100)
    assert links == {
        "https://buy.stripe.com/aaa": "plink_1",
        "https://buy.stripe.com/bbb": "plink_2",
    }


@pytest.mark.asyncio
async def test_list_payment_links_wraps_stripe_errors() -> None:
    with patch.object(
        stripe.PaymentLink, "list", side_effect=stripe.error.AuthenticationError("bad key")
    ):
        with pytest.raises(stripe_api.StripeApiError):
            await stripe_api.list_payment_links()
