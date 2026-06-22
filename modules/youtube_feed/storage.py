"""DB CRUD for YouTube feed watch lists."""
from __future__ import annotations

import logging
import re

import asyncpg

from modules.youtube_feed import fetcher

logger = logging.getLogger(__name__)

CHANNEL_ID_PATTERN = re.compile(r"^UC[\w-]{22}$")


def normalize_channel_id(channel_id: str) -> str:
    """Validate and normalize a YouTube channel ID."""
    cleaned = channel_id.strip()
    if not CHANNEL_ID_PATTERN.match(cleaned):
        raise ValueError(
            f"Invalid YouTube channel ID {channel_id!r}. "
            "Expected format: UC followed by 22 characters (e.g. UCxxxxxxxxxxxxxxxxxxxxxx)."
        )
    return cleaned


def channel_label(channel_id: str, channel_name: str | None) -> str:
    """Human-readable channel name, falling back to the ID."""
    if channel_name:
        return channel_name
    return channel_id


def format_channel_reference(channel_id: str, channel_name: str | None) -> str:
    """Discord-friendly display with both name and ID for copy/paste."""
    if channel_name:
        return f"**{channel_name}** (`{channel_id}`)"
    return f"`{channel_id}`"


async def resolve_channel_name(channel_id: str, channel_name: str | None) -> str:
    """Return stored channel name or fetch it from the RSS feed."""
    if channel_name:
        return channel_name
    try:
        latest = await fetcher.fetch_latest_video(channel_id)
        if latest is not None and latest.channel_title:
            return latest.channel_title
        title = await fetcher.fetch_channel_title(channel_id)
        if title:
            return title
    except Exception:
        logger.exception("Failed to resolve channel name for channel_id=%s", channel_id)
    return channel_id


async def add_watched_channel(
    pool: asyncpg.Pool,
    guild_id: int,
    channel_id: str,
) -> dict:
    """Add a YouTube channel to the guild watch list, priming last_seen to avoid backfill."""
    channel_id = normalize_channel_id(channel_id)
    last_seen: str | None = None
    channel_name: str | None = None
    try:
        latest = await fetcher.fetch_latest_video(channel_id)
        if latest is not None:
            last_seen = latest.video_id
            channel_name = latest.channel_title or None
    except Exception:
        logger.exception("Failed to prime last_seen for channel_id=%s", channel_id)

    if channel_name is None:
        try:
            channel_name = await fetcher.fetch_channel_title(channel_id)
        except Exception:
            logger.exception("Failed to fetch channel title for channel_id=%s", channel_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO youtube_watched_channels (guild_id, channel_id, channel_name, last_seen_video_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, channel_id) DO UPDATE
                SET last_seen_video_id = COALESCE(
                    youtube_watched_channels.last_seen_video_id,
                    EXCLUDED.last_seen_video_id
                ),
                channel_name = COALESCE(
                    youtube_watched_channels.channel_name,
                    EXCLUDED.channel_name
                )
            RETURNING id, guild_id, channel_id, channel_name, last_seen_video_id, added_at;
            """,
            guild_id,
            channel_id,
            channel_name,
            last_seen,
        )

    logger.info(
        "Watching YouTube %s (%s) for guild_id=%s (last_seen=%s)",
        channel_label(channel_id, channel_name),
        channel_id,
        guild_id,
        last_seen,
    )
    return dict(row)


async def remove_watched_channel(pool: asyncpg.Pool, guild_id: int, channel_id: str) -> bool:
    """Remove a YouTube channel from the guild watch list."""
    channel_id = normalize_channel_id(channel_id)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM youtube_watched_channels WHERE guild_id = $1 AND channel_id = $2;",
            guild_id,
            channel_id,
        )
    deleted = result.endswith(" 1")
    if deleted:
        logger.info("Stopped watching channel_id=%s for guild_id=%s", channel_id, guild_id)
    return deleted


async def list_watched_channels(pool: asyncpg.Pool, guild_id: int) -> list[dict]:
    """List watched YouTube channels for a guild."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, guild_id, channel_id, channel_name, last_seen_video_id, added_at
            FROM youtube_watched_channels
            WHERE guild_id = $1
            ORDER BY COALESCE(channel_name, channel_id);
            """,
            guild_id,
        )

    channels = [dict(r) for r in rows]
    for channel in channels:
        if channel.get("channel_name"):
            continue
        resolved = await resolve_channel_name(channel["channel_id"], None)
        if resolved != channel["channel_id"]:
            channel["channel_name"] = resolved
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE youtube_watched_channels
                    SET channel_name = $3
                    WHERE guild_id = $1 AND channel_id = $2 AND channel_name IS NULL;
                    """,
                    guild_id,
                    channel["channel_id"],
                    resolved,
                )
    return channels


async def get_all_watched_channels(pool: asyncpg.Pool) -> list[dict]:
    """Return all watched YouTube channels across guilds (for the scheduler)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.guild_id, w.channel_id, w.channel_name, w.last_seen_video_id, w.added_at,
                   g.youtube_channel_id, g.youtube_enabled
            FROM youtube_watched_channels w
            LEFT JOIN guild_settings g ON g.guild_id = w.guild_id
            ORDER BY w.guild_id, w.channel_id;
            """
        )
    return [dict(r) for r in rows]


async def get_youtube_settings(pool: asyncpg.Pool, guild_id: int) -> dict | None:
    """Return YouTube output channel + enabled flag for a guild."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT guild_id, youtube_channel_id, youtube_enabled
            FROM guild_settings
            WHERE guild_id = $1;
            """,
            guild_id,
        )
    if row is None:
        return None
    return {
        "guild_id": row["guild_id"],
        "youtube_channel_id": row["youtube_channel_id"],
        "youtube_enabled": row["youtube_enabled"],
    }


async def set_youtube_channel(pool: asyncpg.Pool, guild_id: int, channel_id: int) -> None:
    """Set the Discord channel where new YouTube videos are posted."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, youtube_channel_id, youtube_enabled, updated_at)
            VALUES ($1, $2, TRUE, NOW())
            ON CONFLICT (guild_id) DO UPDATE
                SET youtube_channel_id = EXCLUDED.youtube_channel_id,
                    updated_at = NOW();
            """,
            guild_id,
            channel_id,
        )
    logger.info("youtube_channel_id=%s for guild_id=%s", channel_id, guild_id)


async def toggle_youtube(pool: asyncpg.Pool, guild_id: int) -> bool:
    """Flip youtube_enabled for a guild. Returns the new state."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO guild_settings (guild_id, youtube_enabled, updated_at)
            VALUES ($1, TRUE, NOW())
            ON CONFLICT (guild_id) DO UPDATE
                SET youtube_enabled = NOT COALESCE(guild_settings.youtube_enabled, TRUE),
                    updated_at = NOW()
            RETURNING youtube_enabled;
            """,
            guild_id,
        )
    enabled = bool(row["youtube_enabled"])
    logger.info("youtube_enabled=%s for guild_id=%s", enabled, guild_id)
    return enabled


async def update_last_seen(
    pool: asyncpg.Pool,
    guild_id: int,
    channel_id: str,
    video_id: str,
) -> None:
    """Update the tracked latest video ID for a channel."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE youtube_watched_channels
            SET last_seen_video_id = $3
            WHERE guild_id = $1 AND channel_id = $2;
            """,
            guild_id,
            channel_id,
            video_id,
        )


async def seed_unseeded_channels(pool: asyncpg.Pool) -> int:
    """Fetch and store latest video IDs for channels not yet seeded. Returns count seeded."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT guild_id, channel_id
            FROM youtube_watched_channels
            WHERE last_seen_video_id IS NULL;
            """
        )

    seeded = 0
    for row in rows:
        guild_id = row["guild_id"]
        channel_id = row["channel_id"]
        try:
            latest = await fetcher.fetch_latest_video(channel_id)
            if latest is None:
                logger.warning("No video found when seeding channel_id=%s", channel_id)
                continue
            await update_last_seen(pool, guild_id, channel_id, latest.video_id)
            seeded += 1
            logger.info(
                "Seeded channel_id=%s for guild_id=%s with video_id=%s",
                channel_id,
                guild_id,
                latest.video_id,
            )
        except Exception:
            logger.exception("Failed to seed channel_id=%s for guild_id=%s", channel_id, guild_id)

    return seeded
