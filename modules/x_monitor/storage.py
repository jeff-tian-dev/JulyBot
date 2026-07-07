"""DB CRUD for X monitor watch lists and deduplication."""
from __future__ import annotations

import logging
import re

import asyncpg

from modules.x_monitor import client as x_client

logger = logging.getLogger(__name__)

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def normalize_username(username: str) -> str:
    """Validate and normalize an X handle."""
    cleaned = username.strip().lstrip("@").lower()
    if not USERNAME_PATTERN.match(cleaned):
        raise ValueError(
            f"Invalid X username {username!r}. Use 1-15 characters: letters, digits, underscore."
        )
    return cleaned


async def add_watched_account(
    pool: asyncpg.Pool,
    guild_id: int,
    username: str,
) -> dict:
    """Add an account to the guild watch list, priming last_seen to avoid backfill."""
    username = normalize_username(username)
    last_seen = 0
    if x_client.is_configured():
        try:
            last_seen = await x_client.fetch_latest_tweet_id(username)
        except Exception:
            logger.exception("Failed to prime last_seen for @%s", username)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO twitter_watched_accounts (guild_id, username, last_seen_tweet_id)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, username) DO UPDATE
                SET last_seen_tweet_id = GREATEST(
                    twitter_watched_accounts.last_seen_tweet_id,
                    EXCLUDED.last_seen_tweet_id
                )
            RETURNING id, guild_id, username, last_seen_tweet_id, added_at;
            """,
            guild_id,
            username,
            last_seen,
        )

    logger.info("Watching @%s for guild_id=%s (last_seen=%s)", username, guild_id, last_seen)
    return dict(row)


async def remove_watched_account(pool: asyncpg.Pool, guild_id: int, username: str) -> bool:
    """Remove an account from the guild watch list."""
    username = normalize_username(username)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM twitter_watched_accounts WHERE guild_id = $1 AND username = $2;",
            guild_id,
            username,
        )
    deleted = result.endswith(" 1")
    if deleted:
        logger.info("Stopped watching @%s for guild_id=%s", username, guild_id)
    return deleted


async def list_watched_accounts(pool: asyncpg.Pool, guild_id: int) -> list[dict]:
    """List watched accounts for a guild."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, guild_id, username, last_seen_tweet_id, added_at
            FROM twitter_watched_accounts
            WHERE guild_id = $1
            ORDER BY username;
            """,
            guild_id,
        )
    return [dict(r) for r in rows]


async def get_all_watched_accounts(pool: asyncpg.Pool) -> list[dict]:
    """Return all watched accounts across guilds (for the scheduler)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT w.id, w.guild_id, w.username, w.last_seen_tweet_id, w.added_at,
                   g.twitter_channel_id AS x_channel_id,
                   g.twitter_enabled AS x_enabled
            FROM twitter_watched_accounts w
            LEFT JOIN guild_settings g ON g.guild_id = w.guild_id
            ORDER BY w.guild_id, w.username;
            """
        )
    return [dict(r) for r in rows]


async def get_x_settings(pool: asyncpg.Pool, guild_id: int) -> dict | None:
    """Return X output channel + enabled flag for a guild."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT guild_id,
                   twitter_channel_id AS x_channel_id,
                   twitter_enabled AS x_enabled
            FROM guild_settings
            WHERE guild_id = $1;
            """,
            guild_id,
        )
    if row is None:
        return None
    return dict(row)


async def set_x_channel(pool: asyncpg.Pool, guild_id: int, channel_id: int) -> None:
    """Set the Discord channel where X posts are published."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO guild_settings (guild_id, twitter_channel_id, twitter_enabled, updated_at)
            VALUES ($1, $2, TRUE, NOW())
            ON CONFLICT (guild_id) DO UPDATE
                SET twitter_channel_id = EXCLUDED.twitter_channel_id,
                    updated_at = NOW();
            """,
            guild_id,
            channel_id,
        )
    logger.info("x_channel_id=%s for guild_id=%s", channel_id, guild_id)


async def toggle_x(pool: asyncpg.Pool, guild_id: int) -> bool:
    """Flip X monitoring for a guild. Returns the new state."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO guild_settings (guild_id, twitter_enabled, updated_at)
            VALUES ($1, TRUE, NOW())
            ON CONFLICT (guild_id) DO UPDATE
                SET twitter_enabled = NOT COALESCE(guild_settings.twitter_enabled, TRUE),
                    updated_at = NOW()
            RETURNING twitter_enabled AS x_enabled;
            """,
            guild_id,
        )
    enabled = bool(row["x_enabled"])
    logger.info("x_enabled=%s for guild_id=%s", enabled, guild_id)
    return enabled


async def claim_ping_slot(pool: asyncpg.Pool, guild_id: int, cooldown_hours: int) -> bool:
    """Atomically claim the role-ping slot for a guild's X feed.

    Returns True (and stamps NOW()) only if no ping has been sent within the last
    ``cooldown_hours``. Subsequent calls inside the window return False, so bursts of
    posts ping once and stay quiet after. The check-and-set is a single UPDATE so
    concurrent posts can't both claim. A guild_settings row always exists here because
    an X output channel must be configured before posting.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE guild_settings
            SET twitter_last_ping_at = NOW()
            WHERE guild_id = $1
              AND (
                  twitter_last_ping_at IS NULL
                  OR twitter_last_ping_at < NOW() - make_interval(hours => $2)
              )
            RETURNING twitter_last_ping_at;
            """,
            guild_id,
            cooldown_hours,
        )
    return row is not None


async def mark_tweets_seen(
    pool: asyncpg.Pool,
    guild_id: int,
    username: str,
    tweet_ids: list[int],
) -> list[int]:
    """Insert tweet IDs into seen_tweets; return only newly inserted IDs."""
    if not tweet_ids:
        return []

    newly_seen: list[int] = []
    async with pool.acquire() as conn:
        for tweet_id in tweet_ids:
            result = await conn.fetchrow(
                """
                INSERT INTO seen_tweets (tweet_id, guild_id, username)
                VALUES ($1, $2, $3)
                ON CONFLICT (tweet_id) DO NOTHING
                RETURNING tweet_id;
                """,
                tweet_id,
                guild_id,
                username,
            )
            if result is not None:
                newly_seen.append(result["tweet_id"])
    return newly_seen


async def unmark_tweet_seen(pool: asyncpg.Pool, tweet_id: int) -> None:
    """Release a dedup claim so a failed post can be retried on the next poll."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM seen_tweets WHERE tweet_id = $1;",
            tweet_id,
        )


async def update_last_seen(
    pool: asyncpg.Pool,
    guild_id: int,
    username: str,
    tweet_id: int,
) -> None:
    """Update the high-water mark for an account."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE twitter_watched_accounts
            SET last_seen_tweet_id = GREATEST(last_seen_tweet_id, $3)
            WHERE guild_id = $1 AND username = $2;
            """,
            guild_id,
            username,
            tweet_id,
        )
