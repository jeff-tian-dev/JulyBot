"""Standalone DB initializer.

Run with `python scripts/init_db.py` after configuring `.env`.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

# Allow running as a script from repo root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg  # noqa: E402

from config.settings import settings  # noqa: E402
from database.models import create_tables  # noqa: E402

logger = logging.getLogger("init_db")


async def _seed_watched_channels(pool: asyncpg.Pool) -> int:
    """Insert configured YouTube channel IDs (idempotent)."""
    if not settings.YOUTUBE_CHANNEL_IDS:
        return 0
    inserted = 0
    async with pool.acquire() as conn:
        for channel_id in settings.YOUTUBE_CHANNEL_IDS:
            result = await conn.execute(
                """
                INSERT INTO watched_channels (channel_id)
                VALUES ($1)
                ON CONFLICT (channel_id) DO NOTHING;
                """,
                channel_id,
            )
            if result.endswith(" 1"):
                inserted += 1
    return inserted


async def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"Connecting to {settings.DATABASE_URL.split('@')[-1]} ...")
    try:
        pool = await asyncpg.create_pool(dsn=settings.DATABASE_URL, min_size=1, max_size=2)
    except Exception as e:
        print(f"FAILED to connect: {e}")
        return 1

    try:
        await create_tables(pool)
        seeded = await _seed_watched_channels(pool)
        print(f"Tables ready. Seeded {seeded} watched channel(s).")
        return 0
    except Exception as e:
        print(f"FAILED during init: {e}")
        return 1
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
