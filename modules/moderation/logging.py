"""Mod-log channel embeds for kick/ban/unban."""
from __future__ import annotations

import logging
from typing import Literal

import disnake

from config.settings import settings

logger = logging.getLogger(__name__)

Action = Literal["kick", "ban", "unban", "purge"]

_ACTION_COLOURS: dict[Action, int] = {
    "kick": 0xE67E22,
    "ban": 0xE74C3C,
    "unban": 0x2ECC71,
    "purge": 0x9B59B6,
}

_ACTION_TITLES: dict[Action, str] = {
    "kick": "Member Kicked",
    "ban": "Member Banned",
    "unban": "Member Unbanned",
    "purge": "Messages Purged",
}


async def send_mod_log(
    bot: disnake.Client,
    *,
    action: Action,
    target_label: str,
    target_id: int,
    moderator: disnake.Member,
    reason: str | None,
) -> None:
    channel_id = settings.MOD_LOG_CHANNEL_ID
    if not channel_id:
        logger.warning("MOD_LOG_CHANNEL_ID is not configured; skipping mod log")
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.warning("Mod log channel %s not found", channel_id)
        return

    embed = disnake.Embed(
        title=_ACTION_TITLES[action],
        colour=_ACTION_COLOURS[action],
    )
    embed.add_field(name="Target", value=f"{target_label} (`{target_id}`)", inline=False)
    embed.add_field(name="Moderator", value=f"{moderator} (`{moderator.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason or "—", inline=False)

    await channel.send(embed=embed)
