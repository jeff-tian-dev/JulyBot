"""Persistence for Stripe purchases of subscriber access.

NOT CURRENTLY CALLED. Purchases go through Stripe Payment Links posted by
/subscribe, hosted entirely by Stripe — nothing in the bot observes them, so
the Stripe Dashboard is the authoritative record. This module is kept as the
foundation for a future pass that records purchases and grants Discord roles
automatically, which needs a publicly reachable webhook endpoint (see
CLAUDE.md). It is fully implemented and tested; only the caller is missing.

Raw asyncpg by design, pool-first, no Stripe or FastAPI imports — callable
with a mocked pool and no running bot (see modules/agreements/storage.py for
the same pattern). One row per purchase, not one row per customer.
"""
from __future__ import annotations

from datetime import datetime

import asyncpg


async def upsert_from_checkout(
    pool: asyncpg.Pool,
    *,
    stripe_customer_id: str,
    stripe_checkout_session_id: str,
    tier: str,
    email: str,
    discord_username_hint: str | None,
    status: str,
) -> asyncpg.Record:
    """Record a purchase from a completed Checkout Session.

    Upserts on stripe_checkout_session_id so a redelivered checkout.session.completed
    webhook re-applies the same state instead of erroring or duplicating a row.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO subscriptions (
                stripe_customer_id, stripe_checkout_session_id,
                tier, email, discord_username_hint, status
            )
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (stripe_checkout_session_id) DO UPDATE SET
                stripe_customer_id = EXCLUDED.stripe_customer_id,
                tier = EXCLUDED.tier,
                email = EXCLUDED.email,
                discord_username_hint = EXCLUDED.discord_username_hint,
                status = EXCLUDED.status,
                updated_at = NOW()
            RETURNING *;
            """,
            stripe_customer_id,
            stripe_checkout_session_id,
            tier,
            email,
            discord_username_hint,
            status,
        )


async def get_by_customer_id(pool: asyncpg.Pool, stripe_customer_id: str) -> list[asyncpg.Record]:
    """Every purchase for a Stripe customer, newest first."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM subscriptions WHERE stripe_customer_id = $1 ORDER BY created_at DESC;",
            stripe_customer_id,
        )


async def get_by_month(pool: asyncpg.Pool, start: datetime, end: datetime) -> list[asyncpg.Record]:
    """Every purchase in a half-open [start, end) window, oldest first.

    Callers pass the first-of-month and first-of-next-month; each purchase is
    its own row, so a calendar month's purchases are just every row whose
    created_at falls in that range.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM subscriptions WHERE created_at >= $1 AND created_at < $2 ORDER BY created_at;",
            start,
            end,
        )
