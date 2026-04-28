"""/link, /unlink, /whois — stubs only."""
from __future__ import annotations

import disnake
from disnake.ext import commands


class AccountCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(name="link", description="Link your Discord account to your CoC account.")
    async def link(
        self,
        inter: disnake.ApplicationCommandInteraction,
        coc_tag: str,
        token: str,
    ) -> None:
        await inter.response.send_message(
            "[Account linker] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="unlink", description="Unlink your CoC account.")
    async def unlink(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.send_message(
            "[Account linker] This command is not yet implemented.",
            ephemeral=True,
        )

    @commands.slash_command(name="whois", description="Look up a user's linked CoC account.")
    async def whois(
        self,
        inter: disnake.ApplicationCommandInteraction,
        discord_user: disnake.User,
    ) -> None:
        await inter.response.send_message(
            "[Account linker] This command is not yet implemented.",
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(AccountCommands(bot))
