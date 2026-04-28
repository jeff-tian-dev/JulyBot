"""/findbase, /addchannel, /cachestats — stubs only."""
from __future__ import annotations

import disnake
from disnake.ext import commands


class BaseFinderCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(name="findbase", description="Find matching bases for an uploaded screenshot.")
    async def findbase(
        self,
        inter: disnake.ApplicationCommandInteraction,
        image: disnake.Attachment,
    ) -> None:
        await inter.response.send_message(
            "[Base finder] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="addchannel", description="Add a YouTube channel to the watched list.")
    async def addchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        youtube_url: str,
    ) -> None:
        await inter.response.send_message(
            "[Base finder] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="cachestats", description="Show base cache size and ingestion stats.")
    async def cachestats(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.send_message(
            "[Base finder] This command is not yet implemented.",
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(BaseFinderCommands(bot))
