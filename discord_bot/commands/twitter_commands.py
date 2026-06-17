"""Twitter stalker slash commands — add/list/remove accounts and set alert channel."""
from __future__ import annotations

import asyncio
import logging

import disnake
from disnake.ext import commands

logger = logging.getLogger(__name__)

from config.settings import settings
from modules.twitter_stalker.accounts import (
    add_account,
    list_accounts,
    remove_account,
    set_alert_channel,
)
from modules.twitter_stalker import api
from modules.twitter_stalker.rules import sync_filter_rule


class TwitterCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @staticmethod
    def _is_allowed(inter: disnake.ApplicationCommandInteraction) -> bool:
        """Return True if the invoker has an allowed role, or Manage Server if no roles configured."""
        allowed = settings.TWITTER_ALLOWED_ROLE_IDS
        if allowed:
            author_role_ids = {r.id for r in inter.author.roles}
            return bool(author_role_ids & set(allowed))
        return inter.author.guild_permissions.manage_guild

    @commands.slash_command(name="stalk", description="Add a Twitter/X account to the stalk list.")
    async def stalk(
        self,
        inter: disnake.ApplicationCommandInteraction,
        username: str,
    ) -> None:
        if not self._is_allowed(inter):
            await inter.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)
        try:
            row = await add_account(self.bot.pool, username, added_by=inter.author.id)
        except ValueError as e:
            await inter.edit_original_response(content=str(e))
            return

        handle = row["twitter_username"]
        await inter.edit_original_response(
            content=f"Now stalking **@{handle}**. New original posts will alert in the configured channel."
        )

        async def _sync() -> None:
            try:
                await asyncio.wait_for(sync_filter_rule(self.bot.pool), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("sync_filter_rule timed out after /stalk %s", handle)
            except api.TwitterApiError:
                logger.exception("sync_filter_rule failed after /stalk %s", handle)

        asyncio.create_task(_sync())

    @commands.slash_command(name="unstalk", description="Remove a Twitter/X account from the stalk list.")
    async def unstalk(
        self,
        inter: disnake.ApplicationCommandInteraction,
        username: str,
    ) -> None:
        if not self._is_allowed(inter):
            await inter.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await inter.response.defer(ephemeral=True)
        try:
            removed = await remove_account(self.bot.pool, username)
        except ValueError as e:
            await inter.edit_original_response(content=str(e))
            return

        if not removed:
            await inter.edit_original_response(content=f"@{username.lstrip('@')} is not on the stalk list.")
            return

        clean = username.lstrip('@').lower()
        await inter.edit_original_response(content=f"Removed **@{clean}** from the stalk list.")

        async def _sync() -> None:
            try:
                await asyncio.wait_for(sync_filter_rule(self.bot.pool), timeout=20)
            except asyncio.TimeoutError:
                logger.warning("sync_filter_rule timed out after /unstalk %s", clean)
            except api.TwitterApiError:
                logger.exception("sync_filter_rule failed after /unstalk %s", clean)

        asyncio.create_task(_sync())

    @commands.slash_command(name="stalklist", description="List Twitter/X accounts being stalked.")
    async def stalklist(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.defer(ephemeral=True)
        accounts = await list_accounts(self.bot.pool)
        if not accounts:
            await inter.edit_original_response(content="No accounts on the stalk list. Use `/stalk` to add one.")
            return

        lines = [f"• **@{a['twitter_username']}**" for a in accounts]
        await inter.edit_original_response(
            content=f"**Stalk list ({len(accounts)}):**\n" + "\n".join(lines)
        )

    @commands.slash_command(
        name="settwitterchannel",
        description="Set the channel where new tweets from stalked accounts are posted.",
    )
    async def settwitterchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel,
    ) -> None:
        if not self._is_allowed(inter):
            await inter.response.send_message("You don't have permission to use this command.", ephemeral=True)
            return

        await set_alert_channel(self.bot.pool, channel.id)
        await inter.response.send_message(
            f"Tweet alerts will be posted in {channel.mention}.",
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(TwitterCommands(bot))
