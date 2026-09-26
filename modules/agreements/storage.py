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
    agreement_text: str = "",
    product_id: int | None = None,
    product_name: str | None = None,
) -> asyncpg.Record:
    """Insert a pending purchase, before the status message is posted.

    The row exists first because the message carries a *persistent* view whose
    custom_ids need an agreement id to encode. `attach_message` fills in
    message_id once the send succeeds.

    `agreement_text` defaults to empty: terms are accepted in Stripe Checkout,
    so there is no in-Discord signature to capture and nothing to transcribe.
    The column is NOT NULL, hence "" rather than None. Historical rows hold the
    full T&C text and must keep rendering.

    The payment columns (payer_name / payment_method / payment_contact) stay
    NULL — Stripe knows who paid. See the agreements table comment in
    database/models.py.

    `product_id` / `product_name` are set for a `/product sell` purchase and
    NULL for a tier purchase. The name is a snapshot, so the row still says
    what was sold after the product is removed.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO agreements (
                guild_id, channel_id, buyer_id, sent_by, agreement_text,
                product_id, product_name
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *;
            """,
            guild_id,
            channel_id,
            buyer_id,
            sent_by,
            agreement_text,
            product_id,
            product_name,
        )


async def attach_message(pool: asyncpg.Pool, agreement_id: int, message_id: int) -> None:
    """Record which message an agreement was published as."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE agreements SET message_id = $2 WHERE id = $1;",
            agreement_id,
            message_id,
        )


async def set_payer_name(
    pool: asyncpg.Pool, agreement_id: int, payer_name: str
) -> asyncpg.Record | None:
    """Record the buyer's real name on the agreement.

    Backfills the column the retired moderator flow left behind, using the name
    on the Stripe customer record (or one an admin supplied when Stripe had
    none). receipt_text and lookup_embed already render payer_name when it's
    present, so this is what puts a real person on a dispute document rather
    than only a Discord ID.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "UPDATE agreements SET payer_name = $2 WHERE id = $1 RETURNING *;",
            agreement_id,
            payer_name,
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

    Used by `/agreement record` — a buyer paying outside Stripe never sees
    Stripe's checkout, so signing here is the ONLY consent record for them.
    `/subscribe` (Stripe) has no I Agree button; Stripe collects it instead.
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


async def create_manual_agreement(
    pool: asyncpg.Pool,
    *,
    guild_id: int,
    channel_id: int,
    buyer_id: int,
    sent_by: int,
    payment_method: str,
    payer_name: str,
    amount_cents: int,
    agreement_text: str,
) -> asyncpg.Record:
    """Open a purchase paid outside Stripe, for the buyer to sign.

    Unlike `create_pending_agreement` (the Stripe path) this stores the payment
    columns the retired moderator flow used, because for these rows nothing
    else records how the buyer paid — Stripe has never heard of them.

    `agreement_text` is stored verbatim so the row is evidence of exactly what
    the buyer was shown, which for a non-Stripe buyer exists nowhere else.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO agreements (
                guild_id, channel_id, buyer_id, sent_by,
                payment_method, payer_name, amount_cents, agreement_text
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *;
            """,
            guild_id,
            channel_id,
            buyer_id,
            sent_by,
            payment_method,
            payer_name,
            amount_cents,
            agreement_text,
        )


async def confirm_agreement(
    pool: asyncpg.Pool, agreement_id: int, *, confirmed_by: int
) -> asyncpg.Record | None:
    """Link this purchase to a Stripe subscription an admin picked; None if not eligible.

    The subscription's existence and status come from Stripe's API, so this is
    verification rather than attestation. What stays human judgement is WHICH
    subscription belongs to WHICH Discord user — Stripe doesn't know its
    customers' Discord accounts. confirmed_by records who made that call.

    The guards live in SQL rather than only in the handler: confirmed_at IS NULL
    makes a double-click a no-op, and voided_at IS NULL stops confirming a
    cancelled purchase. There is deliberately NO signed_at guard — terms are now
    accepted in Stripe Checkout, so no in-Discord signature exists to require.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE agreements
            SET confirmed_at = NOW(), confirmed_by = $2
            WHERE id = $1
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

    `payment_method` comes back so the caller can tell a Stripe purchase from a
    manually-recorded one — their buttons use different custom_ids, so the
    wrong view class would leave the message's buttons dead after a restart.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, buyer_id, signed_at, payment_method, amount_cents,
                   payer_name, product_id, product_name
              FROM agreements
            WHERE message_id IS NOT NULL AND voided_at IS NULL AND confirmed_at IS NULL
            ORDER BY id;
            """
        )


