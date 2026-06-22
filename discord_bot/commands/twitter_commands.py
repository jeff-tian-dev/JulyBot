"""/settwitterchannel, /toggletwitter, /twitter_add, /twitter_remove, /twitter_list."""
from __future__ import annotations

import disnake
from disnake.ext import commands

from config.settings import settings
from modules.twitter_monitor import storage

ADMIN_PERMS = disnake.Permissions(administrator=True)

NOT_CONFIGURED_MSG = "Twitter monitoring is not configured. Set TWITTER_COOKIES in .env."


class TwitterCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="settwitterchannel",
        description="Set the channel where new tweets are posted.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def settwitterchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel,
    ) -> None:
        if not settings.TWITTER_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        await storage.set_twitter_channel(self.bot.pool, inter.guild.id, channel.id)
        await inter.response.send_message(
            f"New tweets will be posted in {channel.mention}.",
            ephemeral=True,
        )

    @commands.slash_command(
        name="toggletwitter",
        description="Enable or disable Twitter monitoring for this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def toggletwitter(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not settings.TWITTER_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        enabled = await storage.toggle_twitter(self.bot.pool, inter.guild.id)
        state = "enabled" if enabled else "disabled"
        await inter.response.send_message(f"Twitter monitoring is now **{state}**.", ephemeral=True)

    @commands.slash_command(
        name="twitter_add",
        description="Add an X account to the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def twitter_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        username: str,
    ) -> None:
        if not settings.TWITTER_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        try:
            row = await storage.add_watched_account(self.bot.pool, inter.guild.id, username)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        await inter.response.send_message(
            f"Now watching **@{row['username']}** (last seen tweet id: {row['last_seen_tweet_id']}).",
            ephemeral=True,
        )

    @commands.slash_command(
        name="twitter_remove",
        description="Remove an X account from the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def twitter_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        username: str,
    ) -> None:
        if not settings.TWITTER_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        try:
            removed = await storage.remove_watched_account(self.bot.pool, inter.guild.id, username)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        if removed:
            await inter.response.send_message(f"Removed **@{username.lstrip('@')}** from the watch list.", ephemeral=True)
        else:
            await inter.response.send_message("That account is not on the watch list.", ephemeral=True)

    @commands.slash_command(
        name="twitter_list",
        description="List X accounts being watched in this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def twitter_list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not settings.TWITTER_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        accounts = await storage.list_watched_accounts(self.bot.pool, inter.guild.id)
        settings_row = await storage.get_twitter_settings(self.bot.pool, inter.guild.id)

        if not accounts:
            await inter.response.send_message("No X accounts are being watched.", ephemeral=True)
            return

        lines = [f"• **@{a['username']}** (last seen: {a['last_seen_tweet_id']})" for a in accounts]
        body = "\n".join(lines)

        if settings_row and settings_row.get("twitter_channel_id"):
            channel = inter.guild.get_channel(settings_row["twitter_channel_id"])
            channel_ref = channel.mention if channel else f"`{settings_row['twitter_channel_id']}`"
            enabled = settings_row.get("twitter_enabled", True)
            status = "enabled" if enabled else "disabled"
            body += f"\n\nOutput channel: {channel_ref} ({status})"

        await inter.response.send_message(body, ephemeral=True)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(TwitterCommands(bot))
