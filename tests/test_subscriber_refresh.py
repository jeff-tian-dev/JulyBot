"""Unit tests for the scheduled subscriber refresh (mocked storage + Stripe)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.subscriptions import refresh
from modules.subscriptions.stripe_api import (
    StripeApiError,
    StripeNotConfiguredError,
    SubscriptionSummary,
)


def _summary(subscription_id: str = "sub_a", status: str = "active") -> SubscriptionSummary:
    return SubscriptionSummary(
        subscription_id=subscription_id,
        customer_id="cus_1",
        name="Jane Doe",
        email="jane@example.com",
        status=status,
        amount_cents=3000,
        currency="usd",
        created=None,
        current_period_end=datetime(2026, 9, 30, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_status_change_is_written_back() -> None:
    rows = [{"id": 1, "stripe_subscription_id": "sub_a", "status": "active"}]

    with patch.object(refresh.storage, "list_for_refresh", new=AsyncMock(return_value=rows)), \
         patch.object(
             refresh, "get_subscription", new=AsyncMock(return_value=_summary(status="canceled"))
         ), \
         patch.object(refresh.storage, "update_subscriber_status", new=AsyncMock()) as update:
        summary = await refresh.refresh_subscribers(MagicMock())

    assert summary == {"checked": 1, "changed": 1, "errors": 0}
    assert update.await_args.kwargs["status"] == "canceled"


@pytest.mark.asyncio
async def test_unchanged_status_is_not_rewritten() -> None:
    rows = [{"id": 1, "stripe_subscription_id": "sub_a", "status": "active"}]

    with patch.object(refresh.storage, "list_for_refresh", new=AsyncMock(return_value=rows)), \
         patch.object(refresh, "get_subscription", new=AsyncMock(return_value=_summary())), \
         patch.object(refresh.storage, "update_subscriber_status", new=AsyncMock()) as update:
        summary = await refresh.refresh_subscribers(MagicMock())

    update.assert_not_awaited()
    assert summary["changed"] == 0


@pytest.mark.asyncio
async def test_one_bad_subscription_does_not_abort_the_rest() -> None:
    """A deleted or malformed id must not stop everyone else being refreshed."""
    rows = [
        {"id": 1, "stripe_subscription_id": "sub_bad", "status": "active"},
        {"id": 2, "stripe_subscription_id": "sub_good", "status": "active"},
    ]

    async def _fetch(subscription_id):
        if subscription_id == "sub_bad":
            raise StripeApiError("no such subscription")
        return _summary(subscription_id, status="canceled")

    with patch.object(refresh.storage, "list_for_refresh", new=AsyncMock(return_value=rows)), \
         patch.object(refresh, "get_subscription", new=AsyncMock(side_effect=_fetch)), \
         patch.object(refresh.storage, "update_subscriber_status", new=AsyncMock()) as update:
        summary = await refresh.refresh_subscribers(MagicMock())

    assert summary == {"checked": 1, "changed": 1, "errors": 1}
    assert update.await_args.args[1] == "sub_good"


@pytest.mark.asyncio
async def test_missing_key_stops_the_run_without_raising() -> None:
    """If the key is unset every lookup would fail identically, so bail out
    rather than logging the same error once per subscriber."""
    rows = [
        {"id": 1, "stripe_subscription_id": "sub_a", "status": "active"},
        {"id": 2, "stripe_subscription_id": "sub_b", "status": "active"},
    ]

    with patch.object(refresh.storage, "list_for_refresh", new=AsyncMock(return_value=rows)), \
         patch.object(
             refresh, "get_subscription", new=AsyncMock(side_effect=StripeNotConfiguredError())
         ) as fetch:
        summary = await refresh.refresh_subscribers(MagicMock())

    assert fetch.await_count == 1
    assert summary["changed"] == 0


@pytest.mark.asyncio
async def test_a_storage_failure_is_reported_not_raised() -> None:
    """A scheduled job must never raise into APScheduler."""
    with patch.object(
        refresh.storage, "list_for_refresh", new=AsyncMock(side_effect=RuntimeError("db down"))
    ):
        summary = await refresh.refresh_subscribers(MagicMock())

    assert summary["errors"] == 1
