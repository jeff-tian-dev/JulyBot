"""Persistence for /agreement — purchase agreements and their signed state.

Raw asyncpg by design. Every function takes the pool first so it stays callable
from tests with a mocked pool and no running bot.
"""
from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger(__name__)


async def create_pending_agreement(
    pool: asyncpg.Pool,
    *,
    guild_id: int,
    channel_id: int,
    buyer_id: int,
    sent_by: int,
    agreement_text: str,
) -> asyncpg.Record:
    """Insert an unsigned agreement, before the status message is posted.

    The row exists first because the message carries a *persistent* view whose
    custom_ids need an agreement id to encode. `attach_message` fills in
    message_id once the send succeeds.

    The payment columns (payer_name / payment_method / payment_contact) stay
    NULL — Stripe knows who paid. See the agreements table comment in
    database/models.py.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO agreements (guild_id, channel_id, buyer_id, sent_by, agreement_text)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *;
            """,
            guild_id,
            channel_id,
            buyer_id,
            sent_by,
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

    The buyer_id match stops anyone but the addressed buyer from signing, the
    signed_at IS NULL guard makes a double-click (or a race between two clicks) a
    no-op — only the first successful UPDATE returns a row — and the voided_at
    guard stops a buyer signing a purchase an admin cancelled while they had the
    message open.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE agreements
            SET signed_at = NOW()
            WHERE id = $1 AND buyer_id = $2 AND signed_at IS NULL AND voided_at IS NULL
            RETURNING *;
            """,
            agreement_id,
            buyer_id,
        )


async def confirm_agreement(
    pool: asyncpg.Pool, agreement_id: int, *, confirmed_by: int
) -> asyncpg.Record | None:
    """Stamp an admin's confirmation that they saw the payment; None if not eligible.

    This is an ATTESTATION, not a verified payment — there's no Stripe webhook,
    so the bot never observes the charge. The admin checked the Stripe Dashboard
    themselves.

    The guards live in SQL rather than only in the handler: signed_at NOT NULL
    stops confirming a purchase nobody agreed to, confirmed_at IS NULL makes a
    double-click a no-op, and voided_at IS NULL stops confirming a cancelled one.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE agreements
            SET confirmed_at = NOW(), confirmed_by = $2
            WHERE id = $1
              AND signed_at IS NOT NULL
              AND confirmed_at IS NULL
              AND voided_at IS NULL
            RETURNING *;
            """,
            agreement_id,
            confirmed_by,
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


async def list_views_to_restore(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Every posted purchase still awaiting action — the only ones needing live buttons.

    A confirmed or voided purchase is terminal: its message already shows the
    final state with every button disabled, so restoring a view for it would
    only waste a registration.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, buyer_id, signed_at FROM agreements
            WHERE message_id IS NOT NULL AND voided_at IS NULL AND confirmed_at IS NULL
            ORDER BY id;
            """
        )


