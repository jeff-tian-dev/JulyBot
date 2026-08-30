"""POST /checkout/{tier_key} — create a Stripe Checkout Session and redirect."""
from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import RedirectResponse

from config.settings import settings
from web import stripe_client
from web.tiers import TIERS

router = APIRouter()


@router.post("/checkout/{tier_key}")
async def checkout(tier_key: str, discord_username: str = Form(default="")):
    tier = TIERS.get(tier_key)
    if tier is None:
        raise HTTPException(status_code=404, detail=f"Unknown tier {tier_key!r}")

    metadata = {"tier": tier.key}
    cleaned_username = discord_username.strip()
    if cleaned_username:
        metadata["discord_username"] = cleaned_username

    session = await stripe_client.create_checkout_session(
        price_id=tier.price_id,
        success_url=f"{settings.WEB_BASE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{settings.WEB_BASE_URL}/cancel",
        metadata=metadata,
    )
    return RedirectResponse(url=session.url, status_code=303)
