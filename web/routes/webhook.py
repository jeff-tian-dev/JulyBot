"""POST /webhook/stripe — verify signature, dispatch to modules/subscriptions/storage.py.

Reads the raw request body BEFORE any JSON parsing — Stripe's signature check
(stripe.Webhook.construct_event) needs the exact bytes Stripe sent, and a
FastAPI dependency that parses JSON first would consume/alter the body.

Only checkout.session.completed is handled — Checkout runs in mode="payment"
(one-time purchase, repurchased monthly by the buyer, not auto-renewed by
Stripe), so no customer.subscription.* events ever fire for this flow. Every
other event type Stripe sends (charge.succeeded, invoice.paid,
customer.subscription.created from the underlying PaymentIntent/Customer
objects, etc.) is acknowledged with 200 and otherwise ignored, same as any
unhandled type.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request, Response

from modules.subscriptions import storage
from web import stripe_client

logger = logging.getLogger(__name__)

router = APIRouter()

HANDLED_EVENT_TYPES = ("checkout.session.completed",)


@router.post("/webhook/stripe")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    try:
        event = await stripe_client.construct_webhook_event(payload=payload, sig_header=sig_header)
    except stripe_client.WebhookSignatureError:
        logger.warning("Rejected Stripe webhook with bad signature")
        return Response(status_code=400)

    pool = request.app.state.pool
    # Stripe's SDK returns StripeObject instances (e.g. Session), not plain
    # dicts — they support subscript access (data["x"]) but NOT .get(), which
    # raises AttributeError ("'get' is a dict method, but a Session is not a
    # dict"). Converting to a plain dict up front lets the rest of this
    # function use normal dict semantics safely.
    data = event["data"]["object"].to_dict()

    if event["type"] == "checkout.session.completed":
        metadata = data.get("metadata") or {}
        customer_details = data.get("customer_details") or {}
        await storage.upsert_from_checkout(
            pool,
            stripe_customer_id=data["customer"],
            stripe_checkout_session_id=data["id"],
            tier=metadata.get("tier", "unknown"),
            email=customer_details.get("email") or data.get("customer_email") or "",
            discord_username_hint=metadata.get("discord_username"),
            status=data["payment_status"],
        )
        logger.info("Checkout completed for session %s", data["id"])

    else:
        # Not an error — Stripe retries on non-2xx, so unhandled event types
        # are acknowledged rather than rejected.
        logger.debug("Ignoring unhandled Stripe event type %s", event["type"])

    return Response(status_code=200)
