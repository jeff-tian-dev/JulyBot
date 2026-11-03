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
from discord_bot.commands.subscribe_commands import (
    register_persistent_views as register_purchase_views,
)

logger = logging.getLogger(__name__)


COG_MODULES = (
    "discord_bot.commands.x_commands",
    "discord_bot.commands.youtube_commands",
    "discord_bot.commands.moderation_commands",
    "discord_bot.commands.account_commands",
    "discord_bot.commands.roster_commands",
    "discord_bot.commands.post_commands",
    "discord_bot.commands.base_post_commands",
    "discord_bot.commands.agreement_commands",
    "discord_bot.commands.ranked_commands",
    "discord_bot.commands.subscribe_commands",
    "discord_bot.commands.purchase_commands",
    "discord_bot.commands.product_commands",
    "discord_bot.commands.setup_commands",
    "discord_bot.commands.help_commands",
)

# Disabled until wired up (still under development):
# "discord_bot.commands.legend_commands",
# "discord_bot.commands.base_finder_commands",
# "discord_bot.commands.ping_commands",


async def _clear_legacy_guild_commands(bot: commands.InteractionBot) -> None:
    """Remove the home server's old server-only copies of the commands.

    Before multi-server support every command was registered to DISCORD_GUILD_ID
    only (test_guilds). Commands are global now, and disnake never deletes
    commands from a server it no longer registers to, so without this the home
    server shows every command twice. A no-op once they're gone.
    """
    guild_id = settings.DISCORD_GUILD_ID
    if not guild_id:
        return
    try:
        if await bot.fetch_guild_commands(guild_id):
            await bot.bulk_overwrite_guild_commands(guild_id, [])
            logger.info("Cleared legacy server-only commands from guild=%s", guild_id)
    except disnake.HTTPException:
        logger.exception("Couldn't clear legacy server-only commands from guild=%s", guild_id)


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

    # Commands are global so every server the bot joins gets them, and
    # server-only unless a command opts in to DMs (see PERSONAL_CONTEXTS in
    # account_commands / ranked_commands) — most read inter.guild.
    bot = commands.InteractionBot(
        intents=intents,
        default_contexts=disnake.InteractionContextTypes(guild=True),
    )
    bot.pool = pool

    # on_ready fires again on every reconnect; this startup work only needs to
    # run once per process. (Base post buttons need no restoring — they are
    # routed by a stateless listener in base_post_commands.)
    startup_done = False

    @bot.event
    async def on_ready() -> None:
        nonlocal startup_done
        logger.info("Discord bot ready as %s (id=%s)", bot.user, getattr(bot.user, "id", None))
        if not startup_done:
            startup_done = True
            await _clear_legacy_guild_commands(bot)
            await register_purchase_views(bot)

    for module in COG_MODULES:
        bot.load_extension(module)
        logger.info("Loaded extension %s", module)

    return bot
