"""/agreement — read-only access to signed purchase agreements.

Agreements are now signed self-serve as the first step of /subscribe (see
subscribe_commands.py); this Cog only reads them back. The moderator-driven
`/agreement send` and `/agreement void` were removed when payment moved to
Stripe: they collected a payer name and PayPal/Venmo/Wise contact so a dispute
could be tied to a person, which Stripe already knows.

Historical rows from that flow are still here and still readable — receipt_text
and lookup_embed render both shapes. See the agreements table comment in
database/models.py.
"""
from __future__ import annotations

import io
import logging as _logging

import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements.validation import lookup_embed, receipt_text

logger = _logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()


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
