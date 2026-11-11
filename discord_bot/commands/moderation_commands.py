"""/kick, /ban, /unban, /purgeword — admin-only moderation commands."""
from __future__ import annotations

import asyncio
import logging as _logging
import time
from typing import Union

import disnake
from disnake.ext import commands

from modules.moderation import actions, logging, messages, purge
from modules.moderation.validation import ModerationError

logger = _logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
# How often the /purgeword progress message is refreshed.
PROGRESS_INTERVAL_SECONDS = 5
# Interaction tokens last 15 minutes; stop editing the reply a little before.
INTERACTION_EDIT_BUDGET_SECONDS = 14 * 60


class PurgeCancelView(disnake.ui.View):
    """Cancel button on the (ephemeral, invoker-only) /purgeword progress reply.

    Not persistent: a restart ends the purge itself, so there is nothing to
    cancel afterwards. A click is its own interaction, so it still works after
    the original reply's 15-minute token has expired.
    """

    def __init__(self, job: purge.PurgeJob) -> None:
        super().__init__(timeout=None)
        self.job = job

    @disnake.ui.button(label="Cancel purge", style=disnake.ButtonStyle.danger)
    async def cancel_button(
        self, button: disnake.ui.Button, inter: disnake.MessageInteraction
    ) -> None:
        self.job.cancel()
        button.disabled = True
        button.label = "Cancelling…"
        await inter.response.edit_message(view=self)


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
            self.bot.pool,
            inter.guild,
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
            self.bot.pool,
            inter.guild,
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
            self.bot.pool,
            inter.guild,
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
            job = purge.start_purge(inter.guild, word, inter.author, target=member, channel=channel)
        except ModerationError as exc:
            await self._respond(inter, str(exc))
            return

        # The purge runs in the background with no time limit. The ephemeral
        # reply can only be edited while the interaction token is valid
        # (15 min), so progress stops updating after that and the final
        # summary goes to the moderator's DMs instead.
        view = PurgeCancelView(job)
        edit_deadline = time.monotonic() + INTERACTION_EDIT_BUDGET_SECONDS
        await self._edit(inter, purge.format_progress(job.result, word), view)
        while not job.task.done():
            await asyncio.wait({job.task}, timeout=PROGRESS_INTERVAL_SECONDS)
            if not job.task.done() and time.monotonic() < edit_deadline:
                await self._edit(inter, purge.format_progress(job.result, word), view)
        view.stop()

        try:
            result = job.task.result()
        except Exception as exc:  # noqa: BLE001 — surface any failure to the invoker + log
            logger.exception(
                "purgeword failed for target=%s channel=%s word=%r",
                getattr(member, "id", None),
                getattr(channel, "id", None),
                word,
            )
            summary = f"Purge failed: {type(exc).__name__}: {exc}"
            result = job.result
        else:
            summary = purge.format_summary(
                result,
                word,
                member_label=str(member) if member is not None else None,
                channel_mention=channel.mention if channel is not None else None,
            )

        if time.monotonic() < edit_deadline:
            await self._edit(inter, summary, None)
        else:
            await self._dm(inter.author, summary)

        scope = f"in #{channel.name}" if channel is not None else "server-wide"
        reason = f"Purged {result.deleted} message(s) containing {word!r} {scope}"
        if result.cancelled:
            reason += " (cancelled)"
        await logging.send_mod_log(
            self.bot.pool,
            inter.guild,
            action="purge",
            target_label=str(member) if member is not None else "Everyone",
            target_id=member.id if member is not None else None,
            moderator=inter.author,
            reason=reason,
        )

    @commands.slash_command(
        name="purgecancel",
        description="Stop the /purgeword currently running in this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def purgecancel(self, inter: disnake.ApplicationCommandInteraction) -> None:
        job = purge.cancel_running(inter.guild.id)
        if job is None:
            await inter.response.send_message("No purge is running in this server.", ephemeral=True)
            return
        await inter.response.send_message(
            f"🛑 Stopping the running purge (deleted **{job.result.deleted:,}** so far). "
            "Whoever started it gets the final summary.",
            ephemeral=True,
        )

    @staticmethod
    async def _edit(
        inter: disnake.ApplicationCommandInteraction,
        content: str,
        view: disnake.ui.View | None,
    ) -> None:
        """Edit the deferred reply; a failed progress edit must never stop the purge."""
        try:
            await inter.edit_original_response(content=content, view=view)
        except disnake.HTTPException:
            logger.warning("Couldn't edit purgeword reply", exc_info=True)

    @staticmethod
    async def _dm(user: disnake.abc.User, content: str) -> None:
        try:
            await user.send(content)
        except disnake.HTTPException:
            logger.warning("Couldn't DM purge summary to %s", user)

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
