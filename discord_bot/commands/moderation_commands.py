"""/kick, /ban, /unban, /purgeword — admin-only moderation commands."""
from __future__ import annotations

import logging as _logging
from typing import Union

import disnake
from disnake.ext import commands

from modules.moderation import actions, logging, messages, purge
from modules.moderation.validation import ModerationError

logger = _logging.getLogger(__name__)

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
        description="Delete messages containing a word, optionally from one member or in one channel.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def purgeword(
        self,
        inter: disnake.ApplicationCommandInteraction,
        word: str = commands.Param(max_length=100, description="Word or phrase to match (case-insensitive)."),
        # Accept User (not just Member) so resolution never fails on a
        # member-vs-user mismatch or a target who left the guild.
        member: disnake.User = commands.Param(
            default=None, description="Only delete this member's messages. Omit for everyone's."
        ),
        channel: Union[disnake.TextChannel, disnake.Thread] = commands.Param(
            default=None, description="Only scan this channel or thread. Omit for the whole server."
        ),
    ) -> None:
        # A full-server history scan far exceeds the 3s interaction deadline.
        await inter.response.defer(ephemeral=True)

        try:
            result = await purge.purge_messages(
                inter.guild, word, inter.author, target=member, channel=channel
            )
        except ModerationError as exc:
            await self._respond(inter, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — surface any failure to the invoker + log
            logger.exception(
                "purgeword failed for target=%s channel=%s word=%r",
                getattr(member, "id", None),
                getattr(channel, "id", None),
                word,
            )
            await self._respond(inter, f"Purge failed: {type(exc).__name__}: {exc}")
            return

        author_part = f" from **{member}**" if member is not None else ""
        scope_part = (
            f"in {channel.mention}"
            if channel is not None
            else f"across {result.channels_scanned} channel(s)"
        )
        summary = (
            f"Deleted **{result.deleted}** message(s){author_part} containing "
            f"`{word}` {scope_part}."
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
        await self._respond(inter, summary)

        await logging.send_mod_log(
            self.bot,
            action="purge",
            target_label=str(member) if member is not None else "Everyone",
            target_id=member.id if member is not None else None,
            moderator=inter.author,
            reason=(
                f"Purged {result.deleted} message(s) containing {word!r} "
                + (f"in #{channel.name}" if channel is not None else "server-wide")
            ),
        )

    @staticmethod
    async def _respond(inter: disnake.ApplicationCommandInteraction, content: str) -> None:
        """Reply whether or not the interaction was already deferred/responded."""
        try:
            if inter.response.is_done():
                await inter.edit_original_response(content=content)
            else:
                await inter.response.send_message(content=content, ephemeral=True)
        except disnake.HTTPException:
            logger.exception("Failed to send purgeword response")


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(ModerationCommands(bot))
