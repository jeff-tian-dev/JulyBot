"""Poll watched YouTube channels and post new videos to Discord."""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import disnake

from modules.youtube_feed import embeds as embeds_mod
from modules.youtube_feed import fetcher
from modules.youtube_feed import storage

logger = logging.getLogger(__name__)

CHANNEL_POLL_DELAY_SECONDS = 2


async def poll_youtube_channels(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Poll all watched YouTube channels and post when the latest video ID changes."""
    summary = {"channels_polled": 0, "videos_posted": 0, "errors": 0}

    if not bot.is_ready():
        logger.debug("Discord bot not ready; skipping YouTube poll")
        return summary

    try:
        channels = await storage.get_all_watched_channels(pool)
    except Exception:
        logger.exception("Failed to fetch watched YouTube channels")
        summary["errors"] += 1
        return summary

    if not channels:
        return summary

    for watched in channels:
        guild_id = watched["guild_id"]
        channel_id = watched["channel_id"]
        discord_channel_id = watched.get("youtube_channel_id")
        enabled = watched.get("youtube_enabled")

        if not discord_channel_id or enabled is False:
            continue

        discord_channel = bot.get_channel(discord_channel_id)
        if discord_channel is None:
            logger.warning(
                "youtube_channel_id=%s not found for guild_id=%s",
                discord_channel_id,
                guild_id,
            )
            summary["errors"] += 1
            continue

        summary["channels_polled"] += 1
        last_seen = watched.get("last_seen_video_id")

        try:
            latest = await fetcher.fetch_latest_video(channel_id)
        except Exception:
            logger.exception("fetch_latest_video failed for channel_id=%s", channel_id)
            summary["errors"] += 1
            continue

        if latest is None:
            logger.warning("No latest video for channel_id=%s", channel_id)
            summary["errors"] += 1
            continue

        if last_seen is None:
            await storage.update_last_seen(pool, guild_id, channel_id, latest.video_id)
            logger.info(
                "Seeded channel_id=%s for guild_id=%s with video_id=%s (poll safety net)",
                channel_id,
                guild_id,
                latest.video_id,
            )
            continue

        if latest.video_id == last_seen:
            continue

        try:
            embed = embeds_mod.build_video_embed(latest)
            await discord_channel.send(embed=embed)
            summary["videos_posted"] += 1
        except Exception:
            logger.exception(
                "Failed to post video_id=%s for channel_id=%s in guild_id=%s",
                latest.video_id,
                channel_id,
                guild_id,
            )
            summary["errors"] += 1
            continue

        await storage.update_last_seen(pool, guild_id, channel_id, latest.video_id)
        await asyncio.sleep(CHANNEL_POLL_DELAY_SECONDS)

    logger.info(
        "YouTube poll complete: channels=%d videos_posted=%d errors=%d",
        summary["channels_polled"],
        summary["videos_posted"],
        summary["errors"],
    )
    return summary
