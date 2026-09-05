"""Re-check stored recurring subscriptions against Stripe on a schedule.

There is no Stripe webhook, so cancellations and failed renewals are never
pushed to the bot — this job pulls them. It only touches subscriptions already
linked to a Discord user, so the cost is proportional to the subscriber count
rather than to everything in the Stripe account.

**One-time payments are excluded** by `storage.list_for_refresh`, not here: a
successful payment is `succeeded` forever, with no renewal or cancellation to
observe, so re-fetching one would never change anything.

Consequence worth remembering: a status in the database is only as fresh as
the last run of this job, not real-time.
"""
from __future__ import annotations

import logging

import asyncpg

from modules.subscriptions import storage
from modules.subscriptions.stripe_api import (
    StripeApiError,
    StripeNotConfiguredError,
    get_subscription,
)

logger = logging.getLogger(__name__)


async def refresh_subscribers(pool: asyncpg.Pool) -> dict[str, int]:
    """Sync every non-terminal subscription's status from Stripe.

    A single failing subscription never aborts the run — one deleted or
    malformed id shouldn't stop the rest from being refreshed (the same
    per-row isolation poll_ranked_tracking uses).
    """
    try:
        rows = await storage.list_for_refresh(pool)
    except Exception:  # noqa: BLE001 — a scheduled job must not raise into APScheduler
        logger.exception("Couldn't load subscribers to refresh")
        return {"checked": 0, "changed": 0, "errors": 1}

    summary = {"checked": 0, "changed": 0, "errors": 0}

    for row in rows:
        subscription_id = row["stripe_subscription_id"]
        try:
            live = await get_subscription(subscription_id)
        except StripeNotConfiguredError:
            # The key was removed mid-run; nothing further will work either.
            logger.warning("Stripe key not configured; skipping subscriber refresh")
            return summary
        except StripeApiError as exc:
            logger.warning("Couldn't refresh subscription %s: %s", subscription_id, exc)
            summary["errors"] += 1
            continue
        except Exception:  # noqa: BLE001
            logger.exception("Unexpected error refreshing subscription %s", subscription_id)
            summary["errors"] += 1
            continue

        summary["checked"] += 1
        if live.status == row["status"]:
            continue

        try:
            await storage.update_subscriber_status(
                pool,
                subscription_id,
                status=live.status,
                current_period_end=live.current_period_end,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Couldn't store new status for subscription %s", subscription_id)
            summary["errors"] += 1
            continue

        summary["changed"] += 1
        logger.info(
            "Subscription %s status changed %s -> %s",
            subscription_id,
            row["status"],
            live.status,
        )

    return summary
