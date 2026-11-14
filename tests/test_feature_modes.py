"""On / testing / off modes: modules.guild_config.modes, how the X/YouTube/watchdog
posters follow them, and the /toggle command."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from database import models
from discord_bot.commands import setup_commands
from modules.guild_config import modes
from modules.roster import watcher


class _FakePoolAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolAcquireCtx(conn))
    return pool


# --- modes -----------------------------------------------------------------------


def test_each_mode_picks_its_channel() -> None:
    assert modes.target_channel_id(modes.ON, 10, 99) == 10
    assert modes.target_channel_id(modes.TESTING, 10, 99) == 99
    assert modes.target_channel_id(modes.OFF, 10, 99) is None
    # Testing without a test channel posts nowhere, never to the real channel.
    assert modes.target_channel_id(modes.TESTING, 10, None) is None


def test_defaults_are_on_except_roll_call() -> None:
    assert modes.mode_of({}, "rollcall") == modes.OFF
    for feature in ("watchdog", "x", "youtube"):
        assert modes.mode_of({}, feature) == modes.ON
    assert modes.mode_of({"twitter_mode": "bogus"}, "x") == modes.ON


@pytest.mark.asyncio
async def test_set_mode_keeps_the_old_enabled_flag_in_step() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await modes.set_mode(_fake_pool(conn), 7, "x", modes.OFF)
    sql, *args = conn.execute.await_args.args
    assert "twitter_mode" in sql and "twitter_enabled" in sql
    assert args == [7, "off", False]

    await modes.set_mode(_fake_pool(conn), 7, "youtube", modes.TESTING)
    assert conn.execute.await_args.args[1:] == (7, "testing", True)


@pytest.mark.asyncio
async def test_set_mode_for_roll_call_writes_only_its_mode() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    await modes.set_mode(_fake_pool(conn), 7, "rollcall", modes.ON)
    sql, *args = conn.execute.await_args.args
    assert "rollcall_mode" in sql and "enabled" not in sql
    assert args == [7, "on"]


@pytest.mark.asyncio
async def test_set_mode_rejects_an_unknown_mode() -> None:
    with pytest.raises(ValueError):
        await modes.set_mode(MagicMock(), 7, "x", "loud")


def test_migration_carries_paused_feeds_over_as_off() -> None:
    sql = models.ALTER_GUILD_SETTINGS_MODES
    assert "twitter_mode = 'off' WHERE twitter_enabled IS FALSE" in sql
    assert "youtube_mode = 'off' WHERE youtube_enabled IS FALSE" in sql
    assert "rollcall_mode VARCHAR(10) NOT NULL DEFAULT 'off'" in sql


@pytest.mark.asyncio
async def test_feature_channel_off_never_looks_a_channel_up(monkeypatch) -> None:
    monkeypatch.setattr(
        modes.storage, "get_guild_config",
        AsyncMock(return_value={"clan_watch_channel_id": 5, "watchdog_mode": "off"}),
    )
    bot = MagicMock()
    assert await modes.feature_channel(bot, MagicMock(), 1, "watchdog") == ("off", None)
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_feature_channel_testing_resolves_the_test_channel(monkeypatch) -> None:
    monkeypatch.setattr(
        modes.storage, "get_guild_config",
        AsyncMock(return_value={"clan_watch_channel_id": 5, "test_channel_id": 6,
                                "watchdog_mode": "testing"}),
    )
    test_channel = SimpleNamespace(guild=SimpleNamespace(id=1))
    bot = MagicMock()
    bot.get_channel = MagicMock(side_effect=lambda cid: test_channel if cid == 6 else None)
    assert await modes.feature_channel(bot, MagicMock(), 1, "watchdog") == ("testing", test_channel)


def test_role_preview_is_the_plain_role_name() -> None:
    guild = MagicMock()
    guild.get_role = MagicMock(return_value=SimpleNamespace(name="News"))
    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=guild)
    assert modes.role_preview(bot, 1, 9) == "@News"
    bot.get_guild = MagicMock(return_value=None)
    assert modes.role_preview(bot, 1, 9) == "@ping role"


# --- YouTube / X follow the mode ------------------------------------------------------


def _video():
    from modules.youtube_feed import fetcher

    return fetcher.VideoEntry(
        video_id="newVideo009", title="T", url="https://youtu.be/x", published=None,
        channel_title="C",
    )


def _bot_with_channels(channels: dict) -> MagicMock:
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True
    bot.get_channel.side_effect = lambda cid: channels.get(cid)
    guild = MagicMock()
    guild.get_role = MagicMock(return_value=SimpleNamespace(name="YT Pings"))
    bot.get_guild = MagicMock(return_value=guild)
    return bot


@pytest.mark.asyncio
async def test_youtube_testing_posts_to_test_channel_with_role_name_not_ping() -> None:
    from modules.youtube_feed import fetcher, poller, storage

    real, test = MagicMock(), MagicMock()
    real.send, test.send = AsyncMock(), AsyncMock()
    bot = _bot_with_channels({901: real, 950: test})
    watched = [{
        "guild_id": 1, "channel_id": "UC" + "a" * 22, "last_seen_video_id": "old",
        "youtube_channel_id": 901, "youtube_enabled": True, "youtube_mode": "testing",
        "test_channel_id": 950, "youtube_ping_role_id": 111,
    }]
    claim = AsyncMock(return_value=True)

    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=_video())),
        patch.object(storage, "update_last_seen", AsyncMock()) as update,
        patch.object(storage, "claim_ping_slot", claim),
        patch("modules.youtube_feed.poller.asyncio.sleep", AsyncMock()),
    ):
        await poller.poll_youtube_channels(_fake_pool(MagicMock()), bot)

    real.send.assert_not_awaited()
    kwargs = test.send.await_args.kwargs
    assert kwargs["content"] == "@YT Pings"
    assert kwargs["allowed_mentions"].roles is False and kwargs["allowed_mentions"].users is False
    claim.assert_not_awaited()  # a test post never uses up the real ping's cooldown
    update.assert_awaited_once()  # still counts as seen, so "on" won't repost it


@pytest.mark.asyncio
async def test_youtube_off_mode_posts_nothing() -> None:
    from modules.youtube_feed import fetcher, poller, storage

    real = MagicMock()
    real.send = AsyncMock()
    bot = _bot_with_channels({901: real})
    watched = [{
        "guild_id": 1, "channel_id": "UC" + "a" * 22, "last_seen_video_id": "old",
        "youtube_channel_id": 901, "youtube_enabled": False, "youtube_mode": "off",
        "test_channel_id": None, "youtube_ping_role_id": None,
    }]
    fetch = AsyncMock(return_value=_video())
    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", fetch),
    ):
        await poller.poll_youtube_channels(_fake_pool(MagicMock()), bot)
    real.send.assert_not_awaited()
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_x_testing_posts_to_test_channel_with_role_name_not_ping() -> None:
    from modules.x_monitor import client, embeds, poller, storage

    real, test = MagicMock(), MagicMock()
    real.send, test.send = AsyncMock(), AsyncMock()
    bot = _bot_with_channels({999: real, 950: test})
    tweet = SimpleNamespace(id=101, is_retweet=False)
    app = MagicMock()
    app.get_tweets = AsyncMock(return_value=[tweet])
    accounts = [{
        "guild_id": 1, "username": "someone", "last_seen_tweet_id": 50, "x_channel_id": 999,
        "x_enabled": True, "x_mode": "testing", "test_channel_id": 950, "x_ping_role_id": 222,
    }]
    claim = AsyncMock(return_value=True)

    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "get_client", AsyncMock(return_value=app)),
        patch.object(storage, "get_all_watched_accounts", AsyncMock(return_value=accounts)),
        patch.object(storage, "mark_tweets_seen", AsyncMock(return_value=[101])),
        patch.object(storage, "update_last_seen", AsyncMock()),
        patch.object(storage, "claim_ping_slot", claim),
        patch.object(embeds, "build_tweet_message", return_value=([MagicMock()], [])),
        patch("modules.x_monitor.poller.asyncio.sleep", AsyncMock()),
    ):
        await poller.poll_x_accounts(_fake_pool(MagicMock()), bot)

    real.send.assert_not_awaited()
    kwargs = test.send.await_args.kwargs
    assert kwargs["content"] == "@YT Pings"  # the fake guild's role name
    assert kwargs["allowed_mentions"].roles is False
    claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_x_rows_from_before_toggle_still_follow_the_old_flag() -> None:
    from modules.x_monitor import client, poller, storage

    real = MagicMock()
    real.send = AsyncMock()
    bot = _bot_with_channels({999: real})
    app = MagicMock()
    app.get_tweets = AsyncMock(return_value=[])
    accounts = [{"guild_id": 1, "username": "someone", "last_seen_tweet_id": 50,
                 "x_channel_id": 999, "x_enabled": False}]
    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "get_client", AsyncMock(return_value=app)),
        patch.object(storage, "get_all_watched_accounts", AsyncMock(return_value=accounts)),
    ):
        summary = await poller.poll_x_accounts(_fake_pool(MagicMock()), bot)
    assert summary["accounts_polled"] == 0
    app.get_tweets.assert_not_awaited()


# --- watchdog follows the mode ---------------------------------------------------------


def _guild_channel(guild_id: int = 1) -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.guild = SimpleNamespace(id=guild_id)
    return channel


@pytest.mark.asyncio
async def test_watchdog_testing_sends_alerts_to_the_test_channel(monkeypatch) -> None:
    real, test = _guild_channel(), _guild_channel()
    monkeypatch.setattr(
        watcher.guild_config, "get_guild_config",
        AsyncMock(return_value={"clan_watch_channel_id": 501, "test_channel_id": 502,
                                "watchdog_mode": "testing"}),
    )
    bot = MagicMock()
    bot.get_channel = MagicMock(side_effect=lambda cid: {501: real, 502: test}.get(cid))

    await watcher._post_alerts(
        bot, MagicMock(), 1, [{"kind": "left", "tag": "#A", "name": "Al", "seconds": None}]
    )
    real.send.assert_not_awaited()
    assert "left the family" in test.send.await_args.args[0]


@pytest.mark.asyncio
async def test_watchdog_off_still_ends_the_day_without_posting(monkeypatch) -> None:
    channel = _guild_channel()
    monkeypatch.setattr(
        watcher.storage, "get_watched_rosters",
        AsyncMock(return_value=[{"id": 1, "guild_id": 1, "name": "July", "watch_message_id": None}]),
    )
    monkeypatch.setattr(watcher.guild_config, "clans_by_guild", AsyncMock(return_value={1: ["#2PP"]}))
    monkeypatch.setattr(
        watcher.guild_config, "get_guild_config",
        AsyncMock(return_value={"clan_watch_channel_id": 501, "watchdog_mode": "off"}),
    )
    reset = AsyncMock()
    monkeypatch.setattr(watcher.storage, "reset_daily_absent", reset)
    board = AsyncMock()
    monkeypatch.setattr(watcher, "_post_daily_board", board)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)

    await watcher.run_daily_watchlist(MagicMock(), bot)

    board.assert_not_awaited()
    channel.send.assert_not_awaited()
    reset.assert_awaited_once()  # counters don't pile up while it's off


# --- /toggle and /setup ------------------------------------------------------------


def _interaction() -> MagicMock:
    inter = MagicMock()
    inter.guild.id = 7
    inter.response.send_message = AsyncMock()
    return inter


def _cog() -> setup_commands.SetupCommands:
    cog = setup_commands.SetupCommands(MagicMock())
    cog.bot.pool = MagicMock()
    return cog


def test_toggle_choices_cover_every_feature_and_mode() -> None:
    assert setup_commands.FEATURE_CHOICES == {
        "Roll call": "rollcall", "Watchdog": "watchdog", "X": "x", "YouTube": "youtube",
    }
    assert set(setup_commands.MODE_CHOICES.values()) == set(modes.MODES)


@pytest.mark.asyncio
async def test_toggle_sets_the_mode_for_this_server() -> None:
    inter = _interaction()
    cog = _cog()
    config = {"rollcall_channel_id": 40, "rollcall_mode": "on"}
    with (
        patch.object(setup_commands.modes, "set_mode", AsyncMock()) as set_mode,
        patch.object(setup_commands.storage, "get_guild_config", AsyncMock(return_value=config)),
        patch.object(setup_commands, "settings", SimpleNamespace(X_COOKIES="c")),
    ):
        await cog.toggle.callback(cog, inter, feature="rollcall", mode="on")

    assert set_mode.await_args.args[1:] == (7, "rollcall", "on")
    assert "<#40>" in inter.response.send_message.await_args.args[0]


def test_toggle_reply_says_what_is_missing() -> None:
    assert "/setup testchannel" in setup_commands.toggle_reply({}, "watchdog", modes.TESTING)
    assert "<#9>" in setup_commands.toggle_reply({"test_channel_id": 9}, "x", modes.TESTING)
    assert "/setup rollcall" in setup_commands.toggle_reply({}, "rollcall", modes.ON)
    assert "/yt setchannel" in setup_commands.toggle_reply({}, "youtube", modes.ON)
    assert "off" in setup_commands.toggle_reply({}, "x", modes.OFF)


@pytest.mark.asyncio
async def test_setup_testchannel_and_rollcall_save_their_channels() -> None:
    cog = _cog()
    with patch.object(setup_commands.storage, "set_channel", AsyncMock()) as set_channel:
        await cog.testchannel.callback(cog, _interaction(), channel=None)
        assert set_channel.await_args.args[1:] == (7, "test_channel_id", None)
        await cog.rollcall.callback(cog, _interaction(), channel=None)
        assert set_channel.await_args.args[1:] == (7, "rollcall_channel_id", None)


def test_status_shows_modes_test_channel_and_roll_call() -> None:
    guild = MagicMock()
    guild.name = "S"
    guild.get_channel = MagicMock(return_value=None)
    guild.get_role = MagicMock(return_value=None)
    config = {"watchdog_mode": "testing", "youtube_mode": "off"}

    with patch.object(setup_commands, "settings", SimpleNamespace(X_COOKIES="c")):
        embed = setup_commands.build_status_embed(guild, config, [])
    fields = {f.name: f.value for f in embed.fields}

    assert "Watchdog is in testing mode" in fields["Test channel"]
    assert "Testing" in fields["Watchdog"] and "/setup testchannel" in fields["Watchdog"]
    assert "Off" in fields["Roll call"] and "/setup rollcall" in fields["Roll call"]
    assert "Off" in fields["YouTube videos"]
    assert "On" in fields["X posts"]
