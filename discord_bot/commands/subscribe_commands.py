"""/subscribe — run a purchase through a ticket, from agreement to confirmation.

An admin opens the flow for a buyer with `/subscribe <member>`. That posts ONE
public message in the ticket which is edited in place as the purchase advances,
so the moderator watching the ticket can see exactly how far along the buyer is:

    pending  → buyer reads the T&C PDF and clicks I Agree
    signed   → Stripe payment link buttons appear
    confirmed→ an admin clicks Confirm Payment after checking Stripe

Each stage is gated: only the named buyer can click I Agree, only an admin can
Confirm or Cancel. The gates are enforced against the database row rather than
the in-memory view, since a persistent view outlives the process.

**Confirm is an attestation, not verification.** There is no Stripe webhook, so
the bot never observes a payment — the admin checked the Stripe Dashboard
themselves. Don't describe a confirmed purchase as proven paid.

The payment buttons are disnake.ButtonStyle.link buttons, which carry a URL
instead of a custom_id: Discord opens them client-side, so there is no callback
to dispatch for those. The other three buttons are ordinary callbacks on a
persistent view, restored on startup by register_persistent_views().

The links are recurring monthly subscriptions — Stripe re-bills the buyer each
month until they cancel. The tier embed says so explicitly: an unexpected second
charge is what generates chargebacks. Cancellation is manual too (there's no
Stripe customer portal wired up), so the copy points buyers at a moderator.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements.document import AGREEMENT_FULL_TEXT, AGREEMENT_PDF_PATH
from modules.agreements.validation import status_embed
from modules.subscriptions.tiers import TIERS

logger = logging.getLogger(__name__)

EMBED_COLOUR = 0x5865F2
ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
CUSTOM_ID_PREFIX = "purchase"
MAX_CANCEL_REASON_LENGTH = 512


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
    embed.set_footer(text="After paying, a moderator will confirm it here.")
    return embed


def build_subscribe_view() -> disnake.ui.View | None:
    """Link buttons for the purchasable tiers, or None if none are configured.

    A link button with an empty URL is rejected by Discord, so unavailable
    tiers are left out of the view entirely (the embed still lists them).
    """
    buttons = _payment_buttons()
    if not buttons:
        return None

    view = disnake.ui.View(timeout=None)
    for button in buttons:
        view.add_item(button)
    return view


def _payment_buttons() -> list[disnake.ui.Button]:
    return [
        disnake.ui.Button(
            label=f"{tier.name} — ${tier.price_usd}",
            style=disnake.ButtonStyle.link,
            url=tier.payment_link,
        )
        for tier in TIERS.values()
        if tier.available
    ]


class CancelReasonModal(disnake.ui.Modal):
    """Asks the admin why a purchase is being cancelled, for the record."""

    def __init__(self, agreement_id: int) -> None:
        self.agreement_id = agreement_id
        super().__init__(
            title="Cancel Purchase",
            custom_id=f"{CUSTOM_ID_PREFIX}:cancelmodal:{agreement_id}",
            components=[
                disnake.ui.TextInput(
                    label="Reason",
                    custom_id="reason",
                    style=disnake.TextInputStyle.short,
                    max_length=MAX_CANCEL_REASON_LENGTH,
                    placeholder="e.g. buyer changed their mind",
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        reason = inter.text_values["reason"].strip() or "No reason given"
        record = await storage.void_agreement(
            inter.bot.pool, self.agreement_id, voided_by=inter.author.id, reason=reason
        )
        if record is None:
            await inter.response.send_message(
                "That purchase's data is gone — it may have been deleted.", ephemeral=True
            )
            return

        logger.info(
            "Purchase id=%s cancelled by %s: %s", self.agreement_id, inter.author.id, reason
        )
        await inter.response.edit_message(
            embed=status_embed(record), view=PurchaseView(record), attachments=[]
        )


class PurchaseView(disnake.ui.View):
    """The buttons under a purchase status message.

    Persistent (timeout=None) with deterministic custom_ids, so a restarted bot
    re-attaches handlers to messages posted in a previous run — see
    register_persistent_views.

    Which buttons exist depends on the row's state, so the view is rebuilt from
    the record on every edit rather than mutated in place.
    """

    def __init__(self, record) -> None:
        super().__init__(timeout=None)
        self.agreement_id = record["id"]

        terminal = bool(record["voided_at"] or record["confirmed_at"])
        signed = bool(record["signed_at"])

        agree = disnake.ui.Button(
            label="Agreed" if signed else "I Agree",
            style=disnake.ButtonStyle.secondary if signed else disnake.ButtonStyle.success,
            disabled=signed or terminal,
            custom_id=f"{CUSTOM_ID_PREFIX}:agree:{self.agreement_id}",
        )
        agree.callback = self._on_agree
        self.add_item(agree)

        if not terminal:
            confirm = disnake.ui.Button(
                label="Confirm Payment",
                style=disnake.ButtonStyle.primary,
                disabled=not signed,
                custom_id=f"{CUSTOM_ID_PREFIX}:confirm:{self.agreement_id}",
            )
            confirm.callback = self._on_confirm
            self.add_item(confirm)

            cancel = disnake.ui.Button(
                label="Cancel",
                style=disnake.ButtonStyle.danger,
                custom_id=f"{CUSTOM_ID_PREFIX}:cancel:{self.agreement_id}",
            )
            cancel.callback = self._on_cancel
            self.add_item(cancel)

            # Only useful once they've agreed; Discord has no disabled link button,
            # so they simply aren't shown before that.
            if signed:
                for button in _payment_buttons():
                    self.add_item(button)
        else:
            self.stop()

    def _id_from(self, inter: disnake.MessageInteraction) -> int:
        """The agreement id encoded in the clicked button's custom_id.

        A persistent view is matched by custom_id, but the callback runs on
        whichever registered instance disnake dispatches to — its
        `self.agreement_id` is NOT necessarily the clicked purchase's. Always
        take the id from the interaction (the same lesson as
        base_post_commands.py).
        """
        custom_id = (inter.data.custom_id or "") if inter.data else ""
        try:
            return int(custom_id.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            logger.warning("Unparsable purchase custom_id %r", custom_id)
            return self.agreement_id

    @staticmethod
    def _is_admin(inter: disnake.MessageInteraction) -> bool:
        perms = getattr(inter.author, "guild_permissions", None)
        return bool(perms and (perms.administrator or perms.manage_guild))

    async def _on_agree(self, inter: disnake.MessageInteraction) -> None:
        agreement_id = self._id_from(inter)
        record = await storage.get_agreement(inter.bot.pool, agreement_id)
        if record is None:
            await inter.response.send_message(
                "This purchase's data is gone — it may have been deleted.", ephemeral=True
            )
            return
        if inter.author.id != record["buyer_id"]:
            await inter.response.send_message(
                "This purchase isn't addressed to you.", ephemeral=True
            )
            return

        signed = await storage.sign_agreement(inter.bot.pool, agreement_id, inter.author.id)
        if signed is None:
            # Already signed, or cancelled while they had the message open.
            await inter.response.send_message(
                "This purchase can no longer be agreed to — it's already signed or was cancelled.",
                ephemeral=True,
            )
            return

        logger.info("Purchase id=%s agreed by buyer_id=%s", agreement_id, inter.author.id)
        # The T&C PDF stays attached: it's the document they agreed to.
        await inter.response.edit_message(embed=status_embed(signed), view=PurchaseView(signed))

    async def _on_confirm(self, inter: disnake.MessageInteraction) -> None:
        if not self._is_admin(inter):
            await inter.response.send_message(
                "Only a moderator can confirm a payment.", ephemeral=True
            )
            return

        agreement_id = self._id_from(inter)
        confirmed = await storage.confirm_agreement(
            inter.bot.pool, agreement_id, confirmed_by=inter.author.id
        )
        if confirmed is None:
            await inter.response.send_message(
                "That purchase can't be confirmed — it's unsigned, already confirmed, "
                "or cancelled.",
                ephemeral=True,
            )
            return

        logger.info(
            "Purchase id=%s payment confirmed by %s", agreement_id, inter.author.id
        )
        await inter.response.edit_message(
            embed=status_embed(confirmed), view=PurchaseView(confirmed)
        )

    async def _on_cancel(self, inter: disnake.MessageInteraction) -> None:
        if not self._is_admin(inter):
            await inter.response.send_message(
                "Only a moderator can cancel a purchase.", ephemeral=True
            )
            return
        await inter.response.send_modal(CancelReasonModal(self._id_from(inter)))


class SubscribeCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="subscribe",
        description="Start a purchase for a member: agreement, payment, confirmation.",
        default_member_permissions=ADMIN_PERMS,
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def subscribe(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(description="The buyer."),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        # Check purchasability before creating anything, so nobody is asked to
        # sign an agreement for something they can't actually buy.
        if not _payment_buttons():
            logger.warning("/subscribe used with no payment links configured")
            await inter.edit_original_response(
                "Subscriptions aren't set up yet — no Stripe payment links are configured."
            )
            return

        if not AGREEMENT_PDF_PATH.exists():
            logger.error("Agreement PDF missing at %s", AGREEMENT_PDF_PATH)
            await inter.edit_original_response(
                f"The agreement PDF is missing on disk ({AGREEMENT_PDF_PATH}). "
                "Can't start a purchase without it."
            )
            return

        record = await storage.create_pending_agreement(
            self.bot.pool,
            guild_id=inter.guild.id,
            channel_id=inter.channel.id,
            buyer_id=member.id,
            sent_by=inter.author.id,
            agreement_text=AGREEMENT_FULL_TEXT,
        )

        try:
            message = await inter.channel.send(
                content=member.mention,
                embed=status_embed(record),
                file=disnake.File(AGREEMENT_PDF_PATH, filename="terms_and_conditions.pdf"),
                view=PurchaseView(record),
                allowed_mentions=disnake.AllowedMentions(users=[member]),
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure + log
            # Roll back so no orphan row points at a message that never existed.
            await storage.delete_agreement(self.bot.pool, record["id"])
            logger.exception("Failed to post purchase for buyer=%s", member.id)
            await inter.edit_original_response(f"Couldn't start the purchase: {exc}")
            return

        await storage.attach_message(self.bot.pool, record["id"], message.id)
        await inter.edit_original_response(
            f"Purchase #{record['id']} started for {member.mention} — {message.jump_url}",
            allowed_mentions=NO_PINGS,
        )


async def register_persistent_views(bot: commands.InteractionBot) -> None:
    """Re-attach purchase button handlers to messages from previous bot runs.

    Called once after login. Without this, buttons on old messages do nothing
    ("This interaction failed") because the in-memory View is gone.
    """
    try:
        rows = await storage.list_views_to_restore(bot.pool)
    except Exception:  # noqa: BLE001 — never block startup on this
        logger.exception("Couldn't load purchases to restore views")
        return

    for row in rows:
        # list_views_to_restore only returns unterminated rows, so the view is
        # rebuilt from the columns that decide which buttons are live.
        bot.add_view(
            PurchaseView(
                {
                    "id": row["id"],
                    "buyer_id": row["buyer_id"],
                    "signed_at": row["signed_at"],
                    "confirmed_at": None,
                    "voided_at": None,
                }
            )
        )
    if rows:
        logger.info("Restored %d purchase view(s)", len(rows))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(SubscribeCommands(bot))
