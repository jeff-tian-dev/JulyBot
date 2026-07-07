"""Schema definitions and create/drop helpers.

Raw SQL by design — the project uses asyncpg directly, not an ORM.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


CREATE_VECTOR_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

CREATE_USERS = """
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    discord_id BIGINT NOT NULL,
    coc_tag VARCHAR(20) NOT NULL UNIQUE,
    coc_name VARCHAR(100),
    verified BOOLEAN DEFAULT FALSE,
    linked_at TIMESTAMP DEFAULT NOW()
);
"""

# A Discord user may link several CoC accounts (alts), so discord_id is NOT
# unique — only coc_tag is. Older deploys created the table with a UNIQUE
# constraint on discord_id; drop it idempotently and index for lookups.
MIGRATE_USERS_MULTI_ACCOUNT = """
ALTER TABLE users DROP CONSTRAINT IF EXISTS users_discord_id_key;
CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users (discord_id);
"""

CREATE_LEGEND_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS legend_snapshots (
    id SERIAL PRIMARY KEY,
    coc_tag VARCHAR(20) NOT NULL,
    snapshot_date DATE NOT NULL,
    trophies INT,
    attacks_done INT,
    defenses_done INT,
    attack_wins INT,
    defense_wins INT,
    recorded_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(coc_tag, snapshot_date)
);
"""

CREATE_BASE_CACHE = """
CREATE TABLE IF NOT EXISTS base_cache (
    id SERIAL PRIMARY KEY,
    image_path VARCHAR(500) NOT NULL,
    phash VARCHAR(64) NOT NULL UNIQUE,
    source_url VARCHAR(500),
    source_channel VARCHAR(200),
    town_hall_level INT,
    embedding vector(512),
    captured_at TIMESTAMP DEFAULT NOW()
);
"""

CREATE_WATCHED_CHANNELS = """
CREATE TABLE IF NOT EXISTS watched_channels (
    id SERIAL PRIMARY KEY,
    channel_id VARCHAR(100) NOT NULL UNIQUE,
    channel_name VARCHAR(200),
    last_checked TIMESTAMP,
    added_at TIMESTAMP DEFAULT NOW()
);
"""

CREATE_GUILD_SETTINGS = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    ping_channel_id BIGINT,
    pings_enabled BOOLEAN DEFAULT TRUE,
    twitter_channel_id BIGINT,
    twitter_enabled BOOLEAN DEFAULT TRUE,
    updated_at TIMESTAMP DEFAULT NOW()
);
"""

ALTER_GUILD_SETTINGS_TWITTER = """
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS twitter_channel_id BIGINT;
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS twitter_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS twitter_last_ping_at TIMESTAMP;
"""

ALTER_GUILD_SETTINGS_YOUTUBE = """
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS youtube_channel_id BIGINT;
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS youtube_enabled BOOLEAN DEFAULT TRUE;
ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS youtube_last_ping_at TIMESTAMP;
"""

CREATE_YOUTUBE_WATCHED_CHANNELS = """
CREATE TABLE IF NOT EXISTS youtube_watched_channels (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id VARCHAR(24) NOT NULL,
    channel_name VARCHAR(200),
    last_seen_video_id VARCHAR(11),
    added_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(guild_id, channel_id)
);
"""

ALTER_YOUTUBE_WATCHED_CHANNELS = """
ALTER TABLE youtube_watched_channels ADD COLUMN IF NOT EXISTS channel_name VARCHAR(200);
"""

CREATE_TWITTER_WATCHED_ACCOUNTS = """
CREATE TABLE IF NOT EXISTS twitter_watched_accounts (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    username VARCHAR(50) NOT NULL,
    last_seen_tweet_id BIGINT DEFAULT 0,
    added_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(guild_id, username)
);
"""

CREATE_SEEN_TWEETS = """
CREATE TABLE IF NOT EXISTS seen_tweets (
    tweet_id BIGINT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    username VARCHAR(50) NOT NULL,
    posted_at TIMESTAMP DEFAULT NOW()
);
"""

ALL_TABLES = (
    "seen_tweets",
    "twitter_watched_accounts",
    "youtube_watched_channels",
    "users",
    "legend_snapshots",
    "base_cache",
    "watched_channels",
    "guild_settings",
)


async def create_tables(pool: asyncpg.Pool) -> None:
    """Create tables if they do not exist."""
    async with pool.acquire() as conn:
        logger.info("Ensuring pgvector extension is installed")
        await conn.execute(CREATE_VECTOR_EXTENSION)
        table_ddls = (
            (CREATE_USERS, "users"),
            (CREATE_LEGEND_SNAPSHOTS, "legend_snapshots"),
            (CREATE_BASE_CACHE, "base_cache"),
            (CREATE_WATCHED_CHANNELS, "watched_channels"),
            (CREATE_GUILD_SETTINGS, "guild_settings"),
        )

        for ddl, name in table_ddls:
            logger.info("Creating table %s if not exists", name)
            await conn.execute(ddl)
            if name == "users":
                logger.info("Applying multi-account migration to users")
                await conn.execute(MIGRATE_USERS_MULTI_ACCOUNT)

        logger.info("Applying guild_settings X column migrations if needed")
        await conn.execute(ALTER_GUILD_SETTINGS_TWITTER)

        x_ddls = (
            (CREATE_TWITTER_WATCHED_ACCOUNTS, "twitter_watched_accounts"),
            (CREATE_SEEN_TWEETS, "seen_tweets"),
        )
        for ddl, name in x_ddls:
            logger.info("Creating table %s if not exists", name)
            await conn.execute(ddl)

        logger.info("Applying guild_settings youtube column migrations if needed")
        await conn.execute(ALTER_GUILD_SETTINGS_YOUTUBE)

        logger.info("Creating table youtube_watched_channels if not exists")
        await conn.execute(CREATE_YOUTUBE_WATCHED_CHANNELS)
        logger.info("Applying youtube_watched_channels column migrations if needed")
        await conn.execute(ALTER_YOUTUBE_WATCHED_CHANNELS)


async def drop_tables(pool: asyncpg.Pool) -> None:
    """Drop all tables. Intended for test teardown only."""
    async with pool.acquire() as conn:
        for name in ALL_TABLES:
            logger.warning("Dropping table %s", name)
            await conn.execute(f"DROP TABLE IF EXISTS {name} CASCADE;")
