"""Unit tests for modules.subscriptions.storage (mocked pool, no real DB)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.subscriptions import storage


class _FakePoolAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolAcquireCtx(conn))
    return pool


PERIOD_END = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _kwargs(**overrides):
    base = dict(
        discord_id=4242,
        guild_id=1,
        agreement_id=7,
        stripe_subscription_id="sub_aug",
        stripe_customer_id="cus_1",
        payer_name="Jane Doe",
        email="jane@example.com",
        tier="l1",
        status="active",
        current_period_end=PERIOD_END,
        linked_by=555,
    )
    base.update(overrides)
    return base


@pytest.mark.asyncio
async def test_create_subscriber_inserts_every_field() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1, "stripe_subscription_id": "sub_aug"})

    result = await storage.create_subscriber(_fake_pool(conn), **_kwargs())

    assert result["id"] == 1
    sql, *args = conn.fetchrow.await_args.args
    assert "INSERT INTO subscribers" in sql
    assert args == [4242, 1, 7, "sub_aug", "cus_1", "Jane Doe", "jane@example.com",
                    "l1", "active", PERIOD_END, 555]


@pytest.mark.asyncio
async def test_resubscribing_creates_a_second_row() -> None:
    """One row per subscription period — the table is a history, so a buyer
    subscribing again next month must not overwrite this month."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {"id": 1, "stripe_subscription_id": "sub_aug"},
            {"id": 2, "stripe_subscription_id": "sub_sep"},
        ]
    )
    pool = _fake_pool(conn)

    august = await storage.create_subscriber(pool, **_kwargs(stripe_subscription_id="sub_aug"))
    september = await storage.create_subscriber(pool, **_kwargs(stripe_subscription_id="sub_sep"))

    assert august["id"] != september["id"]
    # Plain INSERT, no upsert — an ON CONFLICT clause here would silently
    # collapse the history this table exists to keep.
    assert "ON CONFLICT" not in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_duplicate_stripe_subscription_raises() -> None:
    """The unique index must surface as an error, not a silent re-attribution
    of one payment to a second Discord user."""
    import asyncpg

    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))

    with pytest.raises(asyncpg.UniqueViolationError):
        await storage.create_subscriber(_fake_pool(conn), **_kwargs())


@pytest.mark.asyncio
async def test_list_for_refresh_skips_terminal_statuses() -> None:
    """A canceled subscription never comes back, so re-polling it forever
    would just burn Stripe API calls."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1, "stripe_subscription_id": "sub_a", "status": "active"}])

    rows = await storage.list_for_refresh(_fake_pool(conn))

    assert len(rows) == 1
    sql, *args = conn.fetch.await_args.args
    assert "NOT (status = ANY($1::text[]))" in sql
    assert "canceled" in args[0]
    assert "incomplete_expired" in args[0]


@pytest.mark.asyncio
async def test_list_active_subscribers_excludes_payment_trouble() -> None:
    """past_due / unpaid are live subscriptions in Stripe but mean payment is
    failing, so they deliberately don't count as active access."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}])

    await storage.list_active_subscribers(_fake_pool(conn), 1)

    _, *args = conn.fetch.await_args.args
    assert args == [1, ["active", "trialing"]]


@pytest.mark.asyncio
async def test_update_subscriber_status() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1, "status": "canceled"})

    result = await storage.update_subscriber_status(
        _fake_pool(conn), "sub_aug", status="canceled", current_period_end=None
    )

    assert result["status"] == "canceled"
    sql, *args = conn.fetchrow.await_args.args
    assert "UPDATE subscribers" in sql
    assert args == ["sub_aug", "canceled", None]


@pytest.mark.asyncio
async def test_update_subscriber_status_returns_none_when_row_is_gone() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    result = await storage.update_subscriber_status(
        _fake_pool(conn), "sub_missing", status="active", current_period_end=None
    )

    assert result is None


@pytest.mark.asyncio
async def test_list_subscribers_for_discord_id_is_newest_first() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 2}, {"id": 1}])

    rows = await storage.list_subscribers_for_discord_id(_fake_pool(conn), 4242)

    assert [r["id"] for r in rows] == [2, 1]
    assert "ORDER BY created_at DESC" in conn.fetch.await_args.args[0]
