"""Discord bot entry point.

Commands are registered in discord_bot/commands/.
Each command file is a disnake Cog loaded on startup.
"""
from __future__ import annotations

import logging

import asyncpg
import disnake
from disnake.ext import commands

from config.settings import settings

logger = logging.getLogger(__name__)


COG_MODULES_FULL = (
    "discord_bot.commands.account_commands",
    "discord_bot.commands.legend_commands",
    "discord_bot.commands.base_finder_commands",
    "discord_bot.commands.ping_commands",
    "discord_bot.commands.twitter_commands",
)

COG_MODULES_TWITTER = (
    "discord_bot.commands.twitter_commands",
)


def create_bot(pool: asyncpg.Pool) -> commands.InteractionBot:
    """Build a configured InteractionBot with Cogs for the active BOT_MODE."""
    cog_modules = COG_MODULES_TWITTER if settings.twitter_only else COG_MODULES_FULL
    intents = disnake.Intents.default()
    intents.message_content = True

    test_guilds = [settings.DISCORD_GUILD_ID] if settings.DISCORD_GUILD_ID else None

    bot = commands.InteractionBot(
        intents=intents,
        test_guilds=test_guilds,
    )
    bot.pool = pool

    @bot.event
    async def on_ready() -> None:
        logger.info("Discord bot ready as %s (id=%s)", bot.user, getattr(bot.user, "id", None))

    for module in cog_modules:
        bot.load_extension(module)
        logger.info("Loaded extension %s", module)

    return bot
