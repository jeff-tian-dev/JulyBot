"""Per-server settings storage: raw asyncpg reads/writes on guild_settings + guild_clans.

These used to be single-server .env values (MOD_LOG_CHANNEL_ID,
CLAN_WATCH_CHANNEL_ID, X/YOUTUBE_PING_ROLE_ID, COC_CLAN_TAG,
COC_FAMILY_CLAN_TAGS). Each server now has its own, and an unset value means
the feature is quiet on that server — never a fallback to another server's
channel or role. `import_legacy_config` copies the old .env values into the
home server's row once.
"""
from __future__ import annotations

import logging

import asyncpg

from modules.account_linker.linker import _normalize_tag as normalize_tag

logger = logging.getLogger(__name__)

# Columns /setup may write. The column name is interpolated into SQL, so it must
# come from this whitelist and never from user input.
CHANNEL_COLUMNS = ("mod_log_channel_id", "clan_watch_channel_id")
PING_ROLE_COLUMNS = {"x": "twitter_ping_role_id", "youtube": "youtube_ping_role_id"}

_CONFIG_FIELDS = (
    "mod_log_channel_id",
    "clan_watch_channel_id",
    "twitter_ping_role_id",
    "youtube_ping_role_id",
    "twitter_channel_id",
    "twitter_enabled",
    "youtube_channel_id",
    "youtube_enabled",
)


async def get_guild_config(pool: asyncpg.Pool, guild_id: int) -> dict:
    """Every per-server setting for a guild. Missing row => all None."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {', '.join(_CONFIG_FIELDS)} FROM guild_settings WHERE guild_id = $1;",
            guild_id,
        )
    if row is None:
        return {field: None for field in _CONFIG_FIELDS}
    return dict(row)


async def _set_column(pool: asyncpg.Pool, guild_id: int, column: str, value: int | None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO guild_settings (guild_id, {column}, updated_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (guild_id) DO UPDATE
                SET {column} = EXCLUDED.{column}, updated_at = NOW();
            """,
            guild_id,
            value,
        )
    logger.info("%s=%s for guild_id=%s", column, value, guild_id)


async def set_channel(pool: asyncpg.Pool, guild_id: int, column: str, channel_id: int | None) -> None:
    """Set (or clear, with None) one of the CHANNEL_COLUMNS for a guild."""
    if column not in CHANNEL_COLUMNS:
        raise ValueError(f"Unknown channel setting {column!r}")
    await _set_column(pool, guild_id, column, channel_id)


async def set_ping_role(pool: asyncpg.Pool, guild_id: int, feed: str, role_id: int | None) -> None:
    """Set (or clear, with None) the role pinged for new X or YouTube posts."""
    column = PING_ROLE_COLUMNS.get(feed)
    if column is None:
        raise ValueError(f"Unknown feed {feed!r}")
    await _set_column(pool, guild_id, column, role_id)


# --- clan family ------------------------------------------------------------

async def add_clan(
    pool: asyncpg.Pool, guild_id: int, clan_tag: str, clan_name: str | None, added_by: int | None
) -> bool:
    """Add a clan to a server's family. True if added, False if already there."""
    tag = normalize_tag(clan_tag)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO guild_clans (guild_id, clan_tag, clan_name, added_by)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (guild_id, clan_tag) DO NOTHING;
            """,
            guild_id,
            tag,
            clan_name,
            added_by,
        )
    added = result.endswith(" 1")
    if added:
        logger.info("Added clan %s to guild=%s", tag, guild_id)
    return added


async def remove_clan(pool: asyncpg.Pool, guild_id: int, clan_tag: str) -> bool:
    """Remove a clan from a server's family. True if it was there."""
    tag = normalize_tag(clan_tag)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM guild_clans WHERE guild_id = $1 AND clan_tag = $2;", guild_id, tag
        )
    removed = result.endswith(" 1")
    if removed:
        logger.info("Removed clan %s from guild=%s", tag, guild_id)
    return removed


async def list_clans(pool: asyncpg.Pool, guild_id: int) -> list[dict]:
    """A server's family clans, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT clan_tag, clan_name FROM guild_clans WHERE guild_id = $1 ORDER BY added_at;",
            guild_id,
        )
    return [dict(r) for r in rows]


async def clans_by_guild(pool: asyncpg.Pool) -> dict[int, list[str]]:
    """{guild_id: [clan_tag, ...]} for every server with a clan family (for clan watch)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT guild_id, clan_tag FROM guild_clans ORDER BY added_at;")
    out: dict[int, list[str]] = {}
    for r in rows:
        out.setdefault(r["guild_id"], []).append(r["clan_tag"])
    return out


# --- one-time import of the old single-server .env values -------------------

async def import_legacy_config(
    pool: asyncpg.Pool,
    guild_id: int,
    *,
    mod_log_channel_id: int = 0,
    clan_watch_channel_id: int = 0,
    twitter_ping_role_id: int = 0,
    youtube_ping_role_id: int = 0,
    clan_tags: list[str] | tuple[str, ...] = (),
) -> bool:
    """Copy the pre-multi-server .env values into the home server's settings, once.

    Guarded by guild_settings.legacy_config_imported so it runs a single time:
    an admin who later clears a value with /setup must not have it restored on
    the next restart. Values already set are never overwritten. Returns True if
    the import ran.
    """
    if not guild_id:
        return False

    def _or_null(value: int) -> int | None:
        return value or None

    tags: list[str] = []
    for raw in clan_tags:
        try:
            tag = normalize_tag(raw)
        except ValueError:
            continue
        if tag not in tags:
            tags.append(tag)

    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO guild_settings (guild_id, updated_at)
                VALUES ($1, NOW())
                ON CONFLICT (guild_id) DO UPDATE SET guild_id = EXCLUDED.guild_id
                RETURNING legacy_config_imported;
                """,
                guild_id,
            )
            if row["legacy_config_imported"]:
                return False
            await conn.execute(
                """
                UPDATE guild_settings
                SET mod_log_channel_id = COALESCE(mod_log_channel_id, $2),
                    clan_watch_channel_id = COALESCE(clan_watch_channel_id, $3),
                    twitter_ping_role_id = COALESCE(twitter_ping_role_id, $4),
                    youtube_ping_role_id = COALESCE(youtube_ping_role_id, $5),
                    legacy_config_imported = TRUE,
                    updated_at = NOW()
                WHERE guild_id = $1;
                """,
                guild_id,
                _or_null(mod_log_channel_id),
                _or_null(clan_watch_channel_id),
                _or_null(twitter_ping_role_id),
                _or_null(youtube_ping_role_id),
            )
            if tags:
                await conn.executemany(
                    """
                    INSERT INTO guild_clans (guild_id, clan_tag) VALUES ($1, $2)
                    ON CONFLICT (guild_id, clan_tag) DO NOTHING;
                    """,
                    [(guild_id, tag) for tag in tags],
                )
    logger.info(
        "Imported legacy .env config into guild=%s (%d clan tag(s))", guild_id, len(tags)
    )
    return True
