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


@pytest.mark.asyncio
async def test_upsert_from_checkout_insert() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={"id": 1, "stripe_checkout_session_id": "cs_123", "status": "paid"}
    )
    result = await storage.upsert_from_checkout(
        _fake_pool(conn),
        stripe_customer_id="cus_123",
        stripe_checkout_session_id="cs_123",
        tier="l1",
        email="buyer@example.com",
        discord_username_hint="buyerdiscord",
        status="paid",
    )
    assert result["stripe_checkout_session_id"] == "cs_123"
    conn.fetchrow.assert_awaited_once()
    args = conn.fetchrow.await_args.args
    assert "ON CONFLICT (stripe_checkout_session_id) DO UPDATE" in args[0]


@pytest.mark.asyncio
async def test_upsert_from_checkout_replay_is_idempotent() -> None:
    """A redelivered checkout.session.completed webhook re-applies the same
    state via the same ON CONFLICT upsert rather than erroring."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={"id": 1, "stripe_checkout_session_id": "cs_123", "status": "paid"}
    )
    pool = _fake_pool(conn)
    kwargs = dict(
        stripe_customer_id="cus_123",
        stripe_checkout_session_id="cs_123",
        tier="l1",
        email="buyer@example.com",
        discord_username_hint=None,
        status="paid",
    )
    first = await storage.upsert_from_checkout(pool, **kwargs)
    second = await storage.upsert_from_checkout(pool, **kwargs)
    assert first == second
    assert conn.fetchrow.await_count == 2


@pytest.mark.asyncio
async def test_upsert_from_checkout_different_months_are_separate_rows() -> None:
    """Different checkout sessions (e.g. the same buyer purchasing again next
    month) upsert independently — one row per purchase, not per customer."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {"id": 1, "stripe_checkout_session_id": "cs_august"},
            {"id": 2, "stripe_checkout_session_id": "cs_september"},
        ]
    )
    pool = _fake_pool(conn)
    august = await storage.upsert_from_checkout(
        pool,
        stripe_customer_id="cus_123",
        stripe_checkout_session_id="cs_august",
        tier="l1",
        email="buyer@example.com",
        discord_username_hint=None,
        status="paid",
    )
    september = await storage.upsert_from_checkout(
        pool,
        stripe_customer_id="cus_123",
        stripe_checkout_session_id="cs_september",
        tier="l1",
        email="buyer@example.com",
        discord_username_hint=None,
        status="paid",
    )
    assert august["id"] != september["id"]


@pytest.mark.asyncio
async def test_get_by_customer_id() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    result = await storage.get_by_customer_id(_fake_pool(conn), "cus_123")
    assert len(result) == 2
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_by_month_passes_half_open_range() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)

    result = await storage.get_by_month(_fake_pool(conn), start, end)

    assert len(result) == 2
    conn.fetch.assert_awaited_once()
    args = conn.fetch.await_args.args
    assert "created_at >= $1 AND created_at < $2" in args[0]
    assert args[1] == start
    assert args[2] == end
