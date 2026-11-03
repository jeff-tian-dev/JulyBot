"""Read-only Stripe lookups — the only module that imports `stripe`.

Used for three things: mapping Payment Link URLs to their ids (so a server can
own the links on its product pages), listing the completed checkouts of a
server's own links so an admin can pick the one matching a buyer when
confirming a payment, and re-checking stored recurring subscriptions on a
schedule so cancellations surface on their own.

**Confirm only ever lists checkouts from the asking server's Payment Links**
(`list_checkouts`), never the whole Stripe account: several servers share one
account, and one server's admins must not see another's buyers. See
modules/subscriptions/payment_links.py.

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
# select menu at 25 options, so this stays under it. Each Payment Link is
# fetched at this limit and the merged list is trimmed back down.
RECENT_PURCHASE_LIMIT = 20

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
    # The Payment Link (plink_…) the purchase came through, when known — what
    # ties a payment to the product, and the product to a server.
    payment_link_id: str | None = None

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


def is_configured() -> bool:
    """Whether Stripe lookups are available at all (STRIPE_SECRET_KEY is set)."""
    return bool(settings.STRIPE_SECRET_KEY)


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


def _summarize_checkout(raw) -> SubscriptionSummary | None:
    """A completed Checkout Session, flattened. None when it isn't confirmable.

    Only a `paid` session counts: an unpaid or zero-amount checkout has nothing
    for an admin to confirm. The stored id is the Subscription a recurring link
    created, else the PaymentIntent a one-time link created, so the refresh job
    and the duplicate-link index work exactly as before. `customer_details`
    sits on the session itself, so a guest checkout still names the buyer.
    """
    data = _as_dict(raw)
    if data.get("payment_status") != "paid":
        return None

    recurring = data.get("mode") == "subscription"
    stripe_id = data.get("subscription") if recurring else data.get("payment_intent")
    if isinstance(stripe_id, dict):
        stripe_id = stripe_id.get("id")
    if not stripe_id:
        return None

    customer = data.get("customer")
    customer_id = customer if isinstance(customer, str) else _as_dict(customer or {}).get("id", "")
    details = _as_dict(data.get("customer_details") or {})
    link = data.get("payment_link")
    if isinstance(link, dict):
        link = link.get("id")

    return SubscriptionSummary(
        subscription_id=stripe_id,
        customer_id=customer_id or "",
        name=details.get("name"),
        email=details.get("email"),
        # A new subscription is live at checkout; the refresh job then tracks
        # its real status. A paid one-time payment is succeeded for good.
        status="active" if recurring else PAYMENT_SUCCEEDED,
        amount_cents=data.get("amount_total"),
        currency=data.get("currency") or "usd",
        created=_epoch(data.get("created")),
        current_period_end=None,
        kind=KIND_SUBSCRIPTION if recurring else KIND_PAYMENT,
        payment_link_id=link,
    )


async def list_payment_links() -> dict[str, str]:
    """{url: plink_id} for every Payment Link on the account.

    How a buy.stripe.com URL found on a product page is matched to the id that
    Checkout Sessions are filtered by. A URL that isn't in here belongs to some
    other Stripe account, and nothing of its can be looked up.
    """
    _require_key()

    def _collect() -> dict[str, str]:
        links: dict[str, str] = {}
        for item in stripe.PaymentLink.list(limit=100).auto_paging_iter():
            data = _as_dict(item)
            if data.get("url") and data.get("id"):
                links[data["url"]] = data["id"]
        return links

    try:
        return await asyncio.to_thread(_collect)
    except stripe.error.StripeError as exc:
        raise StripeApiError(str(exc)) from exc


async def _list_link_checkouts(payment_link_id: str, limit: int) -> list[SubscriptionSummary]:
    result = await asyncio.to_thread(
        stripe.checkout.Session.list,
        payment_link=payment_link_id,
        status="complete",
        limit=limit,
    )
    summaries = (_summarize_checkout(item) for item in _as_dict(result).get("data", []))
    return [s for s in summaries if s is not None]


async def list_checkouts(
    payment_link_ids, limit: int = RECENT_PURCHASE_LIMIT
) -> list[SubscriptionSummary]:
    """Recent paid checkouts made through the given Payment Links, newest first.

    **One link failing does not empty the dropdown.** The links that loaded are
    still offered and the failure is logged; only every link failing raises.
    """
    _require_key()
    ids = list(dict.fromkeys(payment_link_ids))
    if not ids:
        return []

    results = await asyncio.gather(
        *(_list_link_checkouts(link_id, limit) for link_id in ids),
        return_exceptions=True,
    )

    merged: list[SubscriptionSummary] = []
    failures: list[BaseException] = []
    for link_id, outcome in zip(ids, results):
        if isinstance(outcome, BaseException):
            failures.append(outcome)
            logger.warning("Couldn't list Stripe checkouts for %s: %s", link_id, outcome)
        else:
            merged.extend(outcome)

    if failures and not merged:
        raise StripeApiError(str(failures[0])) from failures[0]

    # A summary with no timestamp sorts last rather than crashing the comparison.
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
    "RECENT_PURCHASE_LIMIT",
    "TERMINAL_STATUSES",
    "StripeApiError",
    "StripeNotConfiguredError",
    "SubscriptionSummary",
    "get_subscription",
    "is_configured",
    "is_one_time_id",
    "list_checkouts",
    "list_payment_links",
]
