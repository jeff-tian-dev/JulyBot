"""Persistence for /agreement — purchase agreements and their signed state.

Raw asyncpg by design. Every function takes the pool first so it stays callable
from tests with a mocked pool and no running bot.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


async def create_signed_agreement(
    pool: asyncpg.Pool,
    *,
    guild_id: int,
    buyer_id: int,
    agreement_text: str,
) -> asyncpg.Record:
    """Insert an already-signed agreement for a self-serve /subscribe buyer.

    One statement rather than create-then-sign: that split existed because a
    moderator created the row before the buyer signed it, leaving a pending
    window. Here the row only exists because the buyer just clicked I Agree,
    so there is nothing to wait for.

    The payment columns (payer_name / payment_method / payment_contact) and
    sent_by / channel_id / message_id are left NULL — Stripe knows who paid,
    nobody sent this, and the whole flow is ephemeral. See the agreements
    table comment in database/models.py.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO agreements (guild_id, buyer_id, agreement_text, signed_at)
            VALUES ($1, $2, $3, NOW())
            RETURNING *;
            """,
            guild_id,
            buyer_id,
            agreement_text,
        )


async def create_agreement(
    pool: asyncpg.Pool,
    *,
    guild_id: int,
    channel_id: int,
    buyer_id: int,
    sent_by: int,
    payer_name: str,
    payment_method: str,
    payment_contact: str,
    order_ref: str | None,
    agreement_text: str,
) -> asyncpg.Record:
    """Insert an agreement before it's sent; message_id is attached afterwards."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO agreements (
                guild_id, channel_id, buyer_id, sent_by,
                payer_name, payment_method, payment_contact, order_ref, agreement_text
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *;
            """,
            guild_id,
            channel_id,
            buyer_id,
            sent_by,
            payer_name,
            payment_method,
            payment_contact,
            order_ref,
            agreement_text,
        )


async def attach_message(pool: asyncpg.Pool, agreement_id: int, message_id: int) -> None:
    """Record which message an agreement was published as."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agreements SET message_id = $2 WHERE id = $1;",
            agreement_id,
            message_id,
        )


async def delete_agreement(pool: asyncpg.Pool, agreement_id: int) -> None:
    """Remove an agreement (used to roll back when the send fails)."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM agreements WHERE id = $1;", agreement_id)


async def get_agreement(pool: asyncpg.Pool, agreement_id: int) -> asyncpg.Record | None:
    """Fetch an agreement by its primary key. None if it's gone."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM agreements WHERE id = $1;", agreement_id)


async def sign_agreement(
    pool: asyncpg.Pool, agreement_id: int, buyer_id: int
) -> asyncpg.Record | None:
    """Mark an agreement signed by the addressed buyer; None if not eligible.

    The buyer_id match stops anyone but the addressed buyer from signing, and the
    signed_at IS NULL guard makes a double-click (or a race between two clicks) a
    no-op — only the first successful UPDATE returns a row.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE agreements
            SET signed_at = NOW()
            WHERE id = $1 AND buyer_id = $2 AND signed_at IS NULL
            RETURNING *;
            """,
            agreement_id,
            buyer_id,
        )


async def void_agreement(
    pool: asyncpg.Pool, agreement_id: int, *, voided_by: int, reason: str
) -> asyncpg.Record | None:
    """Mark an agreement voided with a reason. Never touches the signed fields."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE agreements
            SET voided_at = NOW(), voided_by = $2, void_reason = $3
            WHERE id = $1
            RETURNING *;
            """,
            agreement_id,
            voided_by,
            reason,
        )


async def list_agreements_for_buyer(
    pool: asyncpg.Pool, buyer_id: int
) -> list[asyncpg.Record]:
    """Every agreement sent to a buyer, newest first."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM agreements WHERE buyer_id = $1 ORDER BY created_at DESC;",
            buyer_id,
        )


