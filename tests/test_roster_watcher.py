"""Unit tests for modules.roster.watcher (mocked storage, clan API, bot)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.roster import watcher


def _fake_bot(channel) -> MagicMock:
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    return bot


@pytest.fixture(autouse=True)
def _stub_side_effects(monkeypatch):
    """Stub the poll's fire-and-forget DB writes (tested on their own elsewhere)."""
    monkeypatch.setattr(watcher.storage, "warm_player_cache", AsyncMock())
    monkeypatch.setattr(watcher.storage, "add_daily_absent", AsyncMock())


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
    # (COC_CLAN_TAG is set to "#2PP" by conftest, so the poll isn't skipped.)
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
        AsyncMock(
            return_value={
                "#IN": {"coc_name": "Jeff", "in_clan": True, "left_at": None,
                        "total_absent_seconds": 0, "clan_name": "July"},
                "#OUT": {"coc_name": "Al", "in_clan": False, "left_at": object(),
                         "total_absent_seconds": 0, "clan_name": "Nomads"},
            }
        ),
    )
    monkeypatch.setattr(watcher.storage, "mark_left", AsyncMock())
    monkeypatch.setattr(watcher.storage, "mark_joined", AsyncMock(return_value=3600))
    monkeypatch.setattr(watcher.storage, "seed_membership", AsyncMock())
    monkeypatch.setattr(watcher.storage, "touch_name", AsyncMock())

    channel = MagicMock()
    channel.send = AsyncMock()
    bot = _fake_bot(channel)

    result = await watcher.poll_clan_watch(MagicMock(), bot)

    assert result["watched_tags"] == 2 and result["alerts"] == 2
    watcher.storage.mark_left.assert_awaited_once()
    watcher.storage.mark_joined.assert_awaited_once()
    # The destination clan of the leaver was persisted via mark_left.
    assert watcher.storage.mark_left.await_args.args[3] == "Rogues"
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

    channel = MagicMock()
    channel.send = AsyncMock()

    result = await watcher.poll_clan_watch(MagicMock(), _fake_bot(channel))
    assert result["watched_tags"] == 1 and result["alerts"] == 0
    seed.assert_awaited_once()
    assert seed.await_args.args[4] == "July"  # seeds the current family clan name
    channel.send.assert_not_awaited()  # no alert on first sight


@pytest.mark.asyncio
async def test_in_family_if_in_any_clan(monkeypatch) -> None:
    # Two family clans; the watched member is in the SECOND one -> counts as "in".
    monkeypatch.setattr(watcher, "family_tags", lambda: ["#A", "#B"])
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
        AsyncMock(
            return_value={
                "#X": {"coc_name": "Xx", "in_clan": False, "left_at": object(),
                       "total_absent_seconds": 0, "clan_name": "Elsewhere"}
            }
        ),
    )
    monkeypatch.setattr(watcher.storage, "mark_joined", AsyncMock(return_value=600))
    monkeypatch.setattr(watcher.storage, "mark_left", AsyncMock())
    channel = MagicMock()
    channel.send = AsyncMock()

    result = await watcher.poll_clan_watch(MagicMock(), _fake_bot(channel))
    assert result["alerts"] == 1
    watcher.storage.mark_joined.assert_awaited_once()  # rejoined via clan B
    watcher.storage.mark_left.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_aborts_on_any_clan_fetch_failure(monkeypatch) -> None:
    # Second family clan fails to fetch -> skip the whole poll, no state changes.
    monkeypatch.setattr(watcher, "family_tags", lambda: ["#A", "#B"])
    monkeypatch.setattr(
        watcher.storage,
        "get_watched_tags",
        AsyncMock(return_value=[{"guild_id": 1, "roster_name": "July", "coc_tag": "#IN"}]),
    )
    monkeypatch.setattr(
        watcher,
        "get_clan",
        AsyncMock(side_effect=[{"name": "ClanA", "memberList": [{"tag": "#IN"}]}, None]),
    )
    mark_left = AsyncMock()
    monkeypatch.setattr(watcher.storage, "mark_left", mark_left)
    monkeypatch.setattr(watcher.storage, "get_membership_map", AsyncMock())
    channel = MagicMock()
    channel.send = AsyncMock()

    result = await watcher.poll_clan_watch(MagicMock(), _fake_bot(channel))
    assert result == {"error": "clan fetch failed"}
    mark_left.assert_not_awaited()  # no false "left" alerts when a clan is unreachable
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_daily_board_sorts_and_deletes_old(monkeypatch) -> None:
    monkeypatch.setattr(watcher.storage, "get_roster_tags", AsyncMock(return_value=["#A", "#B"]))
    monkeypatch.setattr(
        watcher.storage,
        "get_daily_board_state",
        AsyncMock(
            return_value={
                "#A": {"coc_name": "Al", "in_clan": True, "daily_absent_seconds": 0},
                "#B": {"coc_name": "Bo", "in_clan": False, "daily_absent_seconds": 3600},
            }
        ),
    )
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
    await watcher._post_daily_board(MagicMock(), channel, roster, {"#FAM"})

    channel.send.assert_awaited_once()
    channel.get_partial_message.assert_not_called()  # past reports are kept, not deleted
    assert set_id.await_args.args[1] == 5 and set_id.await_args.args[2] == 999

    desc = channel.send.await_args.kwargs["embed"].description
    assert desc.index("5000") < desc.index("3000")  # trophy-sorted, Bo first
    assert "✅" in desc and "❌" in desc  # Al in family, Bo out
    assert "1h" in desc  # Bo's 3600s of daily out-time


@pytest.mark.asyncio
async def test_poll_warms_cache_from_clan_data(monkeypatch) -> None:
    # The poll hands its fetched clan-member data to the player cache.
    monkeypatch.setattr(watcher, "family_tags", lambda: ["#A"])
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
        AsyncMock(
            return_value={
                "#X": {"coc_name": "Xx", "in_clan": True, "left_at": None,
                       "total_absent_seconds": 0, "clan_name": "July"}
            }
        ),
    )
    warm = AsyncMock()
    monkeypatch.setattr(watcher.storage, "warm_player_cache", warm)

    await watcher.poll_clan_watch(MagicMock(), _fake_bot(MagicMock()))
    warm.assert_awaited_once()
    entries = list(warm.await_args.args[1])
    assert ("#X", "Xx", "July", None) in entries  # (tag, name, clan, trophies)
