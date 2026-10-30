"""Asyncpg connection pool singleton."""
from __future__ import annotations

import asyncio
import logging

import asyncpg

from config.settings import settings

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 8
# Checkout cap. Also the budget Pool.release() uses to give up on a socket.
# acquire() defaults to no timeout, and release() is asyncio.shield'ed, so a
# cancelled job cannot interrupt it. Without this budget a dead connection
# stays checked out forever, the pool fills, and every later checkout waits
# forever too (2026-10-26). This is not a per-statement cap: startup DDL has
# to be allowed to wait out a lock, and a short command_timeout crash-looped
# the process on MIGRATE_USERS_MULTI_ACCOUNT.
ACQUIRE_TIMEOUT_SECONDS = 30
# Supabase's pooler sometimes fails a new connection with
# "(EAUTHTIMEOUT) timeout while waiting for message" (2026-10-28); an
# immediate second attempt usually works. Retries stay inside the acquire
# budget above.
CONNECT_ATTEMPTS = 2
CONNECT_RETRY_DELAY_SECONDS = 2
# A connection that dies mid-query can leave its server session behind on
# Supabase's side, 'active' and waiting on ClientRead forever (2026-10-29:
# three sessions stuck 6-30h). No Postgres timeout fires in that state; each
# one holds a pooler slot and its locks, which starved new connections and
# blocked a startup ALTER TABLE. Sessions stuck this long get terminated.
STUCK_SESSION_MINUTES = 10

_pool: asyncpg.Pool | None = None


def _clean_dsn(dsn: str) -> str:
    """Strip statement_cache_size from DSN — asyncpg requires it as a kwarg, not a URL param."""
    import urllib.parse
    parsed = urllib.parse.urlparse(dsn)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("statement_cache_size", None)
    clean_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


def _limit_acquire() -> None:
    """Apply ACQUIRE_TIMEOUT_SECONDS to every checkout that doesn't set its own.

    Pool uses __slots__, so the method has to be replaced on the class.
    """
    current = asyncpg.Pool.acquire
    if getattr(current, "_julybot_limited", False):
        return

    def acquire(self, *, timeout: float | None = None):
        if timeout is None:
            timeout = ACQUIRE_TIMEOUT_SECONDS
        return current(self, timeout=timeout)

    acquire._julybot_limited = True  # type: ignore[attr-defined]
    asyncpg.Pool.acquire = acquire  # type: ignore[method-assign]


async def _connect_with_retry(*args, **kwargs) -> asyncpg.Connection:
    """asyncpg.connect, retried on connection failures. Used as the pool's connect hook."""
    for attempt in range(1, CONNECT_ATTEMPTS):
        try:
            return await asyncpg.connect(*args, **kwargs)
        except (asyncpg.PostgresConnectionError, OSError) as e:
            logger.warning(
                "DB connect failed (attempt %d/%d): %s; retrying", attempt, CONNECT_ATTEMPTS, e
            )
            await asyncio.sleep(CONNECT_RETRY_DELAY_SECONDS)
    return await asyncpg.connect(*args, **kwargs)


async def terminate_stuck_sessions(pool: asyncpg.Pool) -> list[int]:
    """End this role's sessions stuck mid-query for STUCK_SESSION_MINUTES. Returns their pids."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT pid, pg_terminate_backend(pid) AS terminated, left(query, 80) AS query
            FROM pg_stat_activity
            WHERE usename = current_user
              AND pid <> pg_backend_pid()
              AND state = 'active'
              AND wait_event = 'ClientRead'
              AND state_change < now() - make_interval(mins => $1)
            """,
            STUCK_SESSION_MINUTES,
        )
    for row in rows:
        logger.warning("Terminated stuck DB session pid=%s: %s", row["pid"], row["query"])
    return [row["pid"] for row in rows if row["terminated"]]


async def get_pool() -> asyncpg.Pool:
    """Return the global connection pool, creating it if needed."""
    global _pool
    if _pool is None:
        logger.info("Creating asyncpg connection pool (min=%d, max=%d)", POOL_MIN_SIZE, POOL_MAX_SIZE)
        _limit_acquire()
        _pool = await asyncpg.create_pool(
            dsn=_clean_dsn(settings.DATABASE_URL),
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
            connect=_connect_with_retry,
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    """Close the global connection pool on shutdown."""
    global _pool
    if _pool is not None:
        logger.info("Closing asyncpg connection pool")
        await _pool.close()
        _pool = None
