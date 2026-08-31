"""Subscription tier definitions surfaced by /subscribe.

Display copy lives here as a plain constant; the Stripe Payment Link per tier
comes from config/settings.py (.env), matching this repo's convention of
settings.py for per-deployment values and module-level constants for
structural, code-adjacent config. Adding or renaming a tier means an .env
addition plus a small change here — accepted given the tier count is small
and fixed.

These are recurring monthly Stripe subscriptions — the buyer is billed
automatically each month until they cancel. See CLAUDE.md.
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
        description="L2/L3 base access, billed monthly.",
        payment_link=settings.STRIPE_PAYMENT_LINK_L2_L3,
    ),
    "l1": TierConfig(
        key="l1",
        name="L1",
        price_usd=30,
        description="L1 base access, billed monthly — our highest tier.",
        payment_link=settings.STRIPE_PAYMENT_LINK_L1,
    ),
}


__all__ = ["TIERS", "TierConfig"]
