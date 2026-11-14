"""Unit tests for modules.roster.rollcall (mocked storage, clan API and bot)."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.guild_config import modes
from modules.roster import rollcall, watcher

RESET = datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc)


def _missed(**overrides) -> dict:
    row = {"tag": "#A", "name": "Al", "clan": "Rogues", "discord_id": 111}
    row.update(overrides)
    return row


# --- formatting ------------------------------------------------------------------


def test_on_mode_pings_the_discord_user() -> None:
    [message] = rollcall.format_rollcall("July", [_missed()], modes.ON, {}, RESET)
    assert "<@111>" in message and "Al" in message and "#A" in message and "Rogues" in message
    assert "1 missed" in message


def test_testing_mode_shows_the_name_where_the_ping_would_be() -> None:
    [on] = rollcall.format_rollcall("July", [_missed()], modes.ON, {}, RESET)
    [testing] = rollcall.format_rollcall("July", [_missed()], modes.TESTING, {111: "Jeff"}, RESET)
    assert "<@" not in testing
    # Identical apart from the mention being swapped for the plain @name.
    assert testing == on.replace("<@111>", "@Jeff")


def test_members_without_discord_and_without_a_clan() -> None:
    [message] = rollcall.format_rollcall(
        "July", [_missed(discord_id=None, clan=None)], modes.ON, {}, RESET
    )
    assert "<@" not in message and "not in a clan" in message


def test_nobody_missed_says_so() -> None:
    [message] = rollcall.format_rollcall("July", [], modes.ON, {}, RESET)
    assert "everyone was in the family" in message


def test_a_long_list_is_split_under_discords_limit() -> None:
    missed = [_missed(tag=f"#T{i}", name="N" * 40, discord_id=i + 1) for i in range(120)]
    messages = rollcall.format_rollcall("July", missed, modes.ON, {}, RESET)
    assert len(messages) > 1
    assert all(len(m) <= rollcall.MESSAGE_LIMIT for m in messages)
    assert sum(m.count("<@") for m in messages) == 120  # nobody dropped at a split


def test_allowed_mentions_ping_only_listed_users_and_only_when_on() -> None:
    on = rollcall._allowed_mentions(modes.ON, [_missed(), _missed(discord_id=None)])
    assert [u.id for u in on.users] == [111] and on.roles is False and on.everyone is False
    testing = rollcall._allowed_mentions(modes.TESTING, [_missed()])
    assert testing.users is False


# --- the daily run -----------------------------------------------------------------


def _channel(guild_id: int = 1) -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock()
    guild = MagicMock()
    guild.id = guild_id
    guild.get_member = MagicMock(return_value=SimpleNamespace(display_name="Jeff"))
    channel.guild = guild
    return channel


@pytest.fixture
def setup_run(monkeypatch):
    """One server, one watched roster (#IN in the family, #OUT not), one family clan."""
    monkeypatch.setattr(watcher, "CLAN_FETCH_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(
        rollcall.storage, "get_watched_rosters",
        AsyncMock(return_value=[{"id": 3, "guild_id": 1, "name": "July", "watch_message_id": None}]),
    )
    monkeypatch.setattr(rollcall.guild_config, "clans_by_guild", AsyncMock(return_value={1: ["#FAM"]}))
    monkeypatch.setattr(
        rollcall.storage, "get_rollcall_members",
        AsyncMock(return_value=[{"coc_tag": "#IN", "discord_id": 1},
                                {"coc_tag": "#OUT", "discord_id": 111}]),
    )
    monkeypatch.setattr(rollcall.storage, "get_membership_map", AsyncMock(return_value={}))
    monkeypatch.setattr(
        watcher, "get_clan", AsyncMock(return_value={"name": "July", "memberList": [{"tag": "#IN"}]})
    )
    monkeypatch.setattr(
        rollcall, "get_player", AsyncMock(return_value={"name": "Outy", "clan": {"name": "Rogues"}})
    )

    def with_mode(mode: str) -> MagicMock:
        channel = _channel()
        monkeypatch.setattr(
            rollcall.modes.storage, "get_guild_config",
            AsyncMock(return_value={"rollcall_channel_id": 70, "test_channel_id": 71,
                                    "rollcall_mode": mode}),
        )
        return channel

    return with_mode


def _bot(channel) -> MagicMock:
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot


@pytest.mark.asyncio
async def test_on_reports_only_who_missed_and_pings_them(setup_run) -> None:
    channel = setup_run("on")
    bot = _bot(channel)

    summary = await rollcall.run_rollcall(MagicMock(), bot, RESET)

    bot.get_channel.assert_called_with(70)
    content = channel.send.await_args.args[0]
    assert "<@111>" in content and "Outy" in content and "Rogues" in content
    assert "#IN" not in content
    assert [u.id for u in channel.send.await_args.kwargs["allowed_mentions"].users] == [111]
    assert summary == {"guilds": 1, "missed": 1, "failed_guilds": 0}


@pytest.mark.asyncio
async def test_testing_posts_to_the_test_channel_with_names_not_pings(setup_run) -> None:
    channel = setup_run("testing")
    bot = _bot(channel)

    await rollcall.run_rollcall(MagicMock(), bot, RESET)

    bot.get_channel.assert_called_with(71)
    content = channel.send.await_args.args[0]
    assert "@Jeff" in content and "<@" not in content
    assert channel.send.await_args.kwargs["allowed_mentions"].users is False


@pytest.mark.asyncio
async def test_off_posts_nothing_and_skips_the_api(setup_run) -> None:
    channel = setup_run("off")
    await rollcall.run_rollcall(MagicMock(), _bot(channel), RESET)
    channel.send.assert_not_awaited()
    watcher.get_clan.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_clan_fetch_reports_nobody(setup_run, monkeypatch) -> None:
    channel = setup_run("on")
    monkeypatch.setattr(watcher, "get_clan", AsyncMock(return_value=None))

    summary = await rollcall.run_rollcall(MagicMock(), _bot(channel), RESET)

    content = channel.send.await_args.args[0]
    assert "couldn't run" in content and "<@" not in content
    assert channel.send.await_count == 1
    assert summary["failed_guilds"] == 1


@pytest.mark.asyncio
async def test_servers_without_clans_are_skipped(setup_run, monkeypatch) -> None:
    channel = setup_run("on")
    monkeypatch.setattr(rollcall.guild_config, "clans_by_guild", AsyncMock(return_value={}))
    summary = await rollcall.run_rollcall(MagicMock(), _bot(channel), RESET)
    channel.send.assert_not_awaited()
    assert summary["guilds"] == 0


@pytest.mark.asyncio
async def test_get_rollcall_members_dedupes_tags() -> None:
    from modules.roster import storage

    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[
        {"coc_tag": "#A", "discord_id": 1},
        {"coc_tag": "#A", "discord_id": 1},
        {"coc_tag": None, "discord_id": 2},  # a Discord member with no linked account
        {"coc_tag": "#B", "discord_id": None},
    ])

    class _Ctx:
        async def __aenter__(self):
            return conn

        async def __aexit__(self, *exc):
            return None

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx())
    members = await storage.get_rollcall_members(pool, 3)
    assert members == [{"coc_tag": "#A", "discord_id": 1}, {"coc_tag": "#B", "discord_id": None}]
