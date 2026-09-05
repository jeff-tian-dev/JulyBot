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
async def test_list_for_refresh_skips_one_time_payments() -> None:
    """A successful payment is `succeeded` forever — there is no renewal or
    cancellation to observe, so re-polling one only burns Stripe API calls.
    Recurring subscriptions are `sub_…`; one-time payments are `pi_…`."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await storage.list_for_refresh(_fake_pool(conn))

    sql = conn.fetch.await_args.args[0]
    assert "NOT LIKE 'pi" in sql


@pytest.mark.asyncio
async def test_list_active_subscribers_excludes_payment_trouble() -> None:
    """past_due / unpaid are live subscriptions in Stripe but mean payment is
    failing, so they deliberately don't count as active access.

    "succeeded" IS included — that's a one-time PaymentIntent, which is what
    the Payment Links currently produce; without it no one-time purchase could
    ever count as active."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}])

    await storage.list_active_subscribers(_fake_pool(conn), 1)

    sql, *args = conn.fetch.await_args.args
    assert args == [1, ["active", "trialing", "succeeded"]]
    assert "past_due" not in sql
    # Archived rows are a closed-out month, not current access. This is what
    # bounds a one-time purchase, whose status never stops being "succeeded".
    assert "archived_at IS NULL" in sql


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


# --- relink (fixing a mislinked payment) --------------------------------------


@pytest.mark.asyncio
async def test_relink_rewrites_stripe_fields_but_not_the_buyer() -> None:
    """The Discord buyer was already correct — /subscribe names them. Only the
    payment was picked wrongly, so only Stripe-derived columns change."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 3, "stripe_subscription_id": "pi_right"})

    await storage.relink_subscriber(
        _fake_pool(conn),
        3,
        stripe_subscription_id="pi_right",
        stripe_customer_id="cus_9",
        payer_name="Right Person",
        email="right@example.com",
        status="succeeded",
        current_period_end=None,
        relinked_by=555,
    )

    sql, *args = conn.fetchrow.await_args.args
    assert "UPDATE subscribers" in sql
    assert "discord_id" not in sql
    assert "relinked_by = $8" in sql
    assert "relinked_at = NOW()" in sql
    assert args[0] == 3
    assert args[1] == "pi_right"


@pytest.mark.asyncio
async def test_relink_keeps_the_original_linker() -> None:
    """linked_by is the audit trail of who made the first call; a correction
    adds to that history rather than erasing it."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 3})

    await storage.relink_subscriber(
        _fake_pool(conn), 3,
        stripe_subscription_id="pi_x", stripe_customer_id="c", payer_name=None,
        email=None, status="succeeded", current_period_end=None, relinked_by=999,
    )

    sql = conn.fetchrow.await_args.args[0]
    assert "linked_by = " not in sql.replace("relinked_by = ", "")


@pytest.mark.asyncio
async def test_relink_returns_none_when_the_row_is_gone() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    result = await storage.relink_subscriber(
        _fake_pool(conn), 999,
        stripe_subscription_id="pi_x", stripe_customer_id="c", payer_name=None,
        email=None, status="succeeded", current_period_end=None, relinked_by=555,
    )

    assert result is None


@pytest.mark.asyncio
async def test_relink_surfaces_a_duplicate_rather_than_stealing_the_payment() -> None:
    import asyncpg

    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))

    with pytest.raises(asyncpg.UniqueViolationError):
        await storage.relink_subscriber(
            _fake_pool(conn), 3,
            stripe_subscription_id="pi_taken", stripe_customer_id="c", payer_name=None,
            email=None, status="succeeded", current_period_end=None, relinked_by=555,
        )


@pytest.mark.asyncio
async def test_list_recent_subscribers_is_scoped_to_the_guild() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 2}, {"id": 1}])

    await storage.list_recent_subscribers(_fake_pool(conn), 1, limit=15)

    sql, *args = conn.fetch.await_args.args
    assert "WHERE guild_id = $1" in sql
    assert "ORDER BY created_at DESC" in sql
    assert args == [1, 15]


@pytest.mark.asyncio
async def test_linked_stripe_ids_returns_a_set() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"stripe_subscription_id": "pi_a"}, {"stripe_subscription_id": "sub_b"},
    ])

    assert await storage.linked_stripe_ids(_fake_pool(conn)) == {"pi_a", "sub_b"}


# --- archiving a month --------------------------------------------------------


@pytest.mark.asyncio
async def test_archive_marks_rows_without_deleting_them() -> None:
    """These rows are the purchase log and dispute evidence — closing out a
    month must never remove one."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    cutoff = datetime(2026, 9, 1, tzinfo=timezone.utc)

    archived = await storage.archive_subscribers(
        _fake_pool(conn), 1, before=cutoff, archived_by=555
    )

    assert len(archived) == 2
    sql, *args = conn.fetch.await_args.args
    assert "UPDATE subscribers" in sql
    assert "DELETE" not in sql.upper()
    assert "archived_at = NOW()" in sql
    assert args == [1, cutoff, 555]


@pytest.mark.asyncio
async def test_archive_skips_already_archived_rows() -> None:
    """Re-running a close-out must be a no-op, not a restamp that loses when
    the archive actually happened."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await storage.archive_subscribers(
        _fake_pool(conn), 1, before=datetime(2026, 9, 1, tzinfo=timezone.utc), archived_by=555
    )

    assert "archived_at IS NULL" in conn.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_archive_is_scoped_to_one_guild_and_cutoff() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await storage.archive_subscribers(
        _fake_pool(conn), 42, before=datetime(2026, 9, 1, tzinfo=timezone.utc), archived_by=1
    )

    sql = conn.fetch.await_args.args[0]
    assert "guild_id = $1" in sql
    assert "created_at < $2" in sql


@pytest.mark.asyncio
async def test_unarchive_clears_both_columns() -> None:
    """A close-out run too early must be fully undoable, not leave a stale
    archived_by behind."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 3, "archived_at": None})

    await storage.unarchive_subscriber(_fake_pool(conn), 3)

    sql = conn.fetchrow.await_args.args[0]
    assert "archived_at = NULL" in sql
    assert "archived_by = NULL" in sql


@pytest.mark.asyncio
async def test_refresh_skips_archived_rows() -> None:
    """A closed-out month's status no longer drives anything, so re-polling it
    would just burn Stripe calls."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await storage.list_for_refresh(_fake_pool(conn))

    assert "archived_at IS NULL" in conn.fetch.await_args.args[0]
