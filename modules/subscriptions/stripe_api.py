"""Read-only Stripe lookups — the only module that imports `stripe`.

Used for two things: listing recent purchases so an admin can pick the one
matching a buyer when confirming a payment, and re-checking stored recurring
subscriptions on a schedule so cancellations surface on their own.

**Both Payment Link types are supported**, because which one is in use is a
Stripe Dashboard setting the bot can't see and has already been switched once:

  - A **recurring** link creates a Subscription, which has a lifecycle
    (active -> past_due -> canceled) worth re-polling.
  - A **one-time** link creates only a PaymentIntent. There is no Subscription
    object at all, so `Subscription.list` returns nothing for it — which is
    exactly what "Stripe has no active subscriptions to link" meant. A payment
    is `succeeded` forever; there is no renewal or cancellation to observe, so
    the refresh job skips these rather than pointlessly re-fetching them.

`SubscriptionSummary.kind` says which one a row is. Don't assume a summary has
a `current_period_end` — one-time payments have none.

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

# How many purchases to offer when an admin is picking one. Discord caps a
# select menu at 25 options, so this stays under it. Both sources are fetched
# at this limit and then merged, so the combined list is trimmed back down.
RECENT_SUBSCRIPTION_LIMIT = 20

# Statuses a subscription can never leave, so there's no point re-checking them.
TERMINAL_STATUSES = ("canceled", "incomplete_expired")

# What produced a purchase in Stripe. A recurring Payment Link makes a
# Subscription; a one-time link makes only a PaymentIntent.
KIND_SUBSCRIPTION = "subscription"
KIND_PAYMENT = "payment"

# A successful one-time payment. Unlike a subscription status this never
# changes, which is why these are excluded from the refresh job.
PAYMENT_SUCCEEDED = "succeeded"


class StripeNotConfiguredError(Exception):
    """STRIPE_SECRET_KEY is empty, so Stripe lookups are unavailable."""


class StripeApiError(Exception):
    """Stripe rejected the request or was unreachable."""


@dataclass(frozen=True)
class SubscriptionSummary:
    """A Stripe purchase flattened for display — the Cog never sees SDK types.

    Covers both a recurring Subscription and a one-time PaymentIntent; `kind`
    says which. `subscription_id` holds whichever id identifies it (a `sub_…`
    or a `pi_…`), so the rest of the code stores and looks it up the same way
    regardless. `current_period_end` is None for a one-time payment.
    """

    subscription_id: str
    customer_id: str
    name: str | None
    email: str | None
    status: str
    amount_cents: int | None
    currency: str
    created: datetime | None
    current_period_end: datetime | None
    kind: str = KIND_SUBSCRIPTION

    @property
    def is_one_time(self) -> bool:
        return self.kind == KIND_PAYMENT

    def label(self) -> str:
        """A one-line description for a select menu option."""
        who = self.name or self.email or self.customer_id
        parts = [who]
        if self.amount_cents is not None:
            parts.append(f"${self.amount_cents / 100:.2f}")
        if self.created:
            parts.append(self.created.strftime("%Y-%m-%d"))
        # Both kinds can appear in one dropdown, and they mean different things
        # for how long access lasts, so the admin needs to see which is which.
        parts.append("one-time" if self.is_one_time else "monthly")
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
        kind=KIND_SUBSCRIPTION,
    )


def _customer_fields(data: dict) -> tuple[str, str | None, str | None]:
    """(id, name, email) from a `customer` field that may be expanded or not."""
    customer = data.get("customer")
    if isinstance(customer, str):
        return customer, None, None
    customer_data = _as_dict(customer) if customer else {}
    return (
        customer_data.get("id", "") or "",
        customer_data.get("name"),
        customer_data.get("email"),
    )


def _summarize_payment(raw) -> SubscriptionSummary:
    """A one-time PaymentIntent, flattened into the same shape.

    Shape differs from a Subscription: the amount is top-level rather than on a
    price inside items[], and there is no period to end. A guest checkout can
    also leave `customer` unset entirely, in which case the receipt email on
    the intent is the only identifying detail — hence the fallbacks.
    """
    data = _as_dict(raw)
    customer_id, name, email = _customer_fields(data)

    if not email:
        email = data.get("receipt_email")
    if not name or not email:
        # Payment Links put the buyer's details on the charge. Modern API
        # versions expose `latest_charge` (expanded below); `charges.data` is
        # the pre-2022 shape, still read so an older account keeps working.
        charge = data.get("latest_charge")
        if isinstance(charge, str):
            charge = None  # Unexpanded: just an id, nothing to read off it.
        if charge is None:
            charges = _as_dict(data.get("charges") or {}).get("data") or []
            charge = charges[0] if charges else None
        if charge is not None:
            billing = _as_dict(_as_dict(charge).get("billing_details") or {})
            name = name or billing.get("name")
            email = email or billing.get("email")

    return SubscriptionSummary(
        subscription_id=data.get("id", ""),
        customer_id=customer_id,
        name=name,
        email=email,
        status=data.get("status", "unknown"),
        amount_cents=data.get("amount_received") or data.get("amount"),
        currency=data.get("currency") or "usd",
        created=_epoch(data.get("created")),
        current_period_end=None,
        kind=KIND_PAYMENT,
    )


async def _list_active_subscriptions(limit: int) -> list[SubscriptionSummary]:
    """Recurring subscriptions that are currently active."""
    result = await asyncio.to_thread(
        stripe.Subscription.list,
        status="active",
        limit=limit,
        expand=["data.customer"],
    )
    return [_summarize(item) for item in _as_dict(result).get("data", [])]


async def _list_successful_payments(limit: int) -> list[SubscriptionSummary]:
    """One-time payments that went through.

    PaymentIntent.list has no status filter, so unsuccessful attempts are
    filtered out here — an abandoned or failed checkout must never be offered
    as something to confirm.
    """
    result = await asyncio.to_thread(
        stripe.PaymentIntent.list,
        limit=limit,
        # latest_charge carries billing_details, the only identifying info on a
        # guest checkout with no customer object.
        expand=["data.customer", "data.latest_charge"],
    )
    payments = [_summarize_payment(item) for item in _as_dict(result).get("data", [])]
    return [p for p in payments if p.status == PAYMENT_SUCCEEDED]


async def list_recent_subscriptions(
    limit: int = RECENT_SUBSCRIPTION_LIMIT,
) -> list[SubscriptionSummary]:
    """Recent purchases an admin can link to a buyer, newest first.

    Merges both Payment Link types — active recurring subscriptions and
    successful one-time payments — because which kind is in use is a Dashboard
    setting the bot can't see, and it has already changed once. Listing only
    subscriptions is what made a real one-time purchase look like "Stripe has
    no active subscriptions to link".

    **One source failing does not empty the dropdown.** If subscriptions load
    but payments error (or vice versa), the successful half is still offered
    and the failure is logged; an admin with a real purchase in front of them
    can still confirm it. Only both failing raises.
    """
    _require_key()

    results = await asyncio.gather(
        _list_active_subscriptions(limit),
        _list_successful_payments(limit),
        return_exceptions=True,
    )

    merged: list[SubscriptionSummary] = []
    failures: list[BaseException] = []
    for source, outcome in zip(("subscriptions", "payments"), results):
        if isinstance(outcome, BaseException):
            failures.append(outcome)
            logger.warning("Couldn't list Stripe %s: %s", source, outcome)
        else:
            merged.extend(outcome)

    if failures and not merged:
        raise StripeApiError(str(failures[0])) from failures[0]

    # Newest first across both kinds; a summary with no timestamp sorts last
    # rather than crashing the comparison.
    merged.sort(key=lambda s: s.created or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return merged[:limit]


def is_one_time_id(stripe_id: str) -> bool:
    """Whether a stored id refers to a one-time payment rather than a subscription.

    Stripe ids are prefixed by object type (`sub_…` vs `pi_…`), which is what
    lets a stored row be routed to the right endpoint without a `kind` column.
    """
    return stripe_id.startswith("pi_")


async def get_subscription(subscription_id: str) -> SubscriptionSummary:
    """Current state of one purchase, for the refresh job.

    Routes on the id prefix: a one-time payment is a PaymentIntent and would
    404 against Subscription.retrieve.
    """
    _require_key()
    one_time = is_one_time_id(subscription_id)
    retrieve = stripe.PaymentIntent.retrieve if one_time else stripe.Subscription.retrieve
    expand = ["customer", "latest_charge"] if one_time else ["customer"]
    try:
        result = await asyncio.to_thread(retrieve, subscription_id, expand=expand)
    except stripe.error.StripeError as exc:
        raise StripeApiError(str(exc)) from exc

    return _summarize_payment(result) if one_time else _summarize(result)


__all__ = [
    "KIND_PAYMENT",
    "KIND_SUBSCRIPTION",
    "PAYMENT_SUCCEEDED",
    "RECENT_SUBSCRIPTION_LIMIT",
    "TERMINAL_STATUSES",
    "StripeApiError",
    "StripeNotConfiguredError",
    "SubscriptionSummary",
    "get_subscription",
    "is_one_time_id",
    "list_recent_subscriptions",
]
