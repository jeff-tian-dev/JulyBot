"""/purchases — review recorded purchases and fix a mislinked payment.

Two subcommands, both admin-only:

    /purchases list [member]    recent purchases, or one member's history
    /purchases relink <id>      repoint a row at a different Stripe payment

**What relink fixes, precisely:** an admin picking the WRONG PAYMENT out of the
Stripe dropdown when confirming. The Discord buyer is not changed — `/subscribe`
names them up front, so the buyer is the half that was already correct. Relink
therefore takes only the subscriber id and opens a fresh Stripe picker.

Before this existed a mislink was unfixable from Discord: the unique index on
stripe_subscription_id meant the correct buyer could never be linked to that
payment afterwards, and the only remedy was editing the database by hand.

The row is corrected in place rather than deleted and re-created, so `id`,
`created_at`, `agreement_id` and the original `linked_by` all survive — a
correction is exactly when the history of who touched a row matters.
"""
from __future__ import annotations

import logging

import asyncpg
import disnake
from disnake.ext import commands

from modules.subscriptions import stripe_api
from modules.subscriptions import storage as subscriber_storage

logger = logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
EMBED_COLOUR = 0x5865F2
# Discord caps an embed description at 4096 chars; a page of purchases stays
# well under that, and a shorter list is easier to scan for the right row.
LIST_LIMIT = 15
# The admin is mid-correction; if they wander off, re-running the command is cheap.
PICKER_TIMEOUT_SECONDS = 300


def _money(record) -> str:
    return f"{record['tier'] or '—'}"


def purchase_line(record) -> str:
    """One purchase, rendered for the list embed."""
    who = f"<@{record['discord_id']}>"
    parts = [f"**#{record['id']}** — {who} — `{record['stripe_subscription_id']}`"]

    detail = [record["status"]]
    if record["payer_name"]:
        detail.append(record["payer_name"])
    if record["created_at"]:
        detail.append(f"<t:{int(record['created_at'].timestamp())}:d>")
    parts.append("  " + " · ".join(detail))

    # Surface a correction in the list itself — someone scanning for a mistake
    # should see which rows have already been touched.
    if record["relinked_at"]:
        parts.append(f"  ↻ relinked by <@{record['relinked_by']}>")
    return "\n".join(parts)


def build_list_embed(records, *, member: disnake.User | None = None) -> disnake.Embed:
    """The /purchases list panel."""
    if member is not None:
        title = f"Purchases — {member.display_name}"
        empty = f"No purchases recorded for {member.mention}."
    else:
        title = "Recent Purchases"
        empty = "No purchases recorded yet."

    if not records:
        return disnake.Embed(title=title, description=empty, colour=EMBED_COLOUR)

    embed = disnake.Embed(
        title=title,
        description="\n".join(purchase_line(r) for r in records),
        colour=EMBED_COLOUR,
    )
    embed.set_footer(
        text="Use /purchases relink <id> if a purchase is linked to the wrong payment."
    )
    return embed


class RelinkPickerView(disnake.ui.View):
    """Pick the Stripe payment a subscriber row should actually point at.

    Mirrors StripePickerView in subscribe_commands: short-lived and ephemeral,
    so it needs no persistent registration — if it expires, re-run the command.

    Payments already linked to another row are shown but marked, because the
    unique index would reject them anyway; saying so up front beats a confusing
    failure after the admin has already chosen.
    """

    def __init__(self, subscriber_id: int, purchases, *, already_linked: set[str]) -> None:
        super().__init__(timeout=PICKER_TIMEOUT_SECONDS)
        self.subscriber_id = subscriber_id
        self._by_id = {p.subscription_id: p for p in purchases}
        self._already_linked = already_linked

        select = disnake.ui.StringSelect(
            placeholder="Which Stripe purchase should this be?",
            options=[
                disnake.SelectOption(
                    label=p.label()[:100],
                    value=p.subscription_id,
                    description=(
                        "⚠️ already linked to another purchase"
                        if p.subscription_id in already_linked
                        else (p.email or p.subscription_id)
                    )[:100],
                )
                for p in purchases
            ],
        )
        select.callback = self._on_pick
        self.add_item(select)

    async def _on_pick(self, inter: disnake.MessageInteraction) -> None:
        purchase = self._by_id.get(inter.data.values[0])
        if purchase is None:
            await inter.response.send_message(
                "That purchase is no longer in the list — run the command again.",
                ephemeral=True,
            )
            return

        try:
            updated = await subscriber_storage.relink_subscriber(
                inter.bot.pool,
                self.subscriber_id,
                stripe_subscription_id=purchase.subscription_id,
                stripe_customer_id=purchase.customer_id,
                payer_name=purchase.name,
                email=purchase.email,
                status=purchase.status,
                current_period_end=purchase.current_period_end,
                relinked_by=inter.author.id,
            )
        except asyncpg.UniqueViolationError:
            # Name who holds it rather than just refusing — the admin needs to
            # know which other row to fix first.
            existing = await subscriber_storage.get_subscriber_by_stripe_id(
                inter.bot.pool, purchase.subscription_id
            )
            holder = f"purchase #{existing['id']}" if existing else "another purchase"
            await inter.response.send_message(
                f"That Stripe payment is already linked to {holder}. "
                "Relink that one first, or pick a different payment.",
                ephemeral=True,
            )
            return

        if updated is None:
            await inter.response.send_message(
                f"Purchase #{self.subscriber_id} no longer exists.", ephemeral=True
            )
            return

        logger.info(
            "Purchase #%s relinked to %s by %s",
            self.subscriber_id,
            purchase.subscription_id,
            inter.author.id,
        )
        await inter.response.edit_message(
            content=(
                f"Purchase **#{updated['id']}** for <@{updated['discord_id']}> is now "
                f"linked to `{purchase.subscription_id}`"
                + (f" ({purchase.name})" if purchase.name else "")
                + "."
            ),
            embed=None,
            view=None,
            allowed_mentions=NO_PINGS,
        )


class PurchaseCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="purchases",
        default_member_permissions=ADMIN_PERMS,
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def purchases(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Parent group; disnake never invokes this directly."""

    @purchases.sub_command(name="list", description="Show recorded purchases.")
    async def list_purchases(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(
            default=None, description="Only this member's purchases."
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        try:
            if member is not None:
                records = await subscriber_storage.list_subscribers_for_discord_id(
                    self.bot.pool, member.id
                )
            else:
                records = await subscriber_storage.list_recent_subscribers(
                    self.bot.pool, inter.guild.id, limit=LIST_LIMIT
                )
        except Exception as exc:  # noqa: BLE001 — surface it rather than time out
            logger.exception("Couldn't list purchases")
            await inter.edit_original_response(f"Couldn't load purchases: {exc}")
            return

        await inter.edit_original_response(
            embed=build_list_embed(records, member=member), allowed_mentions=NO_PINGS
        )

    @purchases.sub_command(
        name="relink",
        description="Point a purchase at a different Stripe payment.",
    )
    async def relink(
        self,
        inter: disnake.ApplicationCommandInteraction,
        purchase_id: int = commands.Param(
            description="The purchase id from /purchases list.", min_value=1
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        record = await subscriber_storage.get_subscriber(self.bot.pool, purchase_id)
        if record is None:
            await inter.edit_original_response(
                f"No purchase #{purchase_id}. Check `/purchases list`."
            )
            return

        try:
            purchases = await stripe_api.list_recent_subscriptions()
        except stripe_api.StripeNotConfiguredError:
            await inter.edit_original_response(
                "Stripe isn't configured (`STRIPE_SECRET_KEY` is unset), so payments "
                "can't be looked up. Ask whoever runs the bot to add it."
            )
            return
        except stripe_api.StripeApiError as exc:
            logger.warning("Stripe lookup failed while relinking #%s: %s", purchase_id, exc)
            await inter.edit_original_response(f"Couldn't reach Stripe: {exc}")
            return

        if not purchases:
            await inter.edit_original_response(
                "Stripe has no recent purchases to link — no active subscriptions and "
                "no successful one-time payments."
            )
            return

        already = await subscriber_storage.linked_stripe_ids(self.bot.pool)
        # The row's own payment is what we're replacing, so it isn't a conflict.
        already.discard(record["stripe_subscription_id"])

        embed = disnake.Embed(
            title=f"Relink purchase #{record['id']}",
            description=(
                f"Buyer: <@{record['discord_id']}> (unchanged)\n"
                f"Currently linked to: `{record['stripe_subscription_id']}`"
                + (f" — {record['payer_name']}" if record["payer_name"] else "")
            ),
            colour=EMBED_COLOUR,
        )
        await inter.edit_original_response(
            embed=embed,
            view=RelinkPickerView(record["id"], purchases, already_linked=already),
            allowed_mentions=NO_PINGS,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(PurchaseCommands(bot))
