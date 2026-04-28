"""Asyncpg connection pool singleton."""
from __future__ import annotations

import logging

import asyncpg

from config.settings import settings

logger = logging.getLogger(__name__)

POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 10

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    """Return the global connection pool, creating it if needed."""
    global _pool
    if _pool is None:
        logger.info("Creating asyncpg connection pool (min=%d, max=%d)", POOL_MIN_SIZE, POOL_MAX_SIZE)
        _pool = await asyncpg.create_pool(
            dsn=settings.DATABASE_URL,
            min_size=POOL_MIN_SIZE,
            max_size=POOL_MAX_SIZE,
        )
    return _pool


async def close_pool() -> None:
    """Close the global connection pool on shutdown."""
    global _pool
    if _pool is not None:
        logger.info("Closing asyncpg connection pool")
        await _pool.close()
        _pool = None
