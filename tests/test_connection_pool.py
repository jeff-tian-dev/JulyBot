"""Pool deadlines that stop a dead Supabase socket from wedging every job."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import database.connection as connection


@pytest.fixture
def fresh_pool():
    previous_pool = connection._pool
    original_acquire = connection.asyncpg.Pool.acquire
    connection._pool = None
    yield
    connection._pool = previous_pool
    connection.asyncpg.Pool.acquire = original_acquire


def test_acquire_timeout_fills_in_default_and_keeps_explicit(fresh_pool):
    calls: list[float | None] = []

    def raw(self, *, timeout=None):
        calls.append(timeout)
        return "ctx"

    connection.asyncpg.Pool.acquire = raw
    connection._limit_acquire()
    wrapped = connection.asyncpg.Pool.acquire

    assert wrapped(object()) == "ctx"
    assert wrapped(object(), timeout=5) == "ctx"
    assert calls == [connection.ACQUIRE_TIMEOUT_SECONDS, 5]

    connection._limit_acquire()
    assert connection.asyncpg.Pool.acquire is wrapped


@pytest.mark.asyncio
async def test_pool_leaves_statement_timeout_unset(fresh_pool):
    fake_pool = MagicMock()
    create = AsyncMock(return_value=fake_pool)

    with patch.object(connection.asyncpg, "create_pool", create):
        await connection.get_pool()

    create_kwargs = create.await_args.kwargs
    assert "command_timeout" not in create_kwargs
    assert create_kwargs["max_size"] == connection.POOL_MAX_SIZE
    assert create_kwargs["statement_cache_size"] == 0
    assert create_kwargs["connect"] is connection._connect_with_retry
    assert getattr(connection.asyncpg.Pool.acquire, "_julybot_limited", False)


@pytest.mark.asyncio
async def test_connect_retries_once_on_pooler_failure(monkeypatch):
    monkeypatch.setattr(connection, "CONNECT_RETRY_DELAY_SECONDS", 0)
    conn = object()
    err = connection.asyncpg.exceptions.ConnectionFailureError(
        "(EAUTHTIMEOUT) timeout while waiting for message"
    )
    connect = AsyncMock(side_effect=[err, conn])

    with patch.object(connection.asyncpg, "connect", connect):
        assert await connection._connect_with_retry("dsn", loop=None) is conn

    assert connect.await_count == 2
    assert connect.await_args.args == ("dsn",)


@pytest.mark.asyncio
async def test_connect_gives_up_after_last_attempt(monkeypatch):
    monkeypatch.setattr(connection, "CONNECT_RETRY_DELAY_SECONDS", 0)
    connect = AsyncMock(side_effect=OSError("unreachable"))

    with patch.object(connection.asyncpg, "connect", connect):
        with pytest.raises(OSError):
            await connection._connect_with_retry("dsn")

    assert connect.await_count == connection.CONNECT_ATTEMPTS


class _FakeAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_terminate_stuck_sessions_targets_only_wedged_sessions():
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"pid": 11, "terminated": True, "query": "SELECT 1"},
            {"pid": 12, "terminated": False, "query": "SELECT 2"},
        ]
    )
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeAcquireCtx(conn))

    assert await connection.terminate_stuck_sessions(pool) == [11]

    sql, minutes = conn.fetch.await_args.args
    assert minutes == connection.STUCK_SESSION_MINUTES
    # Only this role's own sessions, stuck mid-query, never the checking session.
    for clause in (
        "usename = current_user",
        "pid <> pg_backend_pid()",
        "state = 'active'",
        "wait_event = 'ClientRead'",
    ):
        assert clause in sql
