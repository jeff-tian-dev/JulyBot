"""/agreement — digital signature on paid purchases.

Posts the purchase agreement (a PDF attachment + summary embed) to a buyer with
an "I Agree" button. Clicking it permanently records that Discord user's
acceptance alongside the PayPal name/email a moderator typed in, for later
lookup if a PayPal dispute needs a response. Not a technical block on PayPal
disputes — an audit trail to cite when responding to one.

The button is a *persistent* view: custom_ids carry the agreements row id and
the view has no timeout, so it keeps working after a bot restart (see
register_persistent_views).
"""
from __future__ import annotations

import logging as _logging

import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements.document import AGREEMENT_FULL_TEXT, AGREEMENT_PDF_PATH
from modules.agreements.validation import (
    lookup_embed,
    pending_embed,
    signed_embed,
    validate_order_ref,
    validate_paypal_email,
    validate_paypal_name,
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

        signed = await storage.sign_agreement(inter.bot.pool, agreement_id, inter.author.id)
        if signed is None:
            await inter.response.send_message(
                "This agreement has already been signed.", ephemeral=True
            )
            return

        view = AgreementView(agreement_id, signed=True)
        await inter.response.edit_message(embed=signed_embed(signed), view=view)
        await inter.followup.send(
            "You've signed the agreement. Thanks!", ephemeral=True
        )
        logger.info(
            "Agreement id=%s signed by buyer_id=%s", agreement_id, inter.author.id
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
        paypal_name: str = commands.Param(description="Full name on the PayPal payment."),
        paypal_email: str = commands.Param(description="Email on the PayPal payment."),
        order_ref: str = commands.Param(default=None, description="Optional order reference."),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        try:
            clean_name = validate_paypal_name(paypal_name)
            clean_email = validate_paypal_email(paypal_email)
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
            paypal_name=clean_name,
            paypal_email=clean_email,
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
