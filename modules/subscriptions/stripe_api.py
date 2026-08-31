"""Read-only Stripe lookups — the only module that imports `stripe`.

Used for two things: listing recent subscriptions so an admin can pick the one
matching a buyer when confirming a payment, and re-checking stored
subscriptions on a schedule so cancellations surface on their own.

**Pulling, not receiving.** There is no Stripe webhook — nothing of ours is
publicly reachable — so the bot asks Stripe rather than being told. That means
status is only as fresh as the last lookup, never real-time.

Two rules for anyone adding a call here, both learned the hard way:

  1. **The Stripe SDK is synchronous.** Wrap every call in
     `asyncio.to_thread(...)` or it blocks the bot's event loop.
  2. **SDK objects are `StripeObject`, not dicts.** `.get()` on one raises
     AttributeError ("'get' is a dict method, but a Subscription is not a
     dict"). Call `.to_dict()` first — which is what `_as_dict` below is for.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import stripe

from config.settings import settings

logger = logging.getLogger(__name__)

# How many subscriptions to offer when an admin is picking one. Discord caps a
# select menu at 25 options, so this stays under it.
RECENT_SUBSCRIPTION_LIMIT = 20

# Statuses a subscription can never leave, so there's no point re-checking them.
TERMINAL_STATUSES = ("canceled", "incomplete_expired")


class StripeNotConfiguredError(Exception):
    """STRIPE_SECRET_KEY is empty, so Stripe lookups are unavailable."""


class StripeApiError(Exception):
    """Stripe rejected the request or was unreachable."""


@dataclass(frozen=True)
class SubscriptionSummary:
    """A Stripe subscription flattened for display — the Cog never sees SDK types."""

    subscription_id: str
    customer_id: str
    name: str | None
    email: str | None
    status: str
    amount_cents: int | None
    currency: str
    created: datetime | None
    current_period_end: datetime | None

    def label(self) -> str:
        """A one-line description for a select menu option."""
        who = self.name or self.email or self.customer_id
        parts = [who]
        if self.amount_cents is not None:
            parts.append(f"${self.amount_cents / 100:.2f}")
        if self.created:
            parts.append(self.created.strftime("%Y-%m-%d"))
        return " — ".join(parts)


def _require_key() -> None:
    if not settings.STRIPE_SECRET_KEY:
        raise StripeNotConfiguredError(
            "STRIPE_SECRET_KEY isn't set, so Stripe lookups are disabled."
        )
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _as_dict(obj) -> dict:
    """StripeObject -> plain dict. See rule 2 in the module docstring."""
    return obj.to_dict() if hasattr(obj, "to_dict") else dict(obj)


def _epoch(value) -> datetime | None:
    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=timezone.utc)


def _summarize(raw) -> SubscriptionSummary:
    data = _as_dict(raw)

    customer = data.get("customer")
    if isinstance(customer, str):
        customer_id, name, email = customer, None, None
    else:
        # Expanded via expand=["data.customer"].
        customer_data = _as_dict(customer) if customer else {}
        customer_id = customer_data.get("id", "")
        name = customer_data.get("name")
        email = customer_data.get("email")

    amount_cents = None
    currency = "usd"
    items = data.get("items") or {}
    item_list = _as_dict(items).get("data") or []
    if item_list:
        price = _as_dict(item_list[0]).get("price") or {}
        price_data = _as_dict(price)
        amount_cents = price_data.get("unit_amount")
        currency = price_data.get("currency") or currency

    return SubscriptionSummary(
        subscription_id=data.get("id", ""),
        customer_id=customer_id or "",
        name=name,
        email=email,
        status=data.get("status", "unknown"),
        amount_cents=amount_cents,
        currency=currency,
        created=_epoch(data.get("created")),
        current_period_end=_epoch(data.get("current_period_end")),
    )


async def list_recent_subscriptions(
    limit: int = RECENT_SUBSCRIPTION_LIMIT,
) -> list[SubscriptionSummary]:
    """Recent active subscriptions, newest first, for an admin to pick from.

    The customer is expanded so the buyer's name and email are available
    without a second round trip per subscription.
    """
    _require_key()
    try:
        result = await asyncio.to_thread(
            stripe.Subscription.list,
            status="active",
            limit=limit,
            expand=["data.customer"],
        )
    except stripe.error.StripeError as exc:
        raise StripeApiError(str(exc)) from exc

    return [_summarize(item) for item in _as_dict(result).get("data", [])]


async def get_subscription(subscription_id: str) -> SubscriptionSummary:
    """Current state of one subscription, for the refresh job."""
    _require_key()
    try:
        result = await asyncio.to_thread(
            stripe.Subscription.retrieve, subscription_id, expand=["customer"]
        )
    except stripe.error.StripeError as exc:
        raise StripeApiError(str(exc)) from exc

    return _summarize(result)


__all__ = [
    "RECENT_SUBSCRIPTION_LIMIT",
    "TERMINAL_STATUSES",
    "StripeApiError",
    "StripeNotConfiguredError",
    "SubscriptionSummary",
    "get_subscription",
    "list_recent_subscriptions",
]
