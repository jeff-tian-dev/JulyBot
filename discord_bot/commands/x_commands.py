"""/xsetchannel, /xtoggle, /xadd, /xremove, /xlist."""
from __future__ import annotations

import disnake
from disnake.ext import commands

from config.settings import settings
from discord_bot.time_format import discord_relative_timestamp
from modules.x_monitor import storage
from modules.x_monitor.snowflake import tweet_id_to_datetime

ADMIN_PERMS = disnake.Permissions(administrator=True)

NOT_CONFIGURED_MSG = "X monitoring is not configured. Set X_COOKIES in .env."


def _last_seen_tweet_label(tweet_id: int | None) -> str:
    if not tweet_id:
        return "not seeded"
    created_at = tweet_id_to_datetime(tweet_id)
    if created_at is None:
        return "not seeded"
    return discord_relative_timestamp(created_at)


class XCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="xsetchannel",
        description="Set the channel where new X posts are published.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def xsetchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel,
    ) -> None:
        if not settings.X_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        await storage.set_x_channel(self.bot.pool, inter.guild.id, channel.id)
        await inter.response.send_message(
            f"New X posts will be published in {channel.mention}.",
            ephemeral=True,
        )

    @commands.slash_command(
        name="xtoggle",
        description="Enable or disable X monitoring for this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def xtoggle(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not settings.X_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        enabled = await storage.toggle_x(self.bot.pool, inter.guild.id)
        state = "enabled" if enabled else "disabled"
        await inter.response.send_message(f"X monitoring is now **{state}**.", ephemeral=True)

    @commands.slash_command(
        name="xadd",
        description="Add an X account to the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def xadd(
        self,
        inter: disnake.ApplicationCommandInteraction,
        username: str,
    ) -> None:
        if not settings.X_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        try:
            row = await storage.add_watched_account(self.bot.pool, inter.guild.id, username)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        await inter.response.send_message(
            f"Now watching **@{row['username']}** (last seen post id: {row['last_seen_tweet_id']}).",
            ephemeral=True,
        )

    @commands.slash_command(
        name="xremove",
        description="Remove an X account from the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def xremove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        username: str,
    ) -> None:
        if not settings.X_COOKIES:
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
        name="xlist",
        description="List X accounts being watched in this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def xlist(self, inter: disnake.ApplicationCommandInteraction) -> None:
        if not settings.X_COOKIES:
            await inter.response.send_message(NOT_CONFIGURED_MSG, ephemeral=True)
            return

        accounts = await storage.list_watched_accounts(self.bot.pool, inter.guild.id)
        settings_row = await storage.get_x_settings(self.bot.pool, inter.guild.id)

        enabled = True
        if settings_row is not None:
            enabled = bool(settings_row.get("x_enabled", True))

        status = "enabled" if enabled else "disabled"
        lines = [f"**X monitoring:** {status}"]

        channel_id = settings_row.get("x_channel_id") if settings_row else None
        if channel_id:
            channel = inter.guild.get_channel(channel_id)
            channel_ref = channel.mention if channel else f"`{channel_id}`"
            lines.append(f"**Output channel:** {channel_ref}")
        else:
            lines.append("**Output channel:** not set")

        if accounts:
            lines.append("")
            lines.extend(
                f"• **@{a['username']}** — last seen: {_last_seen_tweet_label(a['last_seen_tweet_id'])}"
                for a in accounts
            )
        else:
            lines.append("")
            lines.append("No accounts on the watch list.")

        await inter.response.send_message("\n".join(lines), ephemeral=True)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(XCommands(bot))
