"""Unit tests for the /subscribe embed + link-button rendering."""
from __future__ import annotations

from unittest.mock import patch

import disnake
import pytest

from discord_bot.commands import subscribe_commands
from modules.subscriptions.tiers import TierConfig


def _tiers(l2_link: str = "https://buy.stripe.com/l2", l1_link: str = "https://buy.stripe.com/l1"):
    return {
        "l2_l3": TierConfig(
            key="l2_l3",
            name="L2/L3",
            price_usd=20,
            description="L2/L3 base access for one month.",
            payment_link=l2_link,
        ),
        "l1": TierConfig(
            key="l1",
            name="L1",
            price_usd=30,
            description="L1 base access for one month.",
            payment_link=l1_link,
        ),
    }


def test_tier_available_reflects_payment_link() -> None:
    tiers = _tiers(l1_link="")
    assert tiers["l2_l3"].available is True
    assert tiers["l1"].available is False


def test_embed_lists_every_tier_with_price() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        embed = subscribe_commands.build_subscribe_embed()

    names = [f.name for f in embed.fields]
    values = " ".join(f.value for f in embed.fields)
    assert names == ["L2/L3", "L1"]
    assert "$20/month" in values
    assert "$30/month" in values


def test_embed_marks_unavailable_tier() -> None:
    """A tier with no payment link is still listed, but flagged — dropping it
    silently would look like the tier no longer exists."""
    with patch.object(subscribe_commands, "TIERS", _tiers(l1_link="")):
        embed = subscribe_commands.build_subscribe_embed()

    by_name = {f.name: f.value for f in embed.fields}
    assert "unavailable" in by_name["L1"].lower()
    assert "unavailable" not in by_name["L2/L3"].lower()


@pytest.mark.asyncio
async def test_view_builds_one_link_button_per_available_tier() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.build_subscribe_view()

    assert view is not None
    assert len(view.children) == 2
    for button in view.children:
        assert button.style is disnake.ButtonStyle.link
        # A link button carries a URL and has no custom_id, so there's no
        # callback to dispatch and nothing to restore after a restart.
        assert button.url.startswith("https://buy.stripe.com/")
        assert button.custom_id is None


@pytest.mark.asyncio
async def test_view_omits_unavailable_tier() -> None:
    """Discord rejects a link button with an empty URL, so an unconfigured
    tier must be left out of the view entirely."""
    with patch.object(subscribe_commands, "TIERS", _tiers(l1_link="")):
        view = subscribe_commands.build_subscribe_view()

    assert view is not None
    assert len(view.children) == 1
    assert view.children[0].url == "https://buy.stripe.com/l2"


@pytest.mark.asyncio
async def test_view_is_none_when_nothing_configured() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers(l2_link="", l1_link="")):
        assert subscribe_commands.build_subscribe_view() is None


def test_embed_discloses_automatic_monthly_billing() -> None:
    """The auto-renewal disclosure is load-bearing, not decoration: an
    unexpected second charge is what produces chargebacks, and a dispute
    cites what the buyer was shown. If the billing model changes, this
    assertion should fail and force the copy to change with it."""
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        embed = subscribe_commands.build_subscribe_embed()

    description = embed.description.lower()
    assert "monthly subscription" in description
    assert "billed automatically" in description
    # Cancellation is manual — no Stripe customer portal is wired up.
    assert "cancel" in description
