"""Persistence for /postbase — base posts and their unique-download tally.

Raw asyncpg by design. Every function takes the pool first so it stays callable
from tests with a mocked pool and no running bot.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# Discord's embed-title limit; the stored title is capped to match.
MAX_TITLE_LEN = 256


async def create_base_post(
    pool: asyncpg.Pool,
    *,
    guild_id: int,
    channel_id: int,
    author_id: int,
    link: str,
    title: str | None = None,
    cc: str | None = None,
    description: str | None = None,
    image_url: str | None = None,
    image_filename: str | None = None,
    stats_admin_only: bool = False,
) -> asyncpg.Record:
    """Insert a base post before it's sent; message_id is attached afterwards."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO base_posts (
                guild_id, channel_id, author_id, link,
                title, cc, description, image_url, image_filename,
                stats_admin_only
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            RETURNING *;
            """,
            guild_id,
            channel_id,
            author_id,
            link,
            title,
            cc,
            description,
            image_url,
            image_filename,
            stats_admin_only,
        )


async def attach_message(pool: asyncpg.Pool, base_post_id: int, message_id: int) -> None:
    """Record which message a base post was published as."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE base_posts SET message_id = $2, updated_at = NOW() WHERE id = $1;",
            base_post_id,
            message_id,
        )


async def delete_base_post(pool: asyncpg.Pool, base_post_id: int) -> None:
    """Remove a base post (used to roll back when the send fails)."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM base_posts WHERE id = $1;", base_post_id)


async def get_base_post(pool: asyncpg.Pool, base_post_id: int) -> asyncpg.Record | None:
    """Fetch a base post by its primary key. None if it's gone."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM base_posts WHERE id = $1;", base_post_id)


async def update_base_post(
    pool: asyncpg.Pool,
    base_post_id: int,
    *,
    title: str | None = None,
    cc: str | None = None,
    description: str | None = None,
    link: str | None = None,
    image_url: str | None = None,
    image_filename: str | None = None,
) -> asyncpg.Record | None:
    """Update only the fields passed as non-None; returns the updated row.

    Callers that want to *clear* a field pass an empty string, which is stored
    as NULL — that's how the edit modal removes a title or CC line.
    """
    fields = {
        "title": title,
        "cc": cc,
        "description": description,
        "link": link,
        "image_url": image_url,
        "image_filename": image_filename,
    }
    provided = {k: (v or None) for k, v in fields.items() if v is not None}
    if not provided:
        return await get_base_post(pool, base_post_id)

    # $1 is the id; the SET params start at $2 in insertion order.
    assignments = ", ".join(f"{col} = ${i}" for i, col in enumerate(provided, start=2))
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            f"UPDATE base_posts SET {assignments}, updated_at = NOW() "
            "WHERE id = $1 RETURNING *;",
            base_post_id,
            *provided.values(),
        )


async def record_download(pool: asyncpg.Pool, base_post_id: int, user_id: int) -> int:
    """Record that `user_id` fetched this post's link; return the unique count.

    ON CONFLICT DO NOTHING keeps repeat presses by the same user from inflating
    the tally, so the returned COUNT is unique downloaders.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO base_post_downloads (base_post_id, user_id) VALUES ($1, $2) "
            "ON CONFLICT DO NOTHING;",
            base_post_id,
            user_id,
        )
        return await conn.fetchval(
            "SELECT COUNT(*) FROM base_post_downloads WHERE base_post_id = $1;",
            base_post_id,
        )


async def count_downloads(pool: asyncpg.Pool, base_post_id: int) -> int:
    """Number of unique users who have fetched this post's link."""
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM base_post_downloads WHERE base_post_id = $1;",
            base_post_id,
        ) or 0


async def list_downloaders(pool: asyncpg.Pool, base_post_id: int) -> list[asyncpg.Record]:
    """Every user who fetched this post's link, oldest first."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT user_id, fetched_at FROM base_post_downloads "
            "WHERE base_post_id = $1 ORDER BY fetched_at ASC;",
            base_post_id,
        )


async def list_base_post_ids(pool: asyncpg.Pool) -> list[int]:
    """All published base post ids — used to re-register views on startup."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id FROM base_posts WHERE message_id IS NOT NULL ORDER BY id;"
        )
    return [row["id"] for row in rows]


async def list_views_to_restore(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Every published post with the data needed to rebuild its view.

    One query with a LEFT JOIN aggregate instead of a count per post, so startup
    stays O(1) queries no matter how many base posts exist.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT p.id,
                   p.stats_admin_only,
                   COUNT(d.user_id) AS download_count
            FROM base_posts p
            LEFT JOIN base_post_downloads d ON d.base_post_id = p.id
            WHERE p.message_id IS NOT NULL
            GROUP BY p.id, p.stats_admin_only
            ORDER BY p.id;
            """
        )
