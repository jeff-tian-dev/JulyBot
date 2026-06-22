"""/setyoutubechannel, /toggleyoutube, /youtube_add, /youtube_remove, /youtube_list."""
from __future__ import annotations

import disnake
from disnake.ext import commands

from modules.youtube_feed import storage

ADMIN_PERMS = disnake.Permissions(administrator=True)


class YoutubeCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="setyoutubechannel",
        description="Set the channel where new YouTube videos are posted.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def setyoutubechannel(
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
        name="toggleyoutube",
        description="Enable or disable YouTube feed monitoring for this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def toggleyoutube(self, inter: disnake.ApplicationCommandInteraction) -> None:
        enabled = await storage.toggle_youtube(self.bot.pool, inter.guild.id)
        state = "enabled" if enabled else "disabled"
        await inter.response.send_message(f"YouTube feed monitoring is now **{state}**.", ephemeral=True)

    @commands.slash_command(
        name="youtube_add",
        description="Add a YouTube channel to the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def youtube_add(
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
        name="youtube_remove",
        description="Remove a YouTube channel from the watch list.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def youtube_remove(
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
        name="youtube_list",
        description="List YouTube channels being watched in this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def youtube_list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        channels = await storage.list_watched_channels(self.bot.pool, inter.guild.id)
        settings_row = await storage.get_youtube_settings(self.bot.pool, inter.guild.id)

        if not channels:
            await inter.response.send_message("No YouTube channels are being watched.", ephemeral=True)
            return

        lines = [
            f"• {storage.format_channel_reference(c['channel_id'], c.get('channel_name'))} "
            f"— last seen: {c['last_seen_video_id'] or 'not seeded'}"
            for c in channels
        ]
        body = "\n".join(lines)

        if settings_row and settings_row.get("youtube_channel_id"):
            channel = inter.guild.get_channel(settings_row["youtube_channel_id"])
            channel_ref = channel.mention if channel else f"`{settings_row['youtube_channel_id']}`"
            enabled = settings_row.get("youtube_enabled", True)
            status = "enabled" if enabled else "disabled"
            body += f"\n\nOutput channel: {channel_ref} ({status})"

        await inter.response.send_message(body, ephemeral=True)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(YoutubeCommands(bot))
