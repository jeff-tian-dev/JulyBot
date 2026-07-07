"""/ytsetchannel, /yttoggle, /ytadd, /ytremove, /ytlist."""
from __future__ import annotations

import disnake
from disnake.ext import commands

from discord_bot.time_format import discord_relative_timestamp
from modules.youtube_feed import fetcher, storage

ADMIN_PERMS = disnake.Permissions(administrator=True)


async def _last_seen_video_label(channel_id: str, video_id: str | None) -> str:
    if not video_id:
        return "not seeded"
    published = await fetcher.find_video_published(channel_id, video_id)
    if published is None:
        latest = await fetcher.fetch_latest_video(channel_id)
        if latest is not None and latest.video_id == video_id and latest.published is not None:
            published = latest.published
    if published is None:
        return "unknown"
    return discord_relative_timestamp(published)


class YoutubeCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="ytsetchannel",
        description="Set the channel where new YouTube videos are posted.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def ytsetchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel,
    ) -> None:
        await storage.set_youtube_channel(self.bot.pool, inter.guild.id, channel.id)
        await inter.response.send_message(
            f"New YouTube videos will be posted in {channel.mention}.",
            ephemeral=True,
        )

    @commands.slash_command(
        name="yttoggle",
        description="Enable or disable YouTube feed monitoring for this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def yttoggle(self, inter: disnake.ApplicationCommandInteraction) -> None:
        enabled = await storage.toggle_youtube(self.bot.pool, inter.guild.id)
        state = "enabled" if enabled else "disabled"
        await inter.response.send_message(f"YouTube feed monitoring is now **{state}**.", ephemeral=True)

    @commands.slash_command(
        name="ytadd",
        description="Add a YouTube channel to the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def ytadd(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel_id: str,
    ) -> None:
        try:
            row = await storage.add_watched_channel(self.bot.pool, inter.guild.id, channel_id)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        last_seen = row["last_seen_video_id"] or "not yet seeded"
        ref = storage.format_channel_reference(row["channel_id"], row.get("channel_name"))
        await inter.response.send_message(
            f"Now watching {ref} (last seen video id: {last_seen}).",
            ephemeral=True,
        )

    @commands.slash_command(
        name="ytremove",
        description="Remove a YouTube channel from the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def ytremove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel_id: str,
    ) -> None:
        try:
            removed = await storage.remove_watched_channel(self.bot.pool, inter.guild.id, channel_id)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        if removed:
            await inter.response.send_message(
                f"Removed YouTube channel **{channel_id.strip()}** from the watch list.",
                ephemeral=True,
            )
        else:
            await inter.response.send_message("That channel is not on the watch list.", ephemeral=True)

    @commands.slash_command(
        name="ytlist",
        description="List YouTube channels being watched in this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def ytlist(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.defer(ephemeral=True)

        channels = await storage.list_watched_channels(self.bot.pool, inter.guild.id)
        settings_row = await storage.get_youtube_settings(self.bot.pool, inter.guild.id)

        enabled = True
        if settings_row is not None:
            enabled = bool(settings_row.get("youtube_enabled", True))

        status = "enabled" if enabled else "disabled"
        lines = [f"**YouTube monitoring:** {status}"]

        channel_id = settings_row.get("youtube_channel_id") if settings_row else None
        if channel_id:
            channel = inter.guild.get_channel(channel_id)
            channel_ref = channel.mention if channel else f"`{channel_id}`"
            lines.append(f"**Output channel:** {channel_ref}")
        else:
            lines.append("**Output channel:** not set")

        if channels:
            lines.append("")
            for c in channels:
                last_seen = await _last_seen_video_label(c["channel_id"], c.get("last_seen_video_id"))
                lines.append(
                    f"• {storage.format_channel_reference(c['channel_id'], c.get('channel_name'))} "
                    f"— last seen: {last_seen}"
                )
        else:
            lines.append("")
            lines.append("No channels on the watch list.")

        await inter.edit_original_response(content="\n".join(lines))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(YoutubeCommands(bot))
