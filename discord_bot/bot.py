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


COG_MODULES = (
    "discord_bot.commands.x_commands",
    "discord_bot.commands.youtube_commands",
    "discord_bot.commands.moderation_commands",
    "discord_bot.commands.account_commands",
    "discord_bot.commands.roster_commands",
)

# Disabled until wired up (still under development):
# "discord_bot.commands.legend_commands",
# "discord_bot.commands.base_finder_commands",
# "discord_bot.commands.ping_commands",


def create_bot(pool: asyncpg.Pool) -> commands.InteractionBot:
    """Build a configured InteractionBot with Cogs for all modules."""
    intents = disnake.Intents.default()
    intents.message_content = True
    # Required to resolve `disnake.Member` command arguments for users other than
    # the invoker (e.g. /purgeword <member>). Without it, member-typed options for
    # other users can fail to resolve, so the interaction is never acked in time
    # ("The application did not respond"). Privileged: also enable "Server Members
    # Intent" for the bot in the Discord Developer Portal.
    intents.members = True

    test_guilds = [settings.DISCORD_GUILD_ID] if settings.DISCORD_GUILD_ID else None

    bot = commands.InteractionBot(
        intents=intents,
        test_guilds=test_guilds,
    )
    bot.pool = pool

    @bot.event
    async def on_ready() -> None:
        logger.info("Discord bot ready as %s (id=%s)", bot.user, getattr(bot.user, "id", None))

    for module in COG_MODULES:
        bot.load_extension(module)
        logger.info("Loaded extension %s", module)

    return bot
