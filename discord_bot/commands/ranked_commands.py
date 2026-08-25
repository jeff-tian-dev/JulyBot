"""/group — refreshable Ranked Battles tournament group dashboard.
/groupextrapolate — extrapolated 30-attack pace top 3 for the group.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from config.settings import settings
from modules.ranked_tracker.extrapolate import build_extrapolate_dashboard
from modules.ranked_tracker.group import RankedGroupError, build_group_dashboard
from modules.ranked_tracker.tracking import list_tracked, start_tracking, stop_tracking

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

    @commands.slash_command(
        name="groupextrapolate",
        description="Extrapolate every eligible group member's pace to a full 30 attacks.",
    )
    async def groupextrapolate(
        self,
        inter: disnake.ApplicationCommandInteraction,
        player_tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
        min_battles: int = commands.Param(
            default=10,
            min_value=0,
            description="A member needs more than this many attacks AND defenses this week to count.",
        ),
    ) -> None:
        # ~100 CoC API calls at bounded concurrency — comfortably past the 3s deadline.
        await inter.response.defer()
        try:
            embed = await build_extrapolate_dashboard(player_tag, min_battles)
        except RankedGroupError as exc:
            await self._respond(inter, str(exc))
            return
        except Exception:
            logger.exception("Unexpected error in /groupextrapolate for %s", player_tag)
            await self._respond(inter, "Something went wrong computing that.")
            return
        await inter.edit_original_response(embed=embed)

    @commands.slash_command(
        name="trackingon",
        description="Get DMed when a player's likely-to-be-hit status changes.",
    )
    async def trackingon(
        self,
        inter: disnake.ApplicationCommandInteraction,
        player_tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
    ) -> None:
        try:
            tag = await start_tracking(self.bot.pool, inter.author.id, player_tag)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return
        await inter.response.send_message(
            f"Tracking **{tag}** — I'll send you a direct message (not in this channel) when its "
            f"likely-to-be-hit status changes (checked every "
            f"{settings.RANKED_TRACKING_POLL_INTERVAL_MINUTES} minutes).",
            ephemeral=True,
        )

    @commands.slash_command(
        name="trackingoff",
        description="Stop DM alerts for a player's likely-to-be-hit status.",
    )
    async def trackingoff(
        self,
        inter: disnake.ApplicationCommandInteraction,
        player_tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
    ) -> None:
        try:
            removed = await stop_tracking(self.bot.pool, inter.author.id, player_tag)
        except ValueError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return
        message = (
            f"Stopped tracking **{player_tag}**." if removed else f"You weren't tracking **{player_tag}**."
        )
        await inter.response.send_message(message, ephemeral=True)

    @commands.slash_command(
        name="trackinglist",
        description="List the player tags you're currently tracking.",
    )
    async def trackinglist(self, inter: disnake.ApplicationCommandInteraction) -> None:
        tags = await list_tracked(self.bot.pool, inter.author.id)
        if not tags:
            await inter.response.send_message("You aren't tracking any tags.", ephemeral=True)
            return
        await inter.response.send_message(
            "You're tracking:\n" + "\n".join(f"- {tag}" for tag in tags), ephemeral=True
        )

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
