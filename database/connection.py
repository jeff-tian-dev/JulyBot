"""Asyncpg connection pool singleton."""
from __future__ import annotations

import logging

import asyncpg

from config.settings import settings

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 1
POOL_MAX_SIZE = 3

_pool: asyncpg.Pool | None = None


def _clean_dsn(dsn: str) -> str:
    """Strip statement_cache_size from DSN — asyncpg requires it as a kwarg, not a URL param."""
    import urllib.parse
    parsed = urllib.parse.urlparse(dsn)
    qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    qs.pop("statement_cache_size", None)
    clean_query = urllib.parse.urlencode({k: v[0] for k, v in qs.items()})
    return urllib.parse.urlunparse(parsed._replace(query=clean_query))


async def get_pool() -> asyncpg.Pool:
    """Return the global connection pool, creating it if needed."""
    global _pool
    if _pool is None:
        logger.info("Creating asyncpg connection pool (min=%d, max=%d)", POOL_MIN_SIZE, POOL_MAX_SIZE)
        _pool = await asyncpg.create_pool(
            dsn=_clean_dsn(settings.DATABASE_URL),
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
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
