"""/agreement — purchases paid outside Stripe, plus read-only lookups.

Most buyers pay through `/subscribe`'s Stripe links, where Stripe collects the
Terms during checkout and knows who paid. Some still pay by PayPal, Venmo, Wise
or cash, and for them NONE of that exists: no Stripe record, no consent record,
nothing tying the payment to a person.

`/agreement record` covers that case. A moderator names the buyer, how they
paid, the payer's real name and the amount; the buyer clicks I Agree on the
Terms; the moderator confirms, and a row lands in the SAME `subscribers` table
Stripe purchases go to — just with no Stripe ids. So `/purchases list` and the
active-subscriber list cover every buyer regardless of how they paid.

This deliberately reinstates the agree step that `/subscribe` dropped on
2026-09-03. That removal was correct there and wrong here: it was removed
because Stripe Checkout already collected the same consent, which is exactly
what a non-Stripe buyer never touches. For these rows the signature stored here
is the only evidence of what the buyer agreed to.

`lookup` and `receipt` are read-only and render every historical row shape too.
See the agreements table comment in database/models.py.
"""
from __future__ import annotations

import io
import logging as _logging

import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements.document import AGREEMENT_FULL_TEXT
from modules.agreements.validation import lookup_embed, receipt_text, status_embed
from modules.subscriptions import storage as subscriber_storage

logger = _logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
# Shared with subscribe_commands so one restore pass covers both flows — see
# register_persistent_views there.
CUSTOM_ID_PREFIX = "purchase"
MAX_PAYER_NAME_LENGTH = 200
# agreements.payment_method is VARCHAR(20); keep these short enough to fit.
PAYMENT_METHODS = ("PayPal", "Venmo", "Wise", "Cash", "Other")


class ManualPurchaseView(disnake.ui.View):
    """Buttons on a manually-recorded (non-Stripe) purchase message.

    Persistent, sharing subscribe_commands' `purchase:<action>:<id>` custom_id
    space so ONE restore pass covers both flows. That means the id is always
    parsed off the clicked button rather than read from `self` — a persistent
    view is matched by custom_id but dispatched to whichever registered
    instance disnake picks, which is usually a different purchase's.

    Only the named buyer can agree; only an admin can confirm or cancel. Both
    are checked against the database row, never the in-memory view.
    """

    def __init__(self, record) -> None:
        super().__init__(timeout=None)
        self.agreement_id = record["id"]

        if record["voided_at"] or record["confirmed_at"]:
            self.stop()
            return

        signed = bool(record["signed_at"])

        agree = disnake.ui.Button(
            label="Agreed" if signed else "I Agree",
            style=disnake.ButtonStyle.secondary if signed else disnake.ButtonStyle.success,
            disabled=signed,
            custom_id=f"{CUSTOM_ID_PREFIX}:magree:{self.agreement_id}",
        )
        agree.callback = self._on_agree
        self.add_item(agree)

        confirm = disnake.ui.Button(
            label="Confirm Payment",
            style=disnake.ButtonStyle.primary,
            # Nothing else records this buyer's consent, so confirming an
            # unsigned manual purchase would leave no evidence at all.
            disabled=not signed,
            custom_id=f"{CUSTOM_ID_PREFIX}:mconfirm:{self.agreement_id}",
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = disnake.ui.Button(
            label="Cancel",
            style=disnake.ButtonStyle.danger,
            custom_id=f"{CUSTOM_ID_PREFIX}:mcancel:{self.agreement_id}",
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    @staticmethod
    def _id_from(inter: disnake.MessageInteraction) -> int:
        """The agreement id encoded in the clicked button's custom_id.

        Never read self.agreement_id in a callback — see the class docstring.
        """
        return int(inter.data.custom_id.rsplit(":", 1)[1])

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
            await inter.response.send_message(
                "This purchase can no longer be agreed to — it's already signed "
                "or was cancelled.",
                ephemeral=True,
            )
            return

        logger.info("Manual purchase id=%s agreed by buyer_id=%s", agreement_id, inter.author.id)
        await inter.response.edit_message(
            embed=status_embed(signed), view=ManualPurchaseView(signed)
        )

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
                "That purchase can't be confirmed — it's already confirmed or cancelled.",
                ephemeral=True,
            )
            return

        # Lands in the same table Stripe purchases do, minus the Stripe ids, so
        # /purchases and the active-subscriber list cover every buyer.
        await subscriber_storage.create_subscriber(
            inter.bot.pool,
            discord_id=confirmed["buyer_id"],
            guild_id=confirmed["guild_id"],
            agreement_id=agreement_id,
            stripe_subscription_id=None,
            stripe_customer_id=None,
            payer_name=confirmed["payer_name"],
            email=None,
            tier=None,
            status=subscriber_storage.MANUAL_STATUS,
            current_period_end=None,
            linked_by=inter.author.id,
            payment_method=confirmed["payment_method"],
            amount_cents=confirmed["amount_cents"],
        )

        logger.info(
            "Manual purchase id=%s confirmed by %s (%s)",
            agreement_id,
            inter.author.id,
            confirmed["payment_method"],
        )
        await inter.response.edit_message(
            embed=status_embed(confirmed), view=ManualPurchaseView(confirmed)
        )

    async def _on_cancel(self, inter: disnake.MessageInteraction) -> None:
        if not self._is_admin(inter):
            await inter.response.send_message(
                "Only a moderator can cancel a purchase.", ephemeral=True
            )
            return
        record = await storage.void_agreement(
            inter.bot.pool,
            self._id_from(inter),
            voided_by=inter.author.id,
            reason="Cancelled by moderator",
        )
        if record is None:
            await inter.response.send_message(
                "That purchase's data is gone.", ephemeral=True
            )
            return
        await inter.response.edit_message(
            embed=status_embed(record), view=ManualPurchaseView(record)
        )


class AgreementCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="agreement",
        description="Purchase agreement commands.",
        default_member_permissions=ADMIN_PERMS,
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def agreement(self, inter: disnake.ApplicationCommandInteraction) -> None:
        pass

    @agreement.sub_command(
        name="record",
        description="Record a purchase paid outside Stripe (PayPal, Venmo, cash...).",
    )
    async def record(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(description="The buyer."),
        method: str = commands.Param(
            description="How they paid.", choices=list(PAYMENT_METHODS)
        ),
        payer_name: str = commands.Param(
            description="The name on the payment.", max_length=MAX_PAYER_NAME_LENGTH
        ),
        amount_usd: float = commands.Param(
            description="Amount paid in USD, e.g. 35", gt=0
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        record = await storage.create_manual_agreement(
            self.bot.pool,
            guild_id=inter.guild.id,
            channel_id=inter.channel.id,
            buyer_id=member.id,
            sent_by=inter.author.id,
            payment_method=method,
            payer_name=payer_name,
            amount_cents=round(amount_usd * 100),
            # Stored verbatim: for a non-Stripe buyer this row is the only
            # record of what they were shown.
            agreement_text=AGREEMENT_FULL_TEXT,
        )

        try:
            message = await inter.channel.send(
                content=member.mention,
                embed=status_embed(record),
                view=ManualPurchaseView(record),
                allowed_mentions=disnake.AllowedMentions(users=[member]),
            )
        except Exception as exc:  # noqa: BLE001 — surface it and log the traceback
            # Roll back so no orphan row points at a message that never existed.
            await storage.delete_agreement(self.bot.pool, record["id"])
            logger.exception("Failed to post manual purchase for buyer=%s", member.id)
            await inter.edit_original_response(f"Couldn't start the purchase: {exc}")
            return

        await storage.attach_message(self.bot.pool, record["id"], message.id)
        await inter.edit_original_response(
            f"Purchase #{record['id']} recorded for {member.mention} "
            f"({method}, ${amount_usd:.2f}) — {message.jump_url}",
            allowed_mentions=NO_PINGS,
        )

    @agreement.sub_command(
        name="lookup", description="Show every agreement signed by a buyer."
    )
    async def lookup(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(description="The buyer."),
    ) -> None:
        rows = await storage.list_agreements_for_buyer(self.bot.pool, member.id)
        await inter.response.send_message(
            embed=lookup_embed(member.id, rows), ephemeral=True, allowed_mentions=NO_PINGS
        )

    @agreement.sub_command(
        name="receipt",
        description="Get a downloadable proof-of-signature document for an agreement.",
    )
    async def receipt(
        self,
        inter: disnake.ApplicationCommandInteraction,
        agreement_id: int = commands.Param(description="The agreement's #id (see /agreement lookup)."),
    ) -> None:
        record = await storage.get_agreement(self.bot.pool, agreement_id)
        if record is None:
            await inter.response.send_message(
                f"No agreement found with id {agreement_id}.", ephemeral=True
            )
            return

        text = receipt_text(
            record,
            buyer_label=await self._user_label(record["buyer_id"]),
            sender_label=(
                await self._user_label(record["sent_by"])
                if record["sent_by"] is not None
                else None
            ),
            voided_by_label=(
                await self._user_label(record["voided_by"])
                if record["voided_by"] is not None
                else None
            ),
            confirmed_by_label=(
                await self._user_label(record["confirmed_by"])
                if record["confirmed_by"] is not None
                else None
            ),
        )
        file = disnake.File(
            io.BytesIO(text.encode("utf-8")), filename=f"agreement_{agreement_id}_receipt.txt"
        )
        await inter.response.send_message(file=file, ephemeral=True)

    async def _user_label(self, user_id: int) -> str:
        """Best-effort "Name (id)" label for a receipt; never raises — a user
        who left the server or was never cached still needs a resolvable
        label on the document."""
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except disnake.HTTPException:
                return f"Unknown user ({user_id})"
        return str(user)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(AgreementCommands(bot))
