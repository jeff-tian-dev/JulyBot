"""Ticket storage: raw asyncpg reads/writes on the four ticket tables.

Pool-first and Discord-free, so it stays testable with a mocked pool. See the
table comments in database/models.py for why each table is shaped the way it is.

Every id a button or command hands in is global, so callers must compare the
returned row's `guild_id` with the server the click came from (the service
layer does) — the same rule the purchase commands follow.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)

# A server's first ticket is #1000, then 1001, 1002, ...
FIRST_TICKET_NUMBER = 1000


class DuplicateTypeError(ValueError):
    """A live ticket type in this server already has that name."""


def _dict(row) -> dict | None:
    return dict(row) if row is not None else None


# --- config -------------------------------------------------------------------

async def get_config(pool: asyncpg.Pool, guild_id: int) -> dict | None:
    """A server's ticket settings, or None if /ticket setup was never run."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ticket_config WHERE guild_id = $1;", guild_id)
    return _dict(row)


async def set_locations(
    pool: asyncpg.Pool,
    guild_id: int,
    *,
    panel_channel_id: int,
    category_id: int,
    log_channel_id: int,
) -> dict:
    """Save the panel channel, tickets category and log channel.

    Moving the panel to another channel forgets the old panel message: a
    message can't be moved, so the next publish posts a new one there.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO ticket_config (guild_id, panel_channel_id, category_id, log_channel_id, updated_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (guild_id) DO UPDATE
                SET panel_message_id = CASE
                        WHEN ticket_config.panel_channel_id IS DISTINCT FROM EXCLUDED.panel_channel_id
                        THEN NULL ELSE ticket_config.panel_message_id END,
                    panel_channel_id = EXCLUDED.panel_channel_id,
                    category_id = EXCLUDED.category_id,
                    log_channel_id = EXCLUDED.log_channel_id,
                    updated_at = NOW()
            RETURNING *;
            """,
            guild_id,
            panel_channel_id,
            category_id,
            log_channel_id,
        )
    logger.info(
        "Ticket locations for guild=%s: panel=%s category=%s log=%s",
        guild_id, panel_channel_id, category_id, log_channel_id,
    )
    return dict(row)


async def set_panel_content(
    pool: asyncpg.Pool,
    guild_id: int,
    *,
    title: str | None,
    body: str | None,
    image: str | None,
    thumbnail: str | None,
) -> None:
    """Overwrite what the panel says. Every value is the final one."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE ticket_config
            SET panel_title = $2, panel_body = $3, panel_image = $4, panel_thumbnail = $5,
                updated_at = NOW()
            WHERE guild_id = $1;
            """,
            guild_id,
            title,
            body,
            image,
            thumbnail,
        )


async def set_panel_message(pool: asyncpg.Pool, guild_id: int, message_id: int | None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE ticket_config SET panel_message_id = $2, updated_at = NOW() WHERE guild_id = $1;",
            guild_id,
            message_id,
        )


# --- ticket types -------------------------------------------------------------

async def list_types(pool: asyncpg.Pool, guild_id: int) -> list[dict]:
    """A server's live ticket types, oldest first (the panel's button order)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM ticket_types WHERE guild_id = $1 AND removed_at IS NULL ORDER BY id;",
            guild_id,
        )
    return [dict(r) for r in rows]


async def get_type(pool: asyncpg.Pool, type_id: int) -> dict | None:
    """One ticket type by id, INCLUDING removed ones (check `removed_at`)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM ticket_types WHERE id = $1;", type_id)
    return _dict(row)


async def get_type_by_name(pool: asyncpg.Pool, guild_id: int, name: str) -> dict | None:
    """A live ticket type of this server by name, case-insensitively."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM ticket_types
            WHERE guild_id = $1 AND lower(name) = lower($2) AND removed_at IS NULL;
            """,
            guild_id,
            name,
        )
    return _dict(row)


async def create_type(
    pool: asyncpg.Pool,
    guild_id: int,
    *,
    name: str,
    emoji: str | None,
    title: str | None,
    body: str | None,
    created_by: int,
) -> dict:
    """Add a ticket type. Raises DuplicateTypeError if the name is taken."""
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO ticket_types (guild_id, name, emoji, title, body, created_by)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING *;
                """,
                guild_id,
                name,
                emoji,
                title,
                body,
                created_by,
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateTypeError(name) from exc
    logger.info("Created ticket type id=%s %r in guild=%s", row["id"], name, guild_id)
    return dict(row)


async def update_type(
    pool: asyncpg.Pool,
    type_id: int,
    *,
    name: str,
    emoji: str | None,
    title: str | None,
    body: str | None,
    image: str | None,
    thumbnail: str | None,
) -> dict | None:
    """Overwrite a live ticket type. Every value is the final one.

    Raises DuplicateTypeError if the new name belongs to another live type.
    """
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                """
                UPDATE ticket_types
                SET name = $2, emoji = $3, title = $4, body = $5, image = $6, thumbnail = $7
                WHERE id = $1 AND removed_at IS NULL
                RETURNING *;
                """,
                type_id,
                name,
                emoji,
                title,
                body,
                image,
                thumbnail,
            )
        except asyncpg.UniqueViolationError as exc:
            raise DuplicateTypeError(name) from exc
    return _dict(row)


async def remove_type(pool: asyncpg.Pool, guild_id: int, type_id: int) -> bool:
    """Soft-delete a ticket type. True if it was live in this server."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE ticket_types SET removed_at = NOW()
            WHERE id = $1 AND guild_id = $2 AND removed_at IS NULL;
            """,
            type_id,
            guild_id,
        )
    removed = result.endswith(" 1")
    if removed:
        logger.info("Removed ticket type id=%s from guild=%s", type_id, guild_id)
    return removed


# --- staff roles --------------------------------------------------------------

async def add_staff_role(pool: asyncpg.Pool, guild_id: int, role_id: int, added_by: int | None) -> bool:
    """True if added, False if it was already a staff role."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO ticket_staff_roles (guild_id, role_id, added_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (guild_id, role_id) DO NOTHING;
            """,
            guild_id,
            role_id,
            added_by,
        )
    return result.endswith(" 1")


async def remove_staff_role(pool: asyncpg.Pool, guild_id: int, role_id: int) -> bool:
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM ticket_staff_roles WHERE guild_id = $1 AND role_id = $2;", guild_id, role_id
        )
    return result.endswith(" 1")


async def list_staff_roles(pool: asyncpg.Pool, guild_id: int) -> list[int]:
    """A server's ticket staff role ids, oldest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role_id FROM ticket_staff_roles WHERE guild_id = $1 ORDER BY added_at, role_id;",
            guild_id,
        )
    return [r["role_id"] for r in rows]


# --- tickets ------------------------------------------------------------------

async def create_ticket(
    pool: asyncpg.Pool, guild_id: int, *, type_id: int, type_name: str, opener_id: int
) -> dict | None:
    """Claim a new open ticket, or None if this member already has one of this type.

    The partial unique index decides, so two simultaneous clicks can't both win.

    The ticket also gets the server's next ticket number, from FIRST_TICKET_NUMBER
    up. Locking the server's ticket_config row makes simultaneous opens take
    turns, so two members can't be handed the same number. A ticket whose open
    fails is deleted, so its number is reused rather than leaving a gap.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT 1 FROM ticket_config WHERE guild_id = $1 FOR UPDATE;", guild_id
            )
            row = await conn.fetchrow(
                """
                INSERT INTO tickets (guild_id, type_id, type_name, opener_id, number)
                VALUES (
                    $1, $2, $3, $4,
                    (SELECT COALESCE(MAX(number) + 1, $5) FROM tickets WHERE guild_id = $1)
                )
                ON CONFLICT (guild_id, opener_id, type_id) WHERE closed_at IS NULL DO NOTHING
                RETURNING *;
                """,
                guild_id,
                type_id,
                type_name,
                opener_id,
                FIRST_TICKET_NUMBER,
            )
    return _dict(row)


async def get_open_ticket(
    pool: asyncpg.Pool, guild_id: int, opener_id: int, type_id: int
) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT * FROM tickets
            WHERE guild_id = $1 AND opener_id = $2 AND type_id = $3 AND closed_at IS NULL;
            """,
            guild_id,
            opener_id,
            type_id,
        )
    return _dict(row)


async def get_ticket(pool: asyncpg.Pool, ticket_id: int) -> dict | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM tickets WHERE id = $1;", ticket_id)
    return _dict(row)


async def attach_channel(pool: asyncpg.Pool, ticket_id: int, channel_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute("UPDATE tickets SET channel_id = $2 WHERE id = $1;", ticket_id, channel_id)


async def delete_ticket(pool: asyncpg.Pool, ticket_id: int) -> None:
    """Roll back a ticket whose channel could never be created."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tickets WHERE id = $1;", ticket_id)


async def claim_close(pool: asyncpg.Pool, ticket_id: int, closed_by: int | None) -> dict | None:
    """Mark a ticket closed. Returns the row only for the caller that closed it.

    One atomic UPDATE, so two people pressing Close at once can't both go on to
    post a transcript and delete the channel.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE tickets SET closed_at = NOW(), closed_by = $2
            WHERE id = $1 AND closed_at IS NULL
            RETURNING *;
            """,
            ticket_id,
            closed_by,
        )
    return _dict(row)


async def reopen(pool: asyncpg.Pool, ticket_id: int) -> None:
    """Undo claim_close when the transcript couldn't be saved."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE tickets SET closed_at = NULL, closed_by = NULL WHERE id = $1;", ticket_id
        )


async def close_by_channel(pool: asyncpg.Pool, channel_id: int) -> dict | None:
    """Close the open ticket living in a channel that was just deleted, if any."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE tickets SET closed_at = NOW()
            WHERE channel_id = $1 AND closed_at IS NULL
            RETURNING *;
            """,
            channel_id,
        )
    return _dict(row)


async def count_open(pool: asyncpg.Pool, guild_id: int) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM tickets WHERE guild_id = $1 AND closed_at IS NULL;", guild_id
        )
