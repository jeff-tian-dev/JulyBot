"""Mod-log channel embeds for kick/ban/unban."""
from __future__ import annotations

import logging
from typing import Literal

import asyncpg
import disnake

from modules.guild_config import storage as guild_config

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
    pool: asyncpg.Pool,
    guild: disnake.Guild,
    *,
    action: Action,
    target_label: str,
    target_id: int | None,
    moderator: disnake.Member,
    reason: str | None,
) -> None:
    """Post to the server's own mod-log channel (/setup modlog); skip if it has none.

    The channel is looked up on `guild` itself, so a server's log can never land
    in another server's channel.
    """
    config = await guild_config.get_guild_config(pool, guild.id)
    channel_id = config.get("mod_log_channel_id")
    if not channel_id:
        logger.debug("No mod-log channel set for guild=%s; skipping mod log", guild.id)
        return

    channel = guild.get_channel(channel_id)
    if channel is None:
        logger.warning("Mod log channel %s not found in guild=%s", channel_id, guild.id)
        return

    embed = disnake.Embed(
        title=_ACTION_TITLES[action],
        colour=_ACTION_COLOURS[action],
    )
    # A keyword purge with no member has no single target user to id.
    target_value = target_label if target_id is None else f"{target_label} (`{target_id}`)"
    embed.add_field(name="Target", value=target_value, inline=False)
    embed.add_field(name="Moderator", value=f"{moderator} (`{moderator.id}`)", inline=False)
    embed.add_field(name="Reason", value=reason or "—", inline=False)

    await channel.send(embed=embed)
