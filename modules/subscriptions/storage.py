"""Persistence for subscribers — who is subscribed and whether it's still live.

Raw asyncpg by design, pool-first, no Stripe imports here, so it stays testable
with a mocked pool and no running bot (see modules/agreements/storage.py for
the same pattern).

One row per subscription period: a resub creates a NEW row rather than
overwriting, so this table is a history. "Is this user subscribed?" means "do
they have a live, unarchived row?", not "is there a row for them".

**Nothing here deletes a row.** These are the purchase log and the evidence for
a payment dispute. Ending a month archives (`archived_at`), which drops a row
out of the active list while leaving it fully readable.
"""
from __future__ import annotations

from datetime import datetime

import asyncpg

from modules.subscriptions.stripe_api import TERMINAL_STATUSES

# Statuses that mean the payment is in good standing. Stripe's other live
# subscription statuses (past_due, unpaid, incomplete) mean payment is in
# trouble, so they deliberately don't count.
#
# "succeeded" is a one-time PaymentIntent, not a subscription — without it a
# one-time purchase could never count as active, which is what the Payment
# Links currently produce. It stays `succeeded` forever, so for those rows
# access is bounded by ARCHIVING, not by the status ever changing.
ACTIVE_STATUSES = ("active", "trialing", "succeeded")


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
    """Everyone with current access in a guild.

    Two conditions, and both are needed. The status check catches a recurring
    subscription that Stripe has since cancelled; the archive check is what
    bounds a ONE-TIME purchase, whose status stays `succeeded` forever and so
    would otherwise grant access permanently.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM subscribers
            WHERE guild_id = $1
              AND status = ANY($2::text[])
              AND archived_at IS NULL
            ORDER BY created_at DESC;
            """,
            guild_id,
            list(ACTIVE_STATUSES),
        )


async def archive_subscribers(
    pool: asyncpg.Pool, guild_id: int, *, before: datetime, archived_by: int
) -> list[asyncpg.Record]:
    """Close out purchases made before `before`; returns what was archived.

    Ends a month: last month's purchases stop counting as current access so the
    active list is only this month's. **Nothing is deleted** — these rows are
    the purchase log and dispute evidence, and a resub already creates a new row
    rather than reusing one.

    Only touches rows not already archived, so re-running it is a no-op rather
    than restamping the timestamp and losing when the close-out actually
    happened. Returning the rows lets the caller report what changed.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            UPDATE subscribers
            SET archived_at = NOW(), archived_by = $3, updated_at = NOW()
            WHERE guild_id = $1
              AND created_at < $2
              AND archived_at IS NULL
            RETURNING *;
            """,
            guild_id,
            before,
            archived_by,
        )


async def unarchive_subscriber(
    pool: asyncpg.Pool, subscriber_id: int
) -> asyncpg.Record | None:
    """Undo an archive on one row, for a close-out run too early or by mistake."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE subscribers
            SET archived_at = NULL, archived_by = NULL, updated_at = NOW()
            WHERE id = $1
            RETURNING *;
            """,
            subscriber_id,
        )


async def list_for_refresh(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    """Purchases still worth re-checking against Stripe.

    Two exclusions, both to avoid burning API calls on rows whose status can
    never change again:
      - Terminal subscriptions — a canceled one never comes back.
      - One-time payments (`pi_…` ids). A successful payment stays `succeeded`
        forever; there is no renewal or cancellation to observe. Recurring
        subscription ids start `sub_…`, so the prefix is enough to tell them
        apart without a `kind` column.
      - Archived rows — a closed-out month is not current access, so its
        status no longer drives anything.
    """
    async with pool.acquire() as conn:
        return await conn.fetch(
            # Raw string: the backslash is SQL's LIKE escape for a literal
            # underscore, not a Python escape.
            r"""
            SELECT id, stripe_subscription_id, status FROM subscribers
            WHERE NOT (status = ANY($1::text[]))
              AND stripe_subscription_id NOT LIKE 'pi\_%'
              AND archived_at IS NULL
            ORDER BY id;
            """,
            list(TERMINAL_STATUSES),
        )


async def get_subscriber(pool: asyncpg.Pool, subscriber_id: int) -> asyncpg.Record | None:
    """One subscriber row by its own id, for /purchases."""
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM subscribers WHERE id = $1;", subscriber_id)


async def list_recent_subscribers(
    pool: asyncpg.Pool, guild_id: int, limit: int = 25
) -> list[asyncpg.Record]:
    """Recent purchases in this guild, newest first."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM subscribers
            WHERE guild_id = $1
            ORDER BY created_at DESC
            LIMIT $2;
            """,
            guild_id,
            limit,
        )


async def linked_stripe_ids(pool: asyncpg.Pool) -> set[str]:
    """Every Stripe id already attached to a subscriber row.

    Used to grey out payments in the relink dropdown that are already someone
    else's — the unique index would reject them anyway, so catching it before
    the admin picks saves a confusing failure.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT stripe_subscription_id FROM subscribers;")
    return {row["stripe_subscription_id"] for row in rows}


async def relink_subscriber(
    pool: asyncpg.Pool,
    subscriber_id: int,
    *,
    stripe_subscription_id: str,
    stripe_customer_id: str,
    payer_name: str | None,
    email: str | None,
    status: str,
    current_period_end: datetime | None,
    relinked_by: int,
) -> asyncpg.Record | None:
    """Repoint a row at a different Stripe payment. None if the row is gone.

    Fixes the one mistake this flow makes: an admin picking the wrong payment
    out of the Stripe dropdown. The Discord buyer is NOT changed — `/subscribe`
    names them up front, so the buyer is the part that was already right.

    Everything Stripe-derived is overwritten together (customer, payer name,
    email, status, period end), because a half-updated row would carry one
    payment's id alongside another's payer name — worse than the mislink.

    `relinked_by` and `relinked_at` are kept rather than just overwriting
    `linked_by`: a correction is exactly the moment you want the history of who
    touched the row. Raises asyncpg.UniqueViolationError if the target payment
    is already linked to a different row, same as create_subscriber.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE subscribers
            SET stripe_subscription_id = $2,
                stripe_customer_id = $3,
                payer_name = $4,
                email = $5,
                status = $6,
                current_period_end = $7,
                relinked_by = $8,
                relinked_at = NOW(),
                updated_at = NOW()
            WHERE id = $1
            RETURNING *;
            """,
            subscriber_id,
            stripe_subscription_id,
            stripe_customer_id,
            payer_name,
            email,
            status,
            current_period_end,
            relinked_by,
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
