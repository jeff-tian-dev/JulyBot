"""Ranked Battles "likely to be hit next" DM tracking.

/trackingon subscribes the invoking Discord user to DM alerts on a CoC player
tag: every poll (see modules/ping_automator/scheduler.py), if
is_likely_to_be_hit_next()'s status for that tag has changed since the last
poll, the user is DMed. No DM on the first successful check (nothing to
diff against yet), a same-status poll, or while the status is UNKNOWN
(player not currently in an active group) — those cycles are skipped
entirely, tracking stays on, and last_status is left untouched so the next
real status is still compared against the last *real* one, not UNKNOWN.
"""
from __future__ import annotations

import logging

import asyncpg
import disnake

from modules.account_linker.linker import _normalize_tag as normalize_tag
from modules.ranked_tracker import poller
from modules.ranked_tracker.group import (
    LIKELY_TARGET,
    NOT_FLAGGED,
    UNKNOWN,
    is_likely_to_be_hit_next,
    resolve_current_group,
)

logger = logging.getLogger(__name__)

_STATUS_LABELS = {
    LIKELY_TARGET: "⚠️ likely to be hit next",
    NOT_FLAGGED: "✅ not currently a likely target",
}


async def start_tracking(pool: asyncpg.Pool, discord_id: int, coc_tag: str) -> str:
    """Subscribe discord_id to status-change DMs for coc_tag. Returns the
    normalized tag. Idempotent — tracking an already-tracked tag is a no-op."""
    tag = normalize_tag(coc_tag)
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ranked_tracking (discord_id, coc_tag)
            VALUES ($1, $2)
            ON CONFLICT (discord_id, coc_tag) DO NOTHING;
            """,
            discord_id,
            tag,
        )
    return tag


async def stop_tracking(pool: asyncpg.Pool, discord_id: int, coc_tag: str) -> bool:
    """Unsubscribe discord_id from coc_tag. Returns True if a row was removed."""
    tag = normalize_tag(coc_tag)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM ranked_tracking WHERE discord_id = $1 AND coc_tag = $2;",
            discord_id,
            tag,
        )
    return result.endswith(" 1")


async def list_tracked(pool: asyncpg.Pool, discord_id: int) -> list[str]:
    """Return every CoC tag discord_id currently tracks, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT coc_tag FROM ranked_tracking WHERE discord_id = $1 ORDER BY created_at;",
            discord_id,
        )
    return [r["coc_tag"] for r in rows]


async def _all_tracking_rows(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT id, discord_id, coc_tag, last_status FROM ranked_tracking;")


async def _update_last_status(pool: asyncpg.Pool, row_id: int, status: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ranked_tracking SET last_status = $1, updated_at = NOW() WHERE id = $2;",
            status,
            row_id,
        )


async def _check_current_status(coc_tag: str) -> str:
    """Resolve a tag's group and return its current is_likely_to_be_hit_next
    status. Returns UNKNOWN on any failure to resolve/fetch, matching the
    "skip silently, keep tracking on" behavior for a tag with no active group."""
    try:
        _player_name, tag, group_tag, season_id = await resolve_current_group(coc_tag)
    except Exception:
        return UNKNOWN

    try:
        group_data = await poller.get_league_group(group_tag, season_id, tag)
    except poller.LeagueGroupFetchError:
        return UNKNOWN

    members = group_data.get("members", [])
    return is_likely_to_be_hit_next(members, tag)


async def poll_ranked_tracking(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Scheduled job: check every tracked tag and DM on a real status change.

    Returns a summary dict for logging. Never raises — a single row's
    failure (bad tag, DM blocked, CoC API error) is caught and skipped so
    one broken subscription can't stop the rest from being checked.
    """
    summary = {"checked": 0, "notified": 0, "skipped": 0, "errors": 0}
    rows = await _all_tracking_rows(pool)

    for row in rows:
        summary["checked"] += 1
        try:
            status = await _check_current_status(row["coc_tag"])
        except Exception:
            logger.exception("ranked_tracking: status check failed for %s", row["coc_tag"])
            summary["errors"] += 1
            continue

        if status == UNKNOWN:
            summary["skipped"] += 1
            continue

        last_status = row["last_status"]
        if last_status is None:
            # First successful check — seed silently, nothing to diff against yet.
            await _update_last_status(pool, row["id"], status)
            continue

        if status == last_status:
            summary["skipped"] += 1
            continue

        try:
            user = await bot.fetch_user(row["discord_id"])
            await user.send(
                f"**{row['coc_tag']}** changed status: {_STATUS_LABELS[status]}"
            )
            summary["notified"] += 1
        except disnake.HTTPException:
            logger.warning(
                "ranked_tracking: failed to DM discord_id=%s for %s (DMs closed?)",
                row["discord_id"],
                row["coc_tag"],
            )
            summary["errors"] += 1
        except Exception:
            logger.exception(
                "ranked_tracking: unexpected error notifying discord_id=%s for %s",
                row["discord_id"],
                row["coc_tag"],
            )
            summary["errors"] += 1

        await _update_last_status(pool, row["id"], status)

    return summary
