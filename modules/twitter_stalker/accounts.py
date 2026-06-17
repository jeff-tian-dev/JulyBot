"""Stalked Twitter account CRUD and alert channel config."""
from __future__ import annotations

import logging
import re

import asyncpg

from modules.twitter_stalker import api
from modules.twitter_stalker.filter_query import validate_filter_capacity

logger = logging.getLogger(__name__)

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{1,50}$")


def normalize_username(raw: str) -> str:
    """Strip @ and lowercase; validate Twitter handle format."""
    username = raw.strip().lstrip("@").lower()
    if not username or not _USERNAME_RE.match(username):
        raise ValueError(
            f"Invalid Twitter username {raw!r}. Use 1–50 letters, digits, or underscores."
        )
    return username


async def _ensure_config_row(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO twitter_stalker_config (id)
        VALUES (1)
        ON CONFLICT (id) DO NOTHING
        """
    )


async def get_config(pool: asyncpg.Pool) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT discord_channel_id, filter_rule_id FROM twitter_stalker_config WHERE id = 1"
        )
        return dict(row) if row else None


async def set_alert_channel(pool: asyncpg.Pool, channel_id: int) -> None:
    async with pool.acquire() as conn:
        await _ensure_config_row(conn)
        await conn.execute(
            """
            UPDATE twitter_stalker_config
            SET discord_channel_id = $1, updated_at = NOW()
            WHERE id = 1
            """,
            channel_id,
        )


async def get_alert_channel_id(pool: asyncpg.Pool) -> int | None:
    config = await get_config(pool)
    if not config:
        return None
    return config.get("discord_channel_id")


async def get_filter_rule_id(pool: asyncpg.Pool) -> str | None:
    config = await get_config(pool)
    if not config:
        return None
    rule_id = config.get("filter_rule_id")
    return str(rule_id) if rule_id else None


async def save_filter_rule_id(pool: asyncpg.Pool, rule_id: str | None) -> None:
    async with pool.acquire() as conn:
        await _ensure_config_row(conn)
        await conn.execute(
            """
            UPDATE twitter_stalker_config
            SET filter_rule_id = $1, updated_at = NOW()
            WHERE id = 1
            """,
            rule_id,
        )


async def list_accounts(pool: asyncpg.Pool) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT twitter_username, display_name, added_by_discord_id, added_at
            FROM stalked_twitter_accounts
            ORDER BY twitter_username
            """
        )
        return [dict(row) for row in rows]


async def list_usernames(pool: asyncpg.Pool) -> list[str]:
    accounts = await list_accounts(pool)
    return [a["twitter_username"] for a in accounts]


async def add_account(
    pool: asyncpg.Pool,
    raw_username: str,
    added_by: int | None = None,
) -> dict:
    """Validate user via API, insert into DB, return the new row."""
    username = normalize_username(raw_username)

    if not api.api_key_configured():
        raise ValueError("TWITTERAPI_IO_KEY is not set. Add it to .env.")

    existing = await list_usernames(pool)
    if username not in existing:
        validate_filter_capacity(existing + [username])

    user = await api.get_user_info(username)
    display_name = user.get("name") or user.get("userName") or username

    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO stalked_twitter_accounts
                    (twitter_username, display_name, added_by_discord_id)
                VALUES ($1, $2, $3)
                RETURNING twitter_username, display_name, added_by_discord_id, added_at
                """,
                username,
                display_name,
                added_by,
            )
        except asyncpg.UniqueViolationError:
            raise ValueError(f"@{username} is already on the stalk list.") from None

    logger.info("Added stalked account @%s", username)
    return dict(row)


async def remove_account(pool: asyncpg.Pool, raw_username: str) -> bool:
    username = normalize_username(raw_username)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM stalked_twitter_accounts WHERE twitter_username = $1",
            username,
        )
    removed = result.endswith("1")
    if removed:
        logger.info("Removed stalked account @%s", username)
    return removed


async def record_seen_tweet(
    pool: asyncpg.Pool,
    tweet_id: str,
    twitter_username: str | None = None,
) -> bool:
    """Insert tweet id for dedup. Returns True if this is the first time seen."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO seen_tweets (tweet_id, twitter_username)
            VALUES ($1, $2)
            ON CONFLICT (tweet_id) DO NOTHING
            """,
            tweet_id,
            twitter_username,
        )
    return result.endswith("1")
