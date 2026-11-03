"""Unit tests for discord_bot.bot startup helpers."""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from discord_bot import bot as bot_module


@pytest.mark.asyncio
async def test_clears_old_server_only_commands_from_the_home_server() -> None:
    bot = MagicMock()
    bot.fetch_guild_commands = AsyncMock(return_value=[MagicMock()])
    bot.bulk_overwrite_guild_commands = AsyncMock()

    with patch.object(bot_module, "settings", SimpleNamespace(DISCORD_GUILD_ID=7)):
        await bot_module._clear_legacy_guild_commands(bot)

    bot.bulk_overwrite_guild_commands.assert_awaited_once_with(7, [])


@pytest.mark.asyncio
async def test_clearing_is_a_no_op_once_they_are_gone() -> None:
    bot = MagicMock()
    bot.fetch_guild_commands = AsyncMock(return_value=[])
    bot.bulk_overwrite_guild_commands = AsyncMock()

    with patch.object(bot_module, "settings", SimpleNamespace(DISCORD_GUILD_ID=7)):
        await bot_module._clear_legacy_guild_commands(bot)

    bot.bulk_overwrite_guild_commands.assert_not_awaited()


@pytest.fixture
def _restore_cog_modules():
    """create_bot() loads every cog with load_extension, which re-imports each
    module into sys.modules. Put the originals back so later tests that compare
    classes (e.g. persistent-view restore) keep seeing the same objects."""
    saved = {k: v for k, v in sys.modules.items() if k.startswith("discord_bot.commands")}
    yield
    for key in [k for k in sys.modules if k.startswith("discord_bot.commands")]:
        del sys.modules[key]
    sys.modules.update(saved)


# What Discord receives: 0 = in a server, 1 = in a DM with the bot.
SERVER_ONLY = [0]
SERVER_AND_DM = [0, 1]


@pytest.mark.asyncio
async def test_commands_are_global_and_server_only_by_default(_restore_cog_modules) -> None:
    bot = bot_module.create_bot(MagicMock())

    assert not bot._test_guilds
    by_name = {cmd.name: cmd for cmd in bot.slash_commands}
    # Every command is registered globally, so other servers get it too.
    assert all(not cmd.guild_ids for cmd in bot.slash_commands)
    assert by_name["roster"].body.to_dict()["contexts"] == SERVER_ONLY
    assert by_name["subscribe"].body.to_dict()["contexts"] == SERVER_ONLY
    # Personal commands also work in a DM with the bot.
    assert by_name["link"].body.to_dict()["contexts"] == SERVER_AND_DM
    assert by_name["trackingon"].body.to_dict()["contexts"] == SERVER_AND_DM
    assert by_name["help"].body.to_dict()["contexts"] == SERVER_AND_DM
    # Server setup is admin-only and never offered in a DM.
    assert by_name["setup"].body.to_dict()["contexts"] == SERVER_ONLY
    assert by_name["setup"].default_member_permissions.administrator
    # /product sell is gone; /product only manages the /subscribe buttons.
    assert set(by_name["product"].children) == {"add", "remove", "list"}
