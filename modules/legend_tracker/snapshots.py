"""Daily legend snapshots: store, retrieve, and diff."""
from __future__ import annotations

import datetime
import logging

import asyncpg

logger = logging.getLogger(__name__)


async def save_snapshot(pool: asyncpg.Pool, stats: dict) -> bool:
    """Insert today's snapshot for a player. Returns True if a new row was inserted."""
    today = datetime.date.today()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO legend_snapshots (
                coc_tag, snapshot_date, trophies,
                attacks_done, defenses_done, attack_wins, defense_wins
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (coc_tag, snapshot_date) DO NOTHING;
            """,
            stats["coc_tag"],
            today,
            stats.get("trophies"),
            stats.get("attacks_done"),
            stats.get("defenses_done"),
            stats.get("attack_wins"),
            stats.get("defense_wins"),
        )
    inserted = result.endswith(" 1")
    if not inserted:
        logger.debug("Snapshot for %s on %s already exists", stats["coc_tag"], today)
    return inserted


async def get_snapshot(
    pool: asyncpg.Pool,
    coc_tag: str,
    date: datetime.date,
) -> dict | None:
    """Return a single snapshot for a player on a given date."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT coc_tag, snapshot_date, trophies, attacks_done,
                   defenses_done, attack_wins, defense_wins, recorded_at
            FROM legend_snapshots
            WHERE coc_tag = $1 AND snapshot_date = $2;
            """,
            coc_tag,
            date,
        )
    return dict(row) if row else None


async def get_recent_snapshots(
    pool: asyncpg.Pool,
    coc_tag: str,
    days: int = 7,
) -> list[dict]:
    """Return the last N days of snapshots for a player, newest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT coc_tag, snapshot_date, trophies, attacks_done,
                   defenses_done, attack_wins, defense_wins, recorded_at
            FROM legend_snapshots
            WHERE coc_tag = $1
            ORDER BY snapshot_date DESC
            LIMIT $2;
            """,
            coc_tag,
            days,
        )
    return [dict(r) for r in rows]


async def compute_day_diff(pool: asyncpg.Pool, coc_tag: str) -> dict | None:
    """Diff today's snapshot vs yesterday's. None if either is missing."""
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    today_snap = await get_snapshot(pool, coc_tag, today)
    yesterday_snap = await get_snapshot(pool, coc_tag, yesterday)
    if today_snap is None or yesterday_snap is None:
        return None

    return {
        "coc_tag": coc_tag,
        "trophy_change": (today_snap["trophies"] or 0) - (yesterday_snap["trophies"] or 0),
        "attacks_used": (today_snap["attacks_done"] or 0) - (yesterday_snap["attacks_done"] or 0),
        "defenses_taken": (today_snap["defenses_done"] or 0) - (yesterday_snap["defenses_done"] or 0),
    }
