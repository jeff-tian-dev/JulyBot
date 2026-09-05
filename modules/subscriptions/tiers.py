"""Subscription tier definitions surfaced by /subscribe.

Display copy lives here as a plain constant; the Stripe Payment Link per tier
comes from config/settings.py (.env), matching this repo's convention of
settings.py for per-deployment values and module-level constants for
structural, code-adjacent config. Adding or renaming a tier means an .env
addition plus a small change here — accepted given the tier count is small
and fixed.

These are ONE-TIME Stripe purchases — each buys a month of access and does
NOT auto-renew. The buyer repurchases when it lapses, matching the ticket
workflow the server already runs.

The billing model is set per Payment Link in the Stripe Dashboard, which the
bot cannot see, and it has been switched more than once. **If it changes back
to recurring, the copy here and in build_subscribe_embed / status_embed must
change in the same commit** — telling a buyer they won't be re-billed when
they will is exactly what produces chargebacks. See CLAUDE.md.
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import settings


@dataclass(frozen=True)
class TierConfig:
    key: str
    name: str
    price_usd: int
    description: str
    payment_link: str

    @property
    def available(self) -> bool:
        """False when no Payment Link is configured for this tier."""
        return bool(self.payment_link)


TIERS: dict[str, TierConfig] = {
    "l2_l3": TierConfig(
        key="l2_l3",
        name="L2/L3",
        price_usd=20,
        description="L2/L3 base access for one month.",
        payment_link=settings.STRIPE_PAYMENT_LINK_L2_L3,
    ),
    "l1": TierConfig(
        key="l1",
        name="L1",
        price_usd=35,
        description="L1 base access for one month — our highest tier.",
        payment_link=settings.STRIPE_PAYMENT_LINK_L1,
    ),
}


__all__ = ["TIERS", "TierConfig"]
