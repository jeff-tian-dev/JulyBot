"""/agreement — digital signature on paid purchases.

Posts the purchase agreement (a PDF attachment + summary embed) to a buyer with
an "I Agree" button. Clicking it does NOT sign immediately — it opens an
ephemeral confirmation panel showing exactly what a moderator typed in (name,
Discord identity, PayPal contact), so a typo or wrong-buyer mistake is caught
before it becomes a permanent record rather than after. Only confirming there
("I Confirm This Is Correct") calls sign_agreement. The resulting record is
kept for later lookup if a PayPal dispute needs a response — not a technical
block on PayPal disputes, an audit trail to cite when responding to one.

The button is a *persistent* view: custom_ids carry the agreements row id and
the view has no timeout, so it keeps working after a bot restart (see
register_persistent_views).
"""
from __future__ import annotations

import io
import logging as _logging

import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements.document import AGREEMENT_FULL_TEXT, AGREEMENT_PDF_PATH
from modules.agreements.validation import (
    PAYMENT_METHODS,
    confirmation_embed,
    lookup_embed,
    pending_embed,
    receipt_text,
    signed_embed,
    validate_order_ref,
    validate_payer_name,
    validate_payment_contact,
    validate_payment_method,
    validate_void_reason,
    voided_embed,
)
from modules.announce.poster import PostError, validate_target

logger = _logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
CUSTOM_ID_PREFIX = "agreement"


class AgreementView(disnake.ui.View):
    """The "I Agree" button under a posted agreement.

    Persistent (timeout=None) with a deterministic custom_id, so a restarted bot
    can re-attach the handler to messages it posted in a previous run.
    """

    def __init__(self, agreement_id: int, *, signed: bool = False) -> None:
        super().__init__(timeout=None)
        self.agreement_id = agreement_id

        button = disnake.ui.Button(
            label="Signed" if signed else "I Agree",
            style=disnake.ButtonStyle.secondary if signed else disnake.ButtonStyle.success,
            disabled=signed,
            custom_id=f"{CUSTOM_ID_PREFIX}:sign:{agreement_id}",
        )
        button.callback = self._on_sign
        self.add_item(button)

    def _id_from(self, inter: disnake.MessageInteraction) -> int:
        """The agreement id encoded in the clicked button's custom_id.

        A persistent view is matched by custom_id, but the callback runs on
        whichever registered instance disnake dispatches to — its
        `self.agreement_id` is NOT necessarily the clicked agreement's. Always
        take the id from the interaction (see the same lesson in
        base_post_commands.py).
        """
        custom_id = (inter.data.custom_id or "") if inter.data else ""
        try:
            return int(custom_id.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            logger.warning("Unparsable agreement custom_id %r", custom_id)
            return self.agreement_id

    async def _on_sign(self, inter: disnake.MessageInteraction) -> None:
        """The I Agree click doesn't sign yet — it opens an ephemeral
        confirmation panel so the buyer can catch a wrong name/contact before
        it becomes a permanent record."""
        agreement_id = self._id_from(inter)
        record = await storage.get_agreement(inter.bot.pool, agreement_id)
        if record is None:
            await inter.response.send_message(
                "This agreement's data is gone — it may have been deleted.", ephemeral=True
            )
            return
        if record["voided_at"] is not None:
            await inter.response.send_message(
                "This agreement has been voided and can no longer be signed.", ephemeral=True
            )
            return
        if inter.author.id != record["buyer_id"]:
            await inter.response.send_message(
                "This agreement isn't addressed to you.", ephemeral=True
            )
            return
        if record["signed_at"] is not None:
            await inter.response.send_message(
                "This agreement has already been signed.", ephemeral=True
            )
            return

        await inter.response.send_message(
            embed=confirmation_embed(record, buyer_label=str(inter.author)),
            view=ConfirmSignView(agreement_id),
            ephemeral=True,
        )


class ConfirmSignView(disnake.ui.View):
    """The ephemeral "I Confirm This Is Correct" step shown after "I Agree".

    Not persistent — it's a short-lived confirmation prompt tied to the
    interaction that opened it, unlike AgreementView's public button.
    """

    def __init__(self, agreement_id: int) -> None:
        super().__init__(timeout=600)
        self.agreement_id = agreement_id

        confirm = disnake.ui.Button(
            label="I Confirm This Is Correct",
            emoji="✅",
            style=disnake.ButtonStyle.success,
            custom_id=f"{CUSTOM_ID_PREFIX}:confirm:{agreement_id}",
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

    async def _on_confirm(self, inter: disnake.MessageInteraction) -> None:
        record = await storage.get_agreement(inter.bot.pool, self.agreement_id)
        if record is None:
            await inter.response.edit_message(
                content="This agreement's data is gone — it may have been deleted.",
                embed=None,
                view=None,
            )
            return
        if record["voided_at"] is not None:
            await inter.response.edit_message(
                content="This agreement has been voided and can no longer be signed.",
                embed=None,
                view=None,
            )
            return
        if inter.author.id != record["buyer_id"]:
            # Can't realistically happen (the panel is ephemeral to the buyer
            # who opened it), but never trust client-side scoping alone.
            await inter.response.send_message(
                "This agreement isn't addressed to you.", ephemeral=True
            )
            return

        signed = await storage.sign_agreement(inter.bot.pool, self.agreement_id, inter.author.id)
        if signed is None:
            await inter.response.edit_message(
                content="This agreement has already been signed.", embed=None, view=None
            )
            return

        await inter.response.edit_message(
            content="You've signed the agreement. Thanks!", embed=None, view=None
        )

        # Reflect the signature on the original public message too, so anyone
        # looking at the channel can see it's been signed. Best-effort: the
        # buyer's confirmation above is already recorded either way.
        try:
            channel = inter.bot.get_channel(signed["channel_id"])
            if channel is not None and signed["message_id"] is not None:
                message = await channel.fetch_message(signed["message_id"])
                await message.edit(
                    embed=signed_embed(signed), view=AgreementView(self.agreement_id, signed=True)
                )
        except disnake.HTTPException:
            logger.warning(
                "Couldn't refresh the public message for agreement id=%s", self.agreement_id
            )

        logger.info(
            "Agreement id=%s signed by buyer_id=%s", self.agreement_id, inter.author.id
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
        name="send", description="Send the purchase agreement to a buyer for e-signature."
    )
    async def send(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(description="The buyer."),
        payer_name: str = commands.Param(description="Full name on the payment."),
        payment_method: str = commands.Param(
            choices=PAYMENT_METHODS, description="How they paid."
        ),
        payment_contact: str = commands.Param(
            description="Email or @handle for the chosen payment method."
        ),
        order_ref: str = commands.Param(default=None, description="Optional order reference."),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        try:
            clean_method = validate_payment_method(payment_method)
            clean_name = validate_payer_name(payer_name)
            clean_contact = validate_payment_contact(clean_method, payment_contact)
            clean_ref = validate_order_ref(order_ref)
            validate_target(inter.channel, inter.guild)
        except PostError as exc:
            await self._respond(inter, str(exc))
            return

        if not AGREEMENT_PDF_PATH.exists():
            await self._respond(
                inter,
                f"The agreement PDF is missing on disk ({AGREEMENT_PDF_PATH}). "
                "Can't send an agreement without it.",
            )
            return

        record = await storage.create_agreement(
            self.bot.pool,
            guild_id=inter.guild.id,
            channel_id=inter.channel.id,
            buyer_id=member.id,
            sent_by=inter.author.id,
            payer_name=clean_name,
            payment_method=clean_method,
            payment_contact=clean_contact,
            order_ref=clean_ref,
            agreement_text=AGREEMENT_FULL_TEXT,
        )

        embed = pending_embed(buyer_id=member.id, order_ref=clean_ref)
        view = AgreementView(record["id"])
        file = disnake.File(AGREEMENT_PDF_PATH, filename="terms_and_conditions.pdf")

        try:
            message = await inter.channel.send(
                content=member.mention,
                embed=embed,
                file=file,
                view=view,
                allowed_mentions=disnake.AllowedMentions(users=[member]),
            )
        except disnake.HTTPException as exc:
            await storage.delete_agreement(self.bot.pool, record["id"])
            await self._respond(inter, f"Failed to send the agreement: {exc.text or exc}")
            return
        except Exception as exc:  # noqa: BLE001 — surface any failure + log
            await storage.delete_agreement(self.bot.pool, record["id"])
            logger.exception("agreement send failed for buyer=%s", member.id)
            await self._respond(inter, f"Send failed: {type(exc).__name__}: {exc}")
            return

        await storage.attach_message(self.bot.pool, record["id"], message.id)
        await self._respond(inter, f"Agreement sent to {member.mention} — {message.jump_url}")

    @agreement.sub_command(
        name="lookup", description="Show every agreement sent to a buyer."
    )
    async def lookup(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member = commands.Param(description="The buyer."),
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
            sender_label=await self._user_label(record["sent_by"]),
            voided_by_label=(
                await self._user_label(record["voided_by"])
                if record["voided_by"] is not None
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

    @agreement.sub_command(name="void", description="Void a signed or pending agreement.")
    async def void(
        self,
        inter: disnake.ApplicationCommandInteraction,
        agreement_id: int = commands.Param(description="The agreement's #id (see /agreement lookup)."),
        reason: str = commands.Param(description="Why this agreement is being voided."),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        try:
            clean_reason = validate_void_reason(reason)
        except PostError as exc:
            await self._respond(inter, str(exc))
            return

        record = await storage.get_agreement(self.bot.pool, agreement_id)
        if record is None:
            await self._respond(inter, f"No agreement found with id {agreement_id}.")
            return
        if record["voided_at"] is not None:
            await self._respond(inter, f"Agreement #{agreement_id} is already voided.")
            return

        voided = await storage.void_agreement(
            self.bot.pool, agreement_id, voided_by=inter.author.id, reason=clean_reason
        )

        # Best-effort: reflect the void on the original message if it's still
        # reachable. Never fail the command over this — the DB record is the
        # authoritative one.
        if voided["message_id"] is not None:
            try:
                channel = self.bot.get_channel(voided["channel_id"])
                if channel is not None:
                    message = await channel.fetch_message(voided["message_id"])
                    await message.edit(embed=voided_embed(voided), view=None)
            except disnake.HTTPException:
                logger.warning(
                    "Couldn't reflect void on agreement id=%s message", agreement_id
                )

        await self._respond(inter, f"Agreement #{agreement_id} voided: {clean_reason}")

    @staticmethod
    async def _respond(inter: disnake.ApplicationCommandInteraction, content: str) -> None:
        """Reply whether or not the interaction was already deferred/responded."""
        try:
            if inter.response.is_done():
                await inter.edit_original_response(content=content, allowed_mentions=NO_PINGS)
            else:
                await inter.response.send_message(
                    content=content, ephemeral=True, allowed_mentions=NO_PINGS
                )
        except disnake.HTTPException:
            logger.exception("Failed to send /agreement response")


async def register_persistent_views(bot: commands.InteractionBot) -> None:
    """Re-attach "I Agree" button handlers to agreements from previous bot runs.

    Called once after login. Without this, buttons on old messages do nothing
    ("This interaction failed") because the in-memory View is gone.
    """
    try:
        rows = await storage.list_views_to_restore(bot.pool)
    except Exception:  # noqa: BLE001 — never block startup on this
        logger.exception("Couldn't load agreements to restore views")
        return

    for row in rows:
        bot.add_view(AgreementView(row["id"]))
    if rows:
        logger.info("Restored %d agreement view(s)", len(rows))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(AgreementCommands(bot))
