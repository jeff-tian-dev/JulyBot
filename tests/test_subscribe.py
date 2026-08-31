"""Unit tests for the /subscribe embed + link-button rendering."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


# --- the two-step agreement-then-pay flow -------------------------------------


def _interaction(*, guild_id: int | None = 1, author_id: int = 4242) -> MagicMock:
    inter = MagicMock()
    inter.author.id = author_id
    inter.guild = MagicMock(id=guild_id) if guild_id is not None else None
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


@pytest.mark.asyncio
async def test_subscribe_shows_terms_first_not_payment_buttons() -> None:
    """Step one is the agreement. The payment links must not be reachable
    before the buyer has accepted the terms."""
    bot = MagicMock()
    cog = subscribe_commands.SubscribeCommands(bot)
    inter = _interaction()

    with patch.object(subscribe_commands, "TIERS", _tiers()):
        await cog.subscribe.callback(cog, inter)

    kwargs = inter.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["embed"].title == "Purchase Agreement"
    assert kwargs["file"] is not None  # the T&C PDF
    assert isinstance(kwargs["view"], subscribe_commands.AgreeView)

    labels = [item.label for item in kwargs["view"].children]
    assert labels == ["I Agree"]


@pytest.mark.asyncio
async def test_subscribe_short_circuits_before_writing_an_agreement() -> None:
    """With nothing purchasable, nobody should be asked to sign anything."""
    bot = MagicMock()
    cog = subscribe_commands.SubscribeCommands(bot)
    inter = _interaction()

    with patch.object(subscribe_commands, "TIERS", _tiers(l2_link="", l1_link="")), \
         patch.object(subscribe_commands.storage, "create_signed_agreement", new=AsyncMock()) as create:
        await cog.subscribe.callback(cog, inter)

    kwargs = inter.response.send_message.call_args.kwargs
    assert "aren't set up yet" in inter.response.send_message.call_args.args[0]
    assert "view" not in kwargs or kwargs.get("view") is None
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_agree_records_the_signature_then_reveals_payment_links() -> None:
    bot = MagicMock()
    bot.pool = MagicMock()
    inter = _interaction()

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage,
             "create_signed_agreement",
             new=AsyncMock(return_value={"id": 7}),
         ) as create:
        view = subscribe_commands.AgreeView(bot)
        await view.children[0].callback(inter)

    # Recorded against the clicking user, with the verbatim terms text.
    kwargs = create.await_args.kwargs
    assert kwargs["buyer_id"] == 4242
    assert kwargs["guild_id"] == 1
    assert kwargs["agreement_text"] == subscribe_commands.AGREEMENT_FULL_TEXT

    # Only then are the payment links sent.
    follow_up = inter.followup.send.call_args.kwargs
    assert follow_up["ephemeral"] is True
    assert follow_up["embed"].title == "Subscriptions"
    urls = [item.url for item in follow_up["view"].children]
    assert urls == ["https://buy.stripe.com/l2", "https://buy.stripe.com/l1"]


@pytest.mark.asyncio
async def test_agree_button_is_disabled_after_signing() -> None:
    """Stops a double-click writing a second row for one purchase."""
    bot = MagicMock()
    bot.pool = MagicMock()
    inter = _interaction()

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage,
             "create_signed_agreement",
             new=AsyncMock(return_value={"id": 7}),
         ):
        view = subscribe_commands.AgreeView(bot)
        await view.children[0].callback(inter)

    assert view.children[0].disabled is True
    assert view.is_finished()
    inter.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_agree_surfaces_a_storage_failure_without_showing_links() -> None:
    """If the signature can't be recorded there's no evidence for a dispute,
    so the buyer must not be handed payment links."""
    bot = MagicMock()
    bot.pool = MagicMock()
    inter = _interaction()

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage,
             "create_signed_agreement",
             new=AsyncMock(side_effect=RuntimeError("db down")),
         ):
        view = subscribe_commands.AgreeView(bot)
        await view.children[0].callback(inter)

    inter.followup.send.assert_not_awaited()
    message = inter.response.send_message.call_args.args[0]
    assert "Couldn't record your agreement" in message
