"""/subscribe — accept the purchase agreement, then show the Stripe payment links.

Two ephemeral steps, both private to the buyer:

  1. The terms panel — a summary embed, the full Terms and Conditions PDF, and
     an "I Agree" button.
  2. Clicking I Agree records a signed agreement row (Discord ID + timestamp +
     the verbatim terms text) and then reveals the tier buttons.

**The signature is evidence, not a gate.** Checkout is a Stripe-hosted Payment
Link, so the bot never observes the payment and cannot verify that a signature
preceded one — anyone holding the link URL can pay without running /subscribe.
The row exists so a chargeback can be answered with proof the buyer accepted
the terms. Don't describe it as enforcement.

The payment buttons are disnake.ButtonStyle.link buttons, which carry a URL
instead of a custom_id. Discord opens them client-side, so there's no callback
and nothing to restore after a restart. The I Agree button does have a
callback, but its view is deliberately short-lived rather than persistent
(unlike base_post_commands.py): it lives inside one ephemeral interaction, and
if it times out the user just runs /subscribe again.

The links are recurring monthly subscriptions — Stripe re-bills the buyer each
month until they cancel. The embed says so explicitly: an unexpected second
charge is what generates chargebacks. Cancellation is manual too (there's no
Stripe customer portal wired up), so the copy points buyers at a moderator.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements.document import AGREEMENT_FULL_TEXT, AGREEMENT_PDF_PATH
from modules.agreements.validation import terms_embed
from modules.subscriptions.tiers import TIERS

logger = logging.getLogger(__name__)

EMBED_COLOUR = 0x5865F2
# The buyer is mid-purchase; if they wander off, re-running /subscribe is cheap.
AGREE_TIMEOUT_SECONDS = 600


def build_subscribe_embed() -> disnake.Embed:
    """The tier list. Renders every tier, marking any that aren't purchasable."""
    embed = disnake.Embed(
        title="Subscriptions",
        description=(
            "Pick a tier below to subscribe securely through Stripe.\n"
            "This is a **monthly subscription** — you'll be billed automatically "
            "each month until you cancel. To cancel, message a moderator."
        ),
        colour=EMBED_COLOUR,
    )
    for tier in TIERS.values():
        value = f"**${tier.price_usd}/month**\n{tier.description}"
        if not tier.available:
            value += "\n*Currently unavailable — ask a moderator.*"
        embed.add_field(name=tier.name, value=value, inline=True)
    embed.set_footer(text="After subscribing, open a ticket so a moderator can set up your access.")
    return embed


def build_subscribe_view() -> disnake.ui.View | None:
    """Link buttons for the purchasable tiers, or None if none are configured.

    A link button with an empty URL is rejected by Discord, so unavailable
    tiers are left out of the view entirely (the embed still lists them).
    """
    buttons = [
        disnake.ui.Button(
            label=f"{tier.name} — ${tier.price_usd}",
            style=disnake.ButtonStyle.link,
            url=tier.payment_link,
        )
        for tier in TIERS.values()
        if tier.available
    ]
    if not buttons:
        return None

    view = disnake.ui.View(timeout=None)
    for button in buttons:
        view.add_item(button)
    return view


class AgreeView(disnake.ui.View):
    """The "I Agree" button under the terms panel.

    Short-lived and non-persistent on purpose — see the module docstring.
    """

    def __init__(self, bot: commands.InteractionBot) -> None:
        super().__init__(timeout=AGREE_TIMEOUT_SECONDS)
        self.bot = bot

        button = disnake.ui.Button(
            label="I Agree",
            style=disnake.ButtonStyle.success,
        )
        button.callback = self._on_agree
        self.add_item(button)

    async def _on_agree(self, inter: disnake.MessageInteraction) -> None:
        try:
            record = await storage.create_signed_agreement(
                self.bot.pool,
                guild_id=inter.guild.id if inter.guild else 0,
                buyer_id=inter.author.id,
                agreement_text=AGREEMENT_FULL_TEXT,
            )
        except Exception as exc:  # noqa: BLE001 — surface it, don't fail silently
            logger.exception("Failed to record agreement for buyer=%s", inter.author.id)
            await inter.response.send_message(
                f"Couldn't record your agreement: {type(exc).__name__}. "
                "Nothing was charged — please try again or contact a moderator.",
                ephemeral=True,
            )
            return

        logger.info(
            "Agreement id=%s signed by buyer_id=%s via /subscribe",
            record["id"],
            inter.author.id,
        )

        # The terms message carries a PDF attachment, which an edit can't remove,
        # so the payment panel is a separate follow-up. Disable the button on the
        # original so it reads as done and can't be double-signed.
        for item in self.children:
            item.disabled = True
            item.label = "Agreed"
        self.stop()

        await inter.response.edit_message(view=self)
        await inter.followup.send(
            embed=build_subscribe_embed(),
            view=build_subscribe_view(),
            ephemeral=True,
        )


class SubscribeCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="subscribe",
        description="Read the purchase agreement and subscribe.",
    )
    async def subscribe(self, inter: disnake.ApplicationCommandInteraction) -> None:
        # Check purchasability before showing the terms, so nobody signs an
        # agreement for something they can't actually buy.
        if build_subscribe_view() is None:
            logger.warning("/subscribe used with no payment links configured")
            await inter.response.send_message(
                "Subscriptions aren't set up yet — please contact a moderator.",
                ephemeral=True,
            )
            return

        if not AGREEMENT_PDF_PATH.exists():
            logger.error("Agreement PDF missing at %s", AGREEMENT_PDF_PATH)
            await inter.response.send_message(
                "The purchase agreement is unavailable right now — please contact a moderator.",
                ephemeral=True,
            )
            return

        await inter.response.send_message(
            embed=terms_embed(),
            file=disnake.File(AGREEMENT_PDF_PATH, filename="terms_and_conditions.pdf"),
            view=AgreeView(self.bot),
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(SubscribeCommands(bot))
