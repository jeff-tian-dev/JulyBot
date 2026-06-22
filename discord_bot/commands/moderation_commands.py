"""/kick, /ban, /unban — admin-only moderation commands."""
from __future__ import annotations

import disnake
from disnake.ext import commands

from modules.moderation import actions, logging, messages
from modules.moderation.validation import ModerationError

ADMIN_PERMS = disnake.Permissions(administrator=True)


class ModerationCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="kick",
        description="Kick a member from the server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def kick(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member,
        reason: str = commands.Param(default=None, max_length=512),
    ) -> None:
        try:
            await actions.kick_member(inter.guild, member, inter.author, reason)
        except ModerationError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        quip = messages.pick_kick_quip()
        await inter.response.send_message(messages.format_public_message(member, quip))
        await logging.send_mod_log(
            self.bot,
            action="kick",
            target_label=str(member),
            target_id=member.id,
            moderator=inter.author,
            reason=reason,
        )

    @commands.slash_command(
        name="ban",
        description="Ban a member from the server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def ban(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member,
        reason: str = commands.Param(default=None, max_length=512),
    ) -> None:
        try:
            await actions.ban_member(inter.guild, member, inter.author, reason)
        except ModerationError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        quip = messages.pick_ban_quip()
        await inter.response.send_message(messages.format_public_message(member, quip))
        await logging.send_mod_log(
            self.bot,
            action="ban",
            target_label=str(member),
            target_id=member.id,
            moderator=inter.author,
            reason=reason,
        )

    @commands.slash_command(
        name="unban",
        description="Unban a user by their Discord ID.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def unban(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user_id: str,
        reason: str = commands.Param(default=None, max_length=512),
    ) -> None:
        try:
            target_label, target_id = await actions.unban_user(inter.guild, user_id, inter.author, reason)
        except ModerationError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        await inter.response.send_message(f"Unbanned **{target_label}**.", ephemeral=True)
        await logging.send_mod_log(
            self.bot,
            action="unban",
            target_label=target_label,
            target_id=target_id,
            moderator=inter.author,
            reason=reason,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(ModerationCommands(bot))
