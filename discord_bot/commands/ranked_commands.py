"""/group — refreshable Ranked Battles tournament group dashboard."""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from modules.ranked_tracker.group import RankedGroupError, build_group_dashboard

logger = logging.getLogger(__name__)


class RankedGroupView(disnake.ui.View):
    """A single refresh button scoped to one /group message.

    Deliberately non-persistent: the feature is "refresh while looking at it,"
    not "survives a bot restart" (see CLAUDE.md). Reading self.coc_tag off the
    instance is safe here specifically because a non-persistent view is never
    re-dispatched to a different registered instance — unlike /postbase's
    persistent views, which must parse state out of custom_id instead.
    """

    def __init__(self, coc_tag: str) -> None:
        super().__init__(timeout=None)
        self.coc_tag = coc_tag

    @disnake.ui.button(label="Refresh", emoji="🔄", style=disnake.ButtonStyle.secondary)
    async def refresh(
        self, button: disnake.ui.Button, inter: disnake.MessageInteraction
    ) -> None:
        await inter.response.defer()
        try:
            embed = await build_group_dashboard(self.coc_tag)
        except RankedGroupError as exc:
            await self._respond(inter, str(exc))
            return
        except Exception:
            logger.exception("Unexpected error refreshing ranked group for %s", self.coc_tag)
            await self._respond(inter, "Something went wrong refreshing this. Try again.")
            return
        await inter.edit_original_response(embed=embed, view=self)

    @staticmethod
    async def _respond(inter: disnake.MessageInteraction, content: str) -> None:
        try:
            if inter.response.is_done():
                await inter.followup.send(content, ephemeral=True)
            else:
                await inter.response.send_message(content, ephemeral=True)
        except disnake.HTTPException:
            logger.exception("Failed to send /group refresh error response")


class RankedCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="group", description="Look up a player's Ranked Battles tournament group."
    )
    async def group(
        self,
        inter: disnake.ApplicationCommandInteraction,
        player_tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
    ) -> None:
        await inter.response.defer()
        try:
            embed = await build_group_dashboard(player_tag)
        except RankedGroupError as exc:
            await self._respond(inter, str(exc))
            return
        except Exception:
            logger.exception("Unexpected error in /group for %s", player_tag)
            await self._respond(inter, "Something went wrong looking that up.")
            return
        await inter.edit_original_response(embed=embed, view=RankedGroupView(player_tag))

    @staticmethod
    async def _respond(inter: disnake.ApplicationCommandInteraction, content: str) -> None:
        """Reply whether or not the interaction was already deferred/responded."""
        try:
            if inter.response.is_done():
                await inter.edit_original_response(content=content)
            else:
                await inter.response.send_message(content=content, ephemeral=True)
        except disnake.HTTPException:
            logger.exception("Failed to send /group response")


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(RankedCommands(bot))
