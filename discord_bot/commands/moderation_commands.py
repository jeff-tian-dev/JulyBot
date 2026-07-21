"""/kick, /ban, /unban — admin-only moderation commands."""
from __future__ import annotations

import disnake
from disnake.ext import commands

from modules.moderation import actions, logging, messages, purge
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

    @commands.slash_command(
        name="purgeword",
        description="Delete all of a member's messages that contain a given word.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def purgeword(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.Member,
        word: str = commands.Param(max_length=100),
    ) -> None:
        # A full-server history scan far exceeds the 3s interaction deadline.
        await inter.response.defer(ephemeral=True)

        try:
            result = await purge.purge_user_messages(inter.guild, member, word, inter.author)
        except ModerationError as exc:
            await inter.edit_original_response(content=str(exc))
            return

        summary = (
            f"Deleted **{result.deleted}** message(s) from **{member}** containing "
            f"`{word}` across {result.channels_scanned} channel(s)."
        )
        if result.channels_skipped:
            summary += f" Skipped {result.channels_skipped} channel(s) I can't manage."
        if result.failed:
            summary += f" {result.failed} deletion(s) failed."
        if result.capped:
            summary += (
                f"\n⚠️ Hit the {purge.MAX_DELETIONS_PER_RUN}-per-run limit — "
                "run the command again to keep going."
            )
        await inter.edit_original_response(content=summary)

        await logging.send_mod_log(
            self.bot,
            action="purge",
            target_label=str(member),
            target_id=member.id,
            moderator=inter.author,
            reason=f"Purged {result.deleted} message(s) containing {word!r}",
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(ModerationCommands(bot))
