"""Thin wrapper around the `stripe` SDK — the only module that imports it.

The official Stripe Python SDK is synchronous (blocking network calls), so
every call here is wrapped in asyncio.to_thread(...) to avoid stalling the
FastAPI event loop. Anyone adding a new Stripe call must do the same.

Kept separate from routes/ so tests can patch these functions directly
(matching this repo's existing convention of patch.object(module, "fn",
AsyncMock(...)) for external HTTP calls, e.g. modules/ranked_tracker/poller.py).
"""
from __future__ import annotations

import asyncio
import logging

import stripe

from config.settings import settings

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


class WebhookSignatureError(Exception):
    """Raised when a webhook request's Stripe-Signature header doesn't verify."""


async def create_checkout_session(
    *,
    price_id: str,
    success_url: str,
    cancel_url: str,
    metadata: dict[str, str],
) -> stripe.checkout.Session:
    """Create a one-time-payment Stripe Checkout Session.

    mode="payment", not mode="subscription" — access is repurchased monthly
    by the buyer's own action, not auto-renewed by Stripe (see CLAUDE.md).
    price_id must reference a one-time Stripe Price; Stripe rejects a
    recurring Price in mode="payment".

    Stripe collects the buyer's email itself as part of Checkout; we don't
    ask for it on our own pricing page.
    """
    return await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="payment",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata=metadata,
    )


async def construct_webhook_event(*, payload: bytes, sig_header: str) -> stripe.Event:
    """Verify and parse a Stripe webhook request body.

    Raises WebhookSignatureError (not stripe's own exception type) so callers
    in routes/webhook.py don't need to import `stripe` themselves.
    """
    try:
        return await asyncio.to_thread(
            stripe.Webhook.construct_event,
            payload,
            sig_header,
            settings.STRIPE_WEBHOOK_SECRET,
        )
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise WebhookSignatureError(str(exc)) from exc


async def retrieve_subscription(subscription_id: str) -> stripe.Subscription:
    return await asyncio.to_thread(stripe.Subscription.retrieve, subscription_id)


__all__ = [
    "WebhookSignatureError",
    "construct_webhook_event",
    "create_checkout_session",
    "retrieve_subscription",
]
