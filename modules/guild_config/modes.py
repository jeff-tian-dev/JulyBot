"""On / testing / off modes for the bot's posting features, set with /toggle.

Every feature that posts on its own (roll call, watchdog, X, YouTube) has a
per-server mode in guild_settings:

- on:      posts to the feature's own channel, with real pings.
- testing: posts the same thing to the server's test channel (/setup testchannel),
           but never pings anyone: a mention is replaced by the plain name.
- off:     posts nothing. Background tracking (watchdog state, seen posts) keeps
           running, so turning a feature back on doesn't flood the channel.

Pollers ask `target_channel_id` (or `feature_channel`) where to post; None means
"don't post", whether because the feature is off or its channel isn't set.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import asyncpg
import disnake

from modules.guild_config import storage

logger = logging.getLogger(__name__)

ON = "on"
TESTING = "testing"
OFF = "off"
MODES = (ON, TESTING, OFF)

TEST_CHANNEL_COLUMN = "test_channel_id"

_DISCORD_OP_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    mode_column: str
    channel_column: str
    default_mode: str
    # The old on/off flag (X and YouTube only). Kept in step with the mode so the
    # startup backfill in database/models.py, which turns enabled=FALSE into
    # mode 'off', can never undo a later /toggle.
    enabled_column: str | None = None


# Column names are interpolated into SQL by set_mode, so they only ever come
# from this table, never from user input.
FEATURES: dict[str, Feature] = {
    f.key: f
    for f in (
        Feature("rollcall", "Roll call", "rollcall_mode", "rollcall_channel_id", OFF),
        Feature("watchdog", "Watchdog", "watchdog_mode", "clan_watch_channel_id", ON),
        Feature("x", "X", "twitter_mode", "twitter_channel_id", ON, "twitter_enabled"),
        Feature("youtube", "YouTube", "youtube_mode", "youtube_channel_id", ON, "youtube_enabled"),
    )
}


async def set_mode(pool: asyncpg.Pool, guild_id: int, feature: str, mode: str) -> None:
    """Set a feature's mode on one server."""
    f = FEATURES[feature]
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    columns = [f.mode_column] + ([f.enabled_column] if f.enabled_column else [])
    values = [mode] + ([mode != OFF] if f.enabled_column else [])
    placeholders = ", ".join(f"${i}" for i in range(2, len(values) + 2))
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO guild_settings (guild_id, {', '.join(columns)}, updated_at)
            VALUES ($1, {placeholders}, NOW())
            ON CONFLICT (guild_id) DO UPDATE SET {updates}, updated_at = NOW();
            """,
            guild_id,
            *values,
        )
    logger.info("%s=%s for guild_id=%s", f.mode_column, mode, guild_id)


def normalize(mode: str | None, default: str) -> str:
    """A stored mode, or `default` when it's missing or unrecognised."""
    return mode if mode in MODES else default


def mode_of(config: dict, feature: str) -> str:
    """A feature's mode from a get_guild_config() dict."""
    f = FEATURES[feature]
    return normalize(config.get(f.mode_column), f.default_mode)


def target_channel_id(mode: str, channel_id: int | None, test_channel_id: int | None) -> int | None:
    """Where a feature in `mode` posts: its own channel, the test channel, or nowhere."""
    if mode == OFF:
        return None
    if mode == TESTING:
        return test_channel_id or None
    return channel_id or None


def feature_target(config: dict, feature: str) -> tuple[str, int | None]:
    """(mode, channel id to post in or None) for a feature, from a get_guild_config() dict."""
    mode = mode_of(config, feature)
    channel_id = config.get(FEATURES[feature].channel_column)
    return mode, target_channel_id(mode, channel_id, config.get(TEST_CHANNEL_COLUMN))


async def feature_channel(bot: disnake.Client, pool: asyncpg.Pool, guild_id: int, feature: str):
    """(mode, channel) a feature should post to on a server; channel is None to stay quiet.

    A channel that resolves to a different server is refused, so one server's
    posts can never land in another's.
    """
    config = await storage.get_guild_config(pool, guild_id)
    mode, channel_id = feature_target(config, feature)
    if not channel_id:
        logger.debug("%s: nowhere to post for guild=%s (mode=%s)", feature, guild_id, mode)
        return mode, None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await asyncio.wait_for(
                bot.fetch_channel(channel_id), timeout=_DISCORD_OP_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            logger.warning("%s: timed out fetching channel %s", feature, channel_id)
            return mode, None
        except disnake.HTTPException:
            logger.warning("%s: channel %s not found", feature, channel_id)
            return mode, None
    channel_guild = getattr(channel, "guild", None)
    if channel_guild is None or channel_guild.id != guild_id:
        logger.warning(
            "%s: channel %s is not in guild=%s; not posting", feature, channel_id, guild_id
        )
        return mode, None
    return mode, channel


def role_preview(bot: disnake.Client, guild_id: int, role_id: int) -> str:
    """How a role ping reads in testing mode: its name as plain text, no mention."""
    guild = bot.get_guild(guild_id)
    role = guild.get_role(role_id) if guild is not None else None
    return f"@{role.name}" if role is not None else "@ping role"
