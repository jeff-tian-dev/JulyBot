"""/subscribe — show the subscription tiers and their Stripe payment links.

The buttons are disnake.ButtonStyle.link buttons, which carry a URL instead
of a custom_id. Discord opens them client-side, so there's no callback, no
interaction to ack, and nothing to restore after a restart — unlike the
persistent views in base_post_commands.py / agreement_commands.py.

Checkout is hosted entirely by Stripe (Payment Links), so the bot never sees
the payment. Subscriptions are recorded in the Stripe Dashboard, not in this
bot's database, and Discord access is still granted manually.

The links are recurring monthly subscriptions — Stripe re-bills the buyer
each month until they cancel. The embed says so explicitly: an unexpected
second charge is what generates chargebacks. Cancellation is manual too
(there's no Stripe customer portal wired up), so the copy points buyers at a
moderator.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from modules.subscriptions.tiers import TIERS

logger = logging.getLogger(__name__)

EMBED_COLOUR = 0x5865F2


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


class SubscribeCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="subscribe",
        description="Show the subscription tiers and how to buy one.",
    )
    async def subscribe(self, inter: disnake.ApplicationCommandInteraction) -> None:
        view = build_subscribe_view()
        if view is None:
            logger.warning("/subscribe used with no payment links configured")
            await inter.response.send_message(
                "Subscriptions aren't set up yet — please contact a moderator.",
                ephemeral=True,
            )
            return

        await inter.response.send_message(
            embed=build_subscribe_embed(), view=view, ephemeral=True
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(SubscribeCommands(bot))
