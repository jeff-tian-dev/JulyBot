"""Persistence for subscribers — who is subscribed and whether it's still live.

Raw asyncpg by design, pool-first, no Stripe imports here, so it stays testable
with a mocked pool and no running bot (see modules/agreements/storage.py for
the same pattern).

One row per subscription period: a resub creates a NEW row rather than
overwriting, so this table is a history. "Is this user subscribed?" means "do
they have a row with a live status?", not "is there a row for them".
"""
from __future__ import annotations

from datetime import datetime

import asyncpg

from modules.subscriptions.stripe_api import TERMINAL_STATUSES

# Statuses that mean the subscriber currently has access. Stripe's other live
# statuses (past_due, unpaid, incomplete) mean payment is in trouble, so they
# deliberately don't count as active.
ACTIVE_STATUSES = ("active", "trialing")


async def create_subscriber(
    pool: asyncpg.Pool,
    *,
    discord_id: int,
    guild_id: int,
    agreement_id: int | None,
    stripe_subscription_id: str,
    stripe_customer_id: str,
    payer_name: str | None,
    email: str | None,
    tier: str | None,
    status: str,
    current_period_end: datetime | None,
    linked_by: int,
) -> asyncpg.Record:
    """Record a confirmed subscription.

    Raises asyncpg.UniqueViolationError if this Stripe subscription is already
    linked to someone — deliberately loud, since silently re-attributing a
    payment to a second Discord user would be worse than failing.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            INSERT INTO subscribers (
                discord_id, guild_id, agreement_id, stripe_subscription_id,
                stripe_customer_id, payer_name, email, tier, status,
                current_period_end, linked_by
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING *;
            """,
            discord_id,
            guild_id,
            agreement_id,
            stripe_subscription_id,
            stripe_customer_id,
            payer_name,
            email,
            tier,
            status,
            current_period_end,
            linked_by,
        )


async def get_subscriber_by_stripe_id(
    pool: asyncpg.Pool, stripe_subscription_id: str
) -> asyncpg.Record | None:
    """The row for a Stripe subscription, if it's already linked to someone."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM subscribers WHERE stripe_subscription_id = $1;",
            stripe_subscription_id,
        )


async def list_subscribers_for_discord_id(
    pool: asyncpg.Pool, discord_id: int
) -> list[asyncpg.Record]:
    """Every subscription period for one Discord user, newest first."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT * FROM subscribers WHERE discord_id = $1 ORDER BY created_at DESC;",
            discord_id,
        )


async def list_active_subscribers(pool: asyncpg.Pool, guild_id: int) -> list[asyncpg.Record]:
    """Everyone with a currently-live subscription in a guild."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM subscribers
            WHERE guild_id = $1 AND status = ANY($2::text[])
            ORDER BY created_at DESC;
            """,
            guild_id,
            list(ACTIVE_STATUSES),
        )


async def list_for_refresh(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Subscriptions still worth re-checking against Stripe.

    Terminal ones are skipped — a canceled subscription never comes back, so
    re-polling it forever would just burn API calls.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, stripe_subscription_id, status FROM subscribers
            WHERE NOT (status = ANY($1::text[]))
            ORDER BY id;
            """,
            list(TERMINAL_STATUSES),
        )


async def update_subscriber_status(
    pool: asyncpg.Pool,
    stripe_subscription_id: str,
    *,
    status: str,
    current_period_end: datetime | None,
) -> asyncpg.Record | None:
    """Apply the latest state from Stripe. None if the row is gone."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE subscribers
            SET status = $2, current_period_end = $3, updated_at = NOW()
            WHERE stripe_subscription_id = $1
            RETURNING *;
            """,
            stripe_subscription_id,
            status,
            current_period_end,
        )
