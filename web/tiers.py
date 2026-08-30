"""Static subscription tier definitions for the pricing page + checkout.

Display copy lives here as a plain constant; the Stripe Price ID per tier is
sourced from config/settings.py (.env), matching this repo's convention of
settings.py for per-deployment/secret values and module-level constants for
structural, code-adjacent config. Adding or renaming a tier means an .env
addition plus a small code change here — accepted trade-off given the tier
count is small and fixed.
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
    price_id: str


TIERS: dict[str, TierConfig] = {
    "l2_l3": TierConfig(
        key="l2_l3",
        name="L2/L3",
        price_usd=20,
        description="L2/L3 subscription access.",
        price_id=settings.STRIPE_PRICE_ID_L2_L3,
    ),
    "l1": TierConfig(
        key="l1",
        name="L1",
        price_usd=30,
        description="L1 subscription access — our highest tier.",
        price_id=settings.STRIPE_PRICE_ID_L1,
    ),
}


__all__ = ["TIERS", "TierConfig"]
