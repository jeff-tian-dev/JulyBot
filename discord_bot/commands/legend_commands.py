"""/legend, /legend_history, /leaderboard — stubs only."""
from __future__ import annotations

import disnake
from disnake.ext import commands


class LegendCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(name="legend", description="Show your latest legend stats.")
    async def legend(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.send_message(
            "[Legend tracker] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="legend_history", description="Show your last N days of legend snapshots.")
    async def legend_history(
        self,
        inter: disnake.ApplicationCommandInteraction,
        days: int = 7,
    ) -> None:
        await inter.response.send_message(
            "[Legend tracker] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="leaderboard", description="Show today's legend leaderboard.")
    async def leaderboard(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.send_message(
            "[Legend tracker] This command is not yet implemented.",
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(LegendCommands(bot))
