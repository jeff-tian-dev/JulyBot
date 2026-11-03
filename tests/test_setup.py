"""Unit tests for /setup (discord_bot.commands.setup_commands + modules.guild_config.setup)
and /help (discord_bot.commands.help_commands)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import help_commands, setup_commands
from modules.guild_config import setup as setup_logic


def _perms(**kwargs) -> SimpleNamespace:
    base = {"view_channel": True, "send_messages": True, "embed_links": True}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _channel(channel_id: int, guild, **perms) -> MagicMock:
    channel = MagicMock()
    channel.id = channel_id
    channel.mention = f"<#{channel_id}>"
    channel.guild = guild
    channel.position = channel_id
    channel.permissions_for = MagicMock(return_value=_perms(**perms))
    return channel


def _guild(channels=(), roles=()) -> MagicMock:
    guild = MagicMock()
    guild.id = 7
    guild.name = "Test Server"
    guild.me.guild_permissions.mention_everyone = False
    by_id = {c.id: c for c in channels}
    guild.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    roles_by_id = {r.id: r for r in roles}
    guild.get_role = MagicMock(side_effect=lambda rid: roles_by_id.get(rid))
    return guild


def _fields(embed) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields}


# --- /setup status -------------------------------------------------------------


def test_status_flags_everything_missing_on_a_fresh_server() -> None:
    guild = _guild()
    with patch.object(setup_commands, "settings", SimpleNamespace(X_COOKIES="c")):
        embed = setup_commands.build_status_embed(guild, {}, [])

    fields = _fields(embed)
    assert "Not set" in fields["Mod log"] and "/setup modlog" in fields["Mod log"]
    assert "No clans" in fields["Clan watch"] and "/setup clanwatch" in fields["Clan watch"]
    assert "/x setchannel" in fields["X posts"]
    assert "/yt setchannel" in fields["YouTube videos"]
    assert "/product add" in fields["Purchases"]


def test_status_counts_the_subscribe_buttons() -> None:
    with patch.object(setup_commands, "settings", SimpleNamespace(X_COOKIES="")):
        fields = _fields(setup_commands.build_status_embed(_guild(), {}, [], 2))
    assert "2 buttons" in fields["Purchases"]


def test_status_shows_configured_values_and_missing_permissions() -> None:
    guild = _guild()
    log = _channel(1, guild)
    watch = _channel(2, guild, embed_links=False)
    guild = _guild(channels=[log, watch])
    log.guild = watch.guild = guild
    config = {"mod_log_channel_id": 1, "clan_watch_channel_id": 2}
    clans = [{"clan_tag": "#2PP", "clan_name": "July"}]

    with patch.object(setup_commands, "settings", SimpleNamespace(X_COOKIES="")):
        fields = _fields(setup_commands.build_status_embed(guild, config, clans))

    assert fields["Mod log"] == "✅ <#1>"
    assert "July" in fields["Clan watch"] and "#2PP" in fields["Clan watch"]
    assert "missing: Embed Links" in fields["Clan watch"]
    # X needs operator cookies; without them it's unavailable on every server.
    assert "Not available" in fields["X posts"]


def test_status_warns_when_a_ping_role_cannot_notify() -> None:
    role = MagicMock(id=9, mention="<@&9>", mentionable=False)
    guild = _guild(roles=[role])
    config = {"youtube_channel_id": None, "youtube_ping_role_id": 9}

    with patch.object(setup_commands, "settings", SimpleNamespace(X_COOKIES="")):
        fields = _fields(setup_commands.build_status_embed(guild, config, []))

    assert "can't mention it" in fields["YouTube videos"]


# --- channel / role setters ----------------------------------------------------


def _interaction() -> MagicMock:
    inter = MagicMock()
    inter.guild.id = 7
    inter.author.id = 555
    inter.response.send_message = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


def _cog() -> setup_commands.SetupCommands:
    cog = setup_commands.SetupCommands(MagicMock())
    cog.bot.pool = MagicMock()
    return cog


@pytest.mark.asyncio
async def test_modlog_refuses_a_channel_the_bot_cannot_post_in() -> None:
    inter = _interaction()
    channel = _channel(1, inter.guild, send_messages=False)
    cog = _cog()

    with patch.object(setup_commands.storage, "set_channel", AsyncMock()) as set_channel:
        await cog.modlog.callback(cog, inter, channel=channel)

    set_channel.assert_not_awaited()
    assert "Send Messages" in inter.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_modlog_saves_the_channel_for_this_server_only() -> None:
    inter = _interaction()
    channel = _channel(1, inter.guild)
    cog = _cog()

    with patch.object(setup_commands.storage, "set_channel", AsyncMock()) as set_channel:
        await cog.modlog.callback(cog, inter, channel=channel)

    assert set_channel.await_args.args[1:] == (7, "mod_log_channel_id", 1)


@pytest.mark.asyncio
async def test_clanwatch_with_no_channel_turns_it_off() -> None:
    inter = _interaction()
    cog = _cog()

    with patch.object(setup_commands.storage, "set_channel", AsyncMock()) as set_channel:
        await cog.clanwatch.callback(cog, inter, channel=None)

    assert set_channel.await_args.args[1:] == (7, "clan_watch_channel_id", None)
    assert "turned off" in inter.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_pingrole_refuses_everyone() -> None:
    inter = _interaction()
    everyone = MagicMock()
    everyone.is_default.return_value = True
    cog = _cog()

    with patch.object(setup_commands.storage, "set_ping_role", AsyncMock()) as set_role:
        await cog.pingrole.callback(cog, inter, feed="x", role=everyone)

    set_role.assert_not_awaited()


@pytest.mark.asyncio
async def test_pingrole_saves_and_clears() -> None:
    inter = _interaction()
    role = MagicMock(id=9, mention="<@&9>", mentionable=True)
    role.is_default.return_value = False
    cog = _cog()

    with patch.object(setup_commands.storage, "set_ping_role", AsyncMock()) as set_role:
        await cog.pingrole.callback(cog, inter, feed="youtube", role=role)
        assert set_role.await_args.args[1:] == (7, "youtube", 9)
        await cog.pingrole.callback(cog, inter, feed="youtube", role=None)
        assert set_role.await_args.args[1:] == (7, "youtube", None)


@pytest.mark.asyncio
async def test_clan_add_reports_a_setup_error() -> None:
    inter = _interaction()
    cog = _cog()

    with patch.object(
        setup_commands, "add_family_clan", AsyncMock(side_effect=setup_logic.SetupError("nope"))
    ):
        await cog.clan_add.callback(cog, inter, tag="#BAD")

    assert "nope" in inter.edit_original_response.await_args.args[0]


# --- clan validation -------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_family_clan_looks_the_clan_up_and_stores_its_name() -> None:
    with patch.object(setup_logic.storage, "list_clans", AsyncMock(return_value=[])), \
         patch.object(setup_logic, "get_clan", AsyncMock(return_value={"name": "July"})), \
         patch.object(setup_logic.storage, "add_clan", AsyncMock(return_value=True)) as add:
        result = await setup_logic.add_family_clan(MagicMock(), 7, "2pp", 555)

    assert result == {"tag": "#2PP", "name": "July", "added": True}
    assert add.await_args.args[1:] == (7, "#2PP", "July", 555)


@pytest.mark.asyncio
async def test_add_family_clan_rejects_an_unknown_clan() -> None:
    with patch.object(setup_logic.storage, "list_clans", AsyncMock(return_value=[])), \
         patch.object(setup_logic, "get_clan", AsyncMock(return_value=None)), \
         patch.object(setup_logic.storage, "add_clan", AsyncMock()) as add:
        with pytest.raises(setup_logic.SetupError, match="Couldn't find"):
            await setup_logic.add_family_clan(MagicMock(), 7, "#NOPE", 555)
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_family_clan_caps_clans_per_server() -> None:
    full = [{"clan_tag": f"#C{i}", "clan_name": None} for i in range(setup_logic.MAX_CLANS_PER_GUILD)]
    with patch.object(setup_logic.storage, "list_clans", AsyncMock(return_value=full)), \
         patch.object(setup_logic, "get_clan", AsyncMock()) as get_clan:
        with pytest.raises(setup_logic.SetupError, match="Remove one first"):
            await setup_logic.add_family_clan(MagicMock(), 7, "#NEW", 555)
    get_clan.assert_not_awaited()  # no CoC API call once the family is full


@pytest.mark.asyncio
async def test_add_family_clan_already_present_skips_the_api() -> None:
    existing = [{"clan_tag": "#2PP", "clan_name": "July"}]
    with patch.object(setup_logic.storage, "list_clans", AsyncMock(return_value=existing)), \
         patch.object(setup_logic, "get_clan", AsyncMock()) as get_clan:
        result = await setup_logic.add_family_clan(MagicMock(), 7, "#2PP", 555)
    assert result["added"] is False
    get_clan.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_family_clan_rejects_an_empty_tag() -> None:
    with pytest.raises(setup_logic.SetupError):
        await setup_logic.add_family_clan(MagicMock(), 7, "   ", 555)


# --- welcome --------------------------------------------------------------------


def test_welcome_prefers_the_system_channel() -> None:
    guild = _guild()
    system = _channel(5, guild)
    other = _channel(1, guild)
    guild.system_channel = system
    guild.text_channels = [other, system]
    assert setup_commands.welcome_channel(guild) is system


def test_welcome_falls_back_to_the_first_channel_it_can_post_in() -> None:
    guild = _guild()
    guild.system_channel = _channel(5, guild, send_messages=False)
    locked = _channel(1, guild, view_channel=False)
    open_ = _channel(2, guild)
    guild.text_channels = [open_, locked]
    assert setup_commands.welcome_channel(guild) is open_


def test_welcome_gives_up_when_it_can_post_nowhere() -> None:
    guild = _guild()
    guild.system_channel = None
    guild.text_channels = [_channel(1, guild, send_messages=False)]
    assert setup_commands.welcome_channel(guild) is None


# --- /help -----------------------------------------------------------------------


class RosterCommands:  # stands in for the real cog class; only its name matters
    pass


class AccountCommands:
    pass


def _cmd(name, cog, *, admin=False, dm=False, children=None, description="d"):
    return SimpleNamespace(
        name=name,
        cog=cog,
        description=description,
        default_member_permissions=disnake.Permissions(administrator=True) if admin else None,
        children=children or {},
        body=SimpleNamespace(to_dict=lambda: {"contexts": [0, 1] if dm else [0]}),
    )


def _sub(name, description="s"):
    return SimpleNamespace(name=name, body=SimpleNamespace(description=description))


COMMANDS = [
    _cmd("roster", RosterCommands(), admin=True,
         children={"add": _sub("add", "Add one."), "view": _sub("view")}),
    _cmd("link", AccountCommands(), dm=True, description="Link it."),
]


def test_help_hides_admin_commands_from_members() -> None:
    sections = help_commands.collect_help(
        COMMANDS, disnake.Permissions(send_messages=True), in_dm=False
    )
    assert list(sections) == ["Accounts"]


def test_help_shows_admins_every_subcommand() -> None:
    sections = help_commands.collect_help(
        COMMANDS, disnake.Permissions(administrator=True), in_dm=False
    )
    assert ("/roster add", "Add one.") in sections["Rosters"]
    assert ("/roster view", "s") in sections["Rosters"]
    # Categories come out in the configured order, not alphabetically.
    assert list(sections) == ["Accounts", "Rosters"]


def test_help_in_a_dm_lists_only_dm_commands() -> None:
    sections = help_commands.collect_help(COMMANDS, None, in_dm=True)
    assert sections == {"Accounts": [("/link", "Link it.")]}


def test_category_embed_stays_under_discords_limit() -> None:
    lines = [(f"/cmd{i}", "x" * 200) for i in range(50)]
    embed = help_commands.category_embed("Big", lines)
    assert len(embed.description) <= help_commands.MAX_DESCRIPTION_LENGTH
