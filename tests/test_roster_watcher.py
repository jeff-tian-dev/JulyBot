"""Unit tests for modules.roster.watcher (mocked storage, clan API, bot)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.roster import watcher


def _channel(guild_id: int = 1) -> MagicMock:
    channel = MagicMock()
    channel.send = AsyncMock()
    channel.guild = SimpleNamespace(id=guild_id)
    return channel


def _fake_bot(*channels) -> MagicMock:
    """A bot that resolves channel id 500+guild_id to that guild's channel."""
    by_id = {500 + c.guild.id: c for c in channels}
    bot = MagicMock()
    bot.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    return bot


def _set_families(monkeypatch, families: dict[int, list[str]]) -> None:
    monkeypatch.setattr(
        watcher.guild_config, "clans_by_guild", AsyncMock(return_value=families)
    )


def _membership(**rows) -> dict:
    return {
        tag: {"coc_name": name, "in_clan": in_clan, "left_at": None if in_clan else object(),
              "total_absent_seconds": 0, "clan_name": clan}
        for tag, (name, in_clan, clan) in rows.items()
    }


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Stub the poll's fire-and-forget DB writes (tested on their own elsewhere),
    give guild 1 a one-clan family, and point each guild at channel 500+guild_id."""
    monkeypatch.setattr(watcher.storage, "warm_player_cache", AsyncMock())
    monkeypatch.setattr(watcher.storage, "add_daily_absent", AsyncMock())
    monkeypatch.setattr(watcher, "CLAN_FETCH_RETRY_DELAY_SECONDS", 0)
    _set_families(monkeypatch, {1: ["#2PP"]})
    monkeypatch.setattr(
        watcher.guild_config,
        "get_guild_config",
        AsyncMock(side_effect=lambda pool, gid: {"clan_watch_channel_id": 500 + gid}),
    )


def test_format_alert_variants() -> None:
    left = watcher._format_alert(
        {
            "kind": "left", "tag": "#ABC", "name": "Bob", "seconds": None,
            "from_clan": "July", "to_clan": "دردشه",
        }
    )
    assert "left the family" in left and "Bob" in left
    assert "July" in left and "دردشه" in left and "→" in left  # from → to clan movement

    rejoin = watcher._format_alert(
        {
            "kind": "join", "tag": "#ABC", "name": "Bob", "seconds": 7200,
            "from_clan": "دردشه", "to_clan": "July",
        }
    )
    assert "rejoined" in rejoin and "2h" in rejoin and "→" in rejoin

    first = watcher._format_alert(
        {"kind": "join", "tag": "#ABC", "name": "Bob", "seconds": None}
    )
    assert "joined the family" in first and "out for" not in first  # no absence on first join
    assert "→" not in first  # neither clan known -> no movement fragment


def test_format_move_uses_no_clan_placeholder() -> None:
    # A known destination but unknown origin still renders, filling the gap.
    frag = watcher._format_move(None, "July")
    assert "No clan" in frag and "July" in frag and "→" in frag
    # Nothing known at all -> no fragment.
    assert watcher._format_move(None, None) == ""


@pytest.mark.asyncio
async def test_poll_detects_leave_and_rejoin(monkeypatch) -> None:
    # Two watched tags: #IN was in clan and left; #OUT was out and rejoined.
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(
            return_value=[
                {"guild_id": 1, "roster_name": "War A", "coc_tag": "#IN"},
                {"guild_id": 1, "roster_name": "War A", "coc_tag": "#OUT"},
            ]
        ),
    )
    # The single family clan "July" currently contains #OUT (not #IN).
    monkeypatch.setattr(
        watcher,
        "get_clan",
        AsyncMock(return_value={"name": "July", "memberList": [{"tag": "#OUT", "name": "Al"}]}),
    )
    # #IN left to an external clan; that clan is discovered via a player fetch.
    monkeypatch.setattr(watcher, "get_player", AsyncMock(return_value={"clan": {"name": "Rogues"}}))
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(return_value=_membership(**{"#IN": ("Jeff", True, "July"),
                                               "#OUT": ("Al", False, "Nomads")})),
    )
    monkeypatch.setattr(watcher.storage, "mark_left", AsyncMock())
    monkeypatch.setattr(watcher.storage, "mark_joined", AsyncMock(return_value=3600))
    monkeypatch.setattr(watcher.storage, "seed_membership", AsyncMock())
    monkeypatch.setattr(watcher.storage, "touch_name", AsyncMock())

    channel = _channel()
    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(channel))

    assert result["watched_tags"] == 2 and result["alerts"] == 2
    watcher.storage.mark_left.assert_awaited_once()
    watcher.storage.mark_joined.assert_awaited_once()
    # Written against guild 1, with the leaver's destination clan persisted.
    assert watcher.storage.mark_left.await_args.args[1] == 1
    assert watcher.storage.mark_left.await_args.args[4] == "Rogues"
    # Two messages sent: one leave, one rejoin.
    assert channel.send.await_count == 2
    sent = " ".join(call.args[0] for call in channel.send.await_args_list)
    assert "left the family" in sent and "rejoined" in sent
    # Leave shows July → Rogues; rejoin shows Nomads → July.
    assert "July" in sent and "Rogues" in sent and "Nomads" in sent and "→" in sent


@pytest.mark.asyncio
async def test_poll_seeds_first_sight_silently(monkeypatch) -> None:
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "War A", "coc_tag": "#NEW"}]),
    )
    monkeypatch.setattr(
        watcher,
        "get_clan",
        AsyncMock(return_value={"name": "July", "memberList": [{"tag": "#NEW", "name": "New"}]}),
    )
    monkeypatch.setattr(watcher.storage, "get_membership_map", AsyncMock(return_value={}))
    seed = AsyncMock()
    monkeypatch.setattr(watcher.storage, "seed_membership", seed)

    channel = _channel()
    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(channel))
    assert result["watched_tags"] == 1 and result["alerts"] == 0
    seed.assert_awaited_once()
    assert seed.await_args.args[1] == 1  # seeded for its own server
    assert seed.await_args.args[5] == "July"  # seeds the current family clan name
    channel.send.assert_not_awaited()  # no alert on first sight


@pytest.mark.asyncio
async def test_in_family_if_in_any_clan(monkeypatch) -> None:
    # Two family clans; the watched member is in the SECOND one -> counts as "in".
    _set_families(monkeypatch, {1: ["#A", "#B"]})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "July", "coc_tag": "#X"}]),
    )
    monkeypatch.setattr(
        watcher,
        "get_clan",
        AsyncMock(
            side_effect=[  # clan A empty, B has X
                {"name": "ClanA", "memberList": []},
                {"name": "ClanB", "memberList": [{"tag": "#X", "name": "Xx"}]},
            ]
        ),
    )
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(return_value=_membership(**{"#X": ("Xx", False, "Elsewhere")})),
    )
    monkeypatch.setattr(watcher.storage, "mark_joined", AsyncMock(return_value=600))
    monkeypatch.setattr(watcher.storage, "mark_left", AsyncMock())

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(_channel()))
    assert result["alerts"] == 1
    watcher.storage.mark_joined.assert_awaited_once()  # rejoined via clan B
    watcher.storage.mark_left.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_skips_server_on_any_clan_fetch_failure(monkeypatch) -> None:
    # Second family clan fails to fetch -> skip that server's poll, no state changes.
    _set_families(monkeypatch, {1: ["#A", "#B"]})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "July", "coc_tag": "#IN"}]),
    )
    monkeypatch.setattr(
        watcher,
        "get_clan",
        AsyncMock(
            side_effect=[{"name": "ClanA", "memberList": [{"tag": "#IN"}]}]
            + [None] * watcher.CLAN_FETCH_ATTEMPTS
        ),
    )
    mark_left = AsyncMock()
    monkeypatch.setattr(watcher.storage, "mark_left", mark_left)
    monkeypatch.setattr(watcher.storage, "get_membership_map", AsyncMock())
    channel = _channel()

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(channel))
    assert result["failed_guilds"] == 1 and result["alerts"] == 0
    mark_left.assert_not_awaited()  # no false "left" alerts when a clan is unreachable
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_one_servers_clan_failure_does_not_skip_another(monkeypatch) -> None:
    _set_families(monkeypatch, {1: ["#BROKEN"], 2: ["#OK"]})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[
            {"guild_id": 1, "roster_name": "R", "coc_tag": "#X"},
            {"guild_id": 2, "roster_name": "R", "coc_tag": "#X"},
        ]),
    )
    clans = {"#BROKEN": None, "#OK": {"name": "Ok", "memberList": []}}
    monkeypatch.setattr(watcher, "get_clan", AsyncMock(side_effect=lambda tag: clans[tag]))
    monkeypatch.setattr(watcher, "get_player", AsyncMock(return_value=None))
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(return_value=_membership(**{"#X": ("Xx", True, "Ok")})),
    )
    mark_left = AsyncMock()
    monkeypatch.setattr(watcher.storage, "mark_left", mark_left)

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(_channel(1), _channel(2)))

    assert result["failed_guilds"] == 1
    mark_left.assert_awaited_once()
    assert mark_left.await_args.args[1] == 2  # only the healthy server moved on


@pytest.mark.asyncio
async def test_same_player_is_tracked_separately_per_server(monkeypatch) -> None:
    """#X is in server 1's family but not server 2's: server 2 alone gets a
    "left" alert, in its own channel, and server 1's channel stays quiet."""
    _set_families(monkeypatch, {1: ["#JULY"], 2: ["#UG"]})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[
            {"guild_id": 1, "roster_name": "R", "coc_tag": "#X"},
            {"guild_id": 2, "roster_name": "R", "coc_tag": "#X"},
        ]),
    )
    clans = {
        "#JULY": {"name": "July", "memberList": [{"tag": "#X", "name": "Xx"}]},
        "#UG": {"name": "UG", "memberList": []},
    }
    monkeypatch.setattr(watcher, "get_clan", AsyncMock(side_effect=lambda tag: clans[tag]))
    monkeypatch.setattr(watcher, "get_player", AsyncMock(return_value={"clan": {"name": "July"}}))
    # Both servers last saw #X in their family.
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(side_effect=lambda pool, gid, tags: _membership(**{"#X": ("Xx", True, "fam")})),
    )
    mark_left = AsyncMock()
    monkeypatch.setattr(watcher.storage, "mark_left", mark_left)
    monkeypatch.setattr(watcher.storage, "touch_name", AsyncMock())
    july, ug = _channel(1), _channel(2)

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(july, ug))

    assert result["alerts"] == 1
    mark_left.assert_awaited_once()
    assert mark_left.await_args.args[1] == 2
    ug.send.assert_awaited_once()
    july.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_shared_clan_is_fetched_once_per_poll(monkeypatch) -> None:
    _set_families(monkeypatch, {1: ["#SHARED"], 2: ["#SHARED"]})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[
            {"guild_id": 1, "roster_name": "R", "coc_tag": "#X"},
            {"guild_id": 2, "roster_name": "R", "coc_tag": "#X"},
        ]),
    )
    get_clan = AsyncMock(return_value={"name": "S", "memberList": [{"tag": "#X", "name": "Xx"}]})
    monkeypatch.setattr(watcher, "get_clan", get_clan)
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(return_value=_membership(**{"#X": ("Xx", True, "S")})),
    )

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(_channel(1), _channel(2)))
    assert result["guilds"] == 2
    get_clan.assert_awaited_once()


@pytest.mark.asyncio
async def test_server_without_clans_is_skipped(monkeypatch) -> None:
    _set_families(monkeypatch, {})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "R", "coc_tag": "#X"}]),
    )
    get_clan = AsyncMock()
    monkeypatch.setattr(watcher, "get_clan", get_clan)

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(_channel()))
    assert result["guilds"] == 0
    get_clan.assert_not_awaited()


@pytest.mark.asyncio
async def test_alerts_never_post_to_a_channel_in_another_server(monkeypatch) -> None:
    # Server 1's configured channel id resolves to a channel that is in server 9.
    foreign = _channel(9)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=foreign)

    channel = await watcher._watch_channel(bot, MagicMock(), 1)
    assert channel is None


@pytest.mark.asyncio
async def test_no_channel_set_means_no_alerts(monkeypatch) -> None:
    monkeypatch.setattr(
        watcher.guild_config,
        "get_guild_config",
        AsyncMock(return_value={"clan_watch_channel_id": None}),
    )
    bot = MagicMock()
    assert await watcher._watch_channel(bot, MagicMock(), 1) is None
    bot.get_channel.assert_not_called()


@pytest.mark.asyncio
async def test_post_daily_board_sorts_and_keeps_old(monkeypatch) -> None:
    monkeypatch.setattr(watcher.storage, "get_roster_tags", AsyncMock(return_value=["#A", "#B"]))
    state = AsyncMock(
        return_value={
            "#A": {"coc_name": "Al", "in_clan": True, "daily_absent_seconds": 0},
            "#B": {"coc_name": "Bo", "in_clan": False, "daily_absent_seconds": 3600},
        }
    )
    monkeypatch.setattr(watcher.storage, "get_daily_board_state", state)
    players = {
        "#A": {"name": "Al", "trophies": 3000, "clan": {"tag": "#FAM"}},   # in family
        "#B": {"name": "Bo", "trophies": 5000, "clan": {"tag": "#OTHER"}},  # out of family
    }
    monkeypatch.setattr(watcher, "get_player", AsyncMock(side_effect=lambda t: players[t]))
    set_id = AsyncMock()
    monkeypatch.setattr(watcher.storage, "set_watch_message_id", set_id)

    channel = MagicMock()
    new_msg = MagicMock()
    new_msg.id = 999
    channel.send = AsyncMock(return_value=new_msg)
    channel.get_partial_message = MagicMock()

    roster = {"id": 5, "guild_id": 1, "name": "July", "watch_message_id": 111}
    await watcher._post_daily_board(MagicMock(), channel, 1, roster, {"#FAM"})

    assert state.await_args.args[1] == 1  # this server's daily counters
    channel.send.assert_awaited_once()
    channel.get_partial_message.assert_not_called()  # past reports are kept, not deleted
    assert set_id.await_args.args[1] == 5 and set_id.await_args.args[2] == 999

    desc = channel.send.await_args.kwargs["embed"].description
    assert desc.index("5000") < desc.index("3000")  # trophy-sorted, Bo first
    assert "✅" in desc and "❌" in desc  # Al in family, Bo out
    assert "1h" in desc  # Bo's 3600s of daily out-time


@pytest.mark.asyncio
async def test_run_daily_resets_only_on_clean_run(monkeypatch) -> None:
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_rosters",
        AsyncMock(return_value=[{"id": 5, "guild_id": 1, "name": "July"}]),
    )
    reset = AsyncMock()
    monkeypatch.setattr(watcher.storage, "reset_daily_absent", reset)
    monkeypatch.setattr(watcher, "_post_daily_board", AsyncMock())

    await watcher.run_daily_watchlist(MagicMock(), _fake_bot(_channel()))
    reset.assert_awaited_once()  # clean run -> counters reset
    assert reset.await_args.args[1] == 1


@pytest.mark.asyncio
async def test_run_daily_skips_reset_and_warns_in_chat_on_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_rosters",
        AsyncMock(return_value=[{"id": 5, "guild_id": 1, "name": "July"}]),
    )
    reset = AsyncMock()
    monkeypatch.setattr(watcher.storage, "reset_daily_absent", reset)
    # The board build blows up (e.g. a CoC API timeout).
    monkeypatch.setattr(watcher, "_post_daily_board", AsyncMock(side_effect=RuntimeError("boom")))
    channel = _channel()

    summary = await watcher.run_daily_watchlist(MagicMock(), _fake_bot(channel))

    assert summary["failed_guilds"] == 1
    reset.assert_not_awaited()  # counters preserved for the next run
    channel.send.assert_awaited_once()  # user is told in chat
    notice = channel.send.await_args.args[0]
    assert "couldn't be posted" in notice and "July" in notice


@pytest.mark.asyncio
async def test_run_daily_one_servers_failure_does_not_block_anothers_reset(monkeypatch) -> None:
    _set_families(monkeypatch, {1: ["#A"], 2: ["#B"]})
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_rosters",
        AsyncMock(return_value=[
            {"id": 5, "guild_id": 1, "name": "Broken"},
            {"id": 6, "guild_id": 2, "name": "Fine"},
        ]),
    )
    reset = AsyncMock()
    monkeypatch.setattr(watcher.storage, "reset_daily_absent", reset)

    async def board(pool, channel, guild_id, roster, family):
        if guild_id == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(watcher, "_post_daily_board", board)

    await watcher.run_daily_watchlist(MagicMock(), _fake_bot(_channel(1), _channel(2)))

    reset.assert_awaited_once()
    assert reset.await_args.args[1] == 2


@pytest.mark.asyncio
async def test_poll_warms_cache_from_clan_data(monkeypatch) -> None:
    # The poll hands its fetched clan-member data to the player cache.
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "July", "coc_tag": "#X"}]),
    )
    monkeypatch.setattr(
        watcher,
        "get_clan",
        AsyncMock(return_value={"name": "July", "memberList": [{"tag": "#X", "name": "Xx"}]}),
    )
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(return_value=_membership(**{"#X": ("Xx", True, "July")})),
    )
    warm = AsyncMock()
    monkeypatch.setattr(watcher.storage, "warm_player_cache", warm)

    await watcher.poll_watchdog(MagicMock(), _fake_bot(_channel()))
    warm.assert_awaited_once()
    entries = list(warm.await_args.args[1])
    assert ("#X", "Xx", "July", None) in entries  # (tag, name, clan, trophies)


@pytest.mark.asyncio
async def test_poll_retries_a_failed_clan_fetch(monkeypatch) -> None:
    # A clan that fails once then answers must not cost the whole poll.
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "July", "coc_tag": "#X"}]),
    )
    get_clan = AsyncMock(
        side_effect=[None, {"name": "July", "memberList": [{"tag": "#X", "name": "Xx"}]}]
    )
    monkeypatch.setattr(watcher, "get_clan", get_clan)
    monkeypatch.setattr(
        watcher.storage,
        "get_membership_map",
        AsyncMock(return_value=_membership(**{"#X": ("Xx", True, "July")})),
    )

    result = await watcher.poll_watchdog(MagicMock(), _fake_bot(_channel()))
    assert result["failed_guilds"] == 0
    assert get_clan.await_count == 2


@pytest.mark.asyncio
async def test_post_daily_board_spans_several_messages_for_a_big_roster(monkeypatch) -> None:
    tags = [f"#T{i}" for i in range(150)]
    monkeypatch.setattr(watcher.storage, "get_roster_tags", AsyncMock(return_value=tags))
    monkeypatch.setattr(
        watcher.storage,
        "get_daily_board_state",
        AsyncMock(return_value={t: {"coc_name": t, "in_clan": True, "daily_absent_seconds": 0} for t in tags}),
    )
    monkeypatch.setattr(
        watcher,
        "get_player",
        AsyncMock(side_effect=lambda t: {"name": f"Player{t}", "trophies": 5000, "clan": {"tag": "#FAM"}}),
    )
    set_id = AsyncMock()
    monkeypatch.setattr(watcher.storage, "set_watch_message_id", set_id)

    sent = [MagicMock(id=100 + i) for i in range(10)]
    channel = MagicMock()
    channel.send = AsyncMock(side_effect=sent)

    await watcher._post_daily_board(MagicMock(), channel, 1, {"id": 5, "name": "July"}, {"#FAM"})

    assert channel.send.await_count > 1
    lines = [
        line
        for call in channel.send.await_args_list
        for line in call.kwargs["embed"].description.splitlines()
    ]
    assert len(lines) == 150  # nobody cut off
    assert set_id.await_args.args[2] == 100  # first page is the recorded board message
