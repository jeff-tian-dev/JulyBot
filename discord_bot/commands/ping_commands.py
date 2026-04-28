"""/setpingchannel, /togglepings — stubs only."""
from __future__ import annotations

import disnake
from disnake.ext import commands


class PingCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(name="setpingchannel", description="Set the channel where legend pings are sent.")
    async def setpingchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel,
    ) -> None:
        await inter.response.send_message(
            "[Ping automator] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="togglepings", description="Toggle scheduled legend pings on or off.")
    async def togglepings(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.send_message(
            "[Ping automator] This command is not yet implemented.",
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(PingCommands(bot))
