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


@pytest.mark.asyncio
async def test_list_recent_subscriptions_requests_active_and_expands_customer() -> None:
    listing = _stripe_object({"data": [_stripe_object(_subscription())]})

    with patch.object(stripe.Subscription, "list", return_value=listing) as mock_list:
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

    with patch.object(stripe.Subscription, "list", return_value=listing):
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

    with patch.object(stripe.Subscription, "list", return_value=listing):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].name is None
    assert result[0].email == "a@b.c"


@pytest.mark.asyncio
async def test_summary_survives_a_subscription_with_no_items() -> None:
    listing = _stripe_object({"data": [_stripe_object(_subscription(items={"data": []}))]})

    with patch.object(stripe.Subscription, "list", return_value=listing):
        result = await stripe_api.list_recent_subscriptions()

    assert result[0].amount_cents is None


@pytest.mark.asyncio
async def test_label_is_readable_and_survives_missing_fields() -> None:
    listing = _stripe_object({"data": [_stripe_object(_subscription())]})

    with patch.object(stripe.Subscription, "list", return_value=listing):
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
    with patch.object(
        stripe.Subscription, "list", side_effect=stripe.error.APIConnectionError("down")
    ):
        with pytest.raises(stripe_api.StripeApiError):
            await stripe_api.list_recent_subscriptions()
