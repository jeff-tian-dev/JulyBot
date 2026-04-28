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
    discord_id BIGINT NOT NULL UNIQUE,
    coc_tag VARCHAR(20) NOT NULL UNIQUE,
    coc_name VARCHAR(100),
    verified BOOLEAN DEFAULT FALSE,
    linked_at TIMESTAMP DEFAULT NOW()
);
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

ALL_TABLES = ("users", "legend_snapshots", "base_cache", "watched_channels")


async def create_tables(pool: asyncpg.Pool) -> None:
    """Create the pgvector extension and all tables if they do not exist."""
    async with pool.acquire() as conn:
        logger.info("Ensuring pgvector extension is installed")
        await conn.execute(CREATE_VECTOR_EXTENSION)
        for ddl, name in (
            (CREATE_USERS, "users"),
            (CREATE_LEGEND_SNAPSHOTS, "legend_snapshots"),
            (CREATE_BASE_CACHE, "base_cache"),
            (CREATE_WATCHED_CHANNELS, "watched_channels"),
        ):
            logger.info("Creating table %s if not exists", name)
            await conn.execute(ddl)


async def drop_tables(pool: asyncpg.Pool) -> None:
    """Drop all tables. Intended for test teardown only."""
    async with pool.acquire() as conn:
        for name in ALL_TABLES:
            logger.warning("Dropping table %s", name)
            await conn.execute(f"DROP TABLE IF EXISTS {name} CASCADE;")
