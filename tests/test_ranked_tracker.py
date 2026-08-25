"""Unit tests for modules.ranked_tracker."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.ranked_tracker import group, poller


class _FakeResponse:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.get = MagicMock(return_value=response)


def _member(tag: str, wins: int = 0, losses: int = 0) -> dict:
    return {"playerTag": tag, "playerName": tag, "defenseWinCount": wins, "defenseLoseCount": losses}


def _log(opponent: str, creation_time: str, trophies: int = 5, stars: int = 3, pct: int = 100) -> dict:
    return {
        "opponentPlayerTag": opponent,
        "opponentName": opponent,
        "stars": stars,
        "destructionPercentage": pct,
        "trophies": trophies,
        "creationTime": creation_time,
    }


def test_compute_defense_histogram_matches_example_shape() -> None:
    members = (
        [_member(f"#A{i}", 5, 4) for i in range(55)]  # 9 defenses each
        + [_member(f"#B{i}", 4, 4) for i in range(44)]  # 8 defenses each
        + [_member("#C0", 4, 3)]  # 7 defenses
    )
    histogram = group.compute_defense_histogram(members)
    assert histogram == [(9, 55), (8, 44), (7, 1)]


def test_compute_defense_histogram_includes_zero_bucket() -> None:
    members = [_member("#A", 0, 0), _member("#B", 1, 0)]
    histogram = group.compute_defense_histogram(members)
    assert (0, 1) in histogram
    assert (1, 1) in histogram


def test_compute_defense_histogram_defaults_missing_keys() -> None:
    members = [{"playerTag": "#A"}]
    histogram = group.compute_defense_histogram(members)
    assert histogram == [(0, 1)]


@pytest.mark.asyncio
async def test_resolve_current_group_happy_path() -> None:
    player = {
        "name": "Jeff",
        "currentLeagueGroupTag": "#GROUP1",
        "currentLeagueSeasonId": 2026008,
    }
    with patch.object(poller, "get_player", AsyncMock(return_value=player)):
        name, tag, group_tag, season_id = await group.resolve_current_group("#2pp0jccl")

    assert name == "Jeff"
    assert tag == "#2PP0JCCL"
    assert group_tag == "#GROUP1"
    assert season_id == 2026008


@pytest.mark.asyncio
async def test_resolve_current_group_not_in_ranked_battles() -> None:
    player = {"name": "Jeff", "currentLeagueGroupTag": None, "currentLeagueSeasonId": None}
    with patch.object(poller, "get_player", AsyncMock(return_value=player)):
        with pytest.raises(group.RankedGroupError, match="isn't currently in a Ranked Battles group"):
            await group.resolve_current_group("#2PP0JCCL")


@pytest.mark.asyncio
async def test_resolve_current_group_api_failure() -> None:
    with patch.object(poller, "get_player", AsyncMock(return_value=None)):
        with pytest.raises(group.RankedGroupError, match="Couldn't reach the CoC API"):
            await group.resolve_current_group("#2PP0JCCL")


@pytest.mark.asyncio
async def test_fetch_group_failure_surfaces_status_and_body() -> None:
    error = poller.LeagueGroupFetchError(404, '{"reason":"notFound"}')
    with patch.object(poller, "get_league_group", AsyncMock(side_effect=error)):
        with pytest.raises(group.RankedGroupError, match="HTTP 404") as excinfo:
            await group.fetch_group("#GROUP1", 2026008, "#2PP0JCCL")
    assert "notFound" in str(excinfo.value)


@pytest.mark.asyncio
async def test_get_league_group_includes_player_tag_query_param() -> None:
    """Regression test: the CoC API 400s "Required parameter 'playerTag' missing"
    without this — playerTag is a required query param, not optional."""
    response = _FakeResponse(200, '{"members": [], "defenseLogs": []}')
    session = _FakeSession(response)
    with patch.object(poller, "get_session", AsyncMock(return_value=session)):
        await poller.get_league_group("#GROUP1", 2026008, "#2PP0JCCL")

    url = session.get.call_args.args[0]
    assert "playerTag=%232PP0JCCL" in url or "playerTag=" in url
    assert "2PP0JCCL" in url


@pytest.mark.asyncio
async def test_get_league_group_raises_on_non_200() -> None:
    response = _FakeResponse(400, '{"reason":"badRequest","message":"Required parameter \'playerTag\' missing"}')
    session = _FakeSession(response)
    with patch.object(poller, "get_session", AsyncMock(return_value=session)):
        with pytest.raises(poller.LeagueGroupFetchError) as excinfo:
            await poller.get_league_group("#GROUP1", 2026008, "#2PP0JCCL")
    assert excinfo.value.status == 400
    assert "playerTag" in excinfo.value.body


def test_detect_log_scope_group_wide() -> None:
    members = [_member(f"#P{i}", 4, 4) for i in range(100)]  # 8 defenses each -> 800 total
    logs = [_log(f"#ATK{i}", "20260825T000000.000Z") for i in range(780)]
    assert group.detect_log_scope(logs, members, "#P0") == group.GROUP_WIDE


def test_detect_log_scope_player_scoped() -> None:
    members = [_member(f"#P{i}", 4, 4) for i in range(100)]  # own total = 8, group total = 800
    logs = [_log(f"#ATK{i}", "20260825T000000.000Z") for i in range(8)]
    assert group.detect_log_scope(logs, members, "#P0") == group.PLAYER_SCOPED


def test_detect_log_scope_ambiguous_falls_back_to_player_scoped() -> None:
    members = [_member(f"#P{i}", 2, 2) for i in range(10)]  # own=4, group total=40
    logs = [_log(f"#ATK{i}", "20260825T000000.000Z") for i in range(22)]  # matches neither closely
    assert group.detect_log_scope(logs, members, "#P0") == group.PLAYER_SCOPED


def test_format_last_defenses_sorts_and_truncates() -> None:
    logs = [
        _log("#OLD", "20260820T000000.000Z"),
        _log("#NEW", "20260825T000000.000Z"),
        _log("#MID", "20260822T000000.000Z"),
    ]
    title, body = group.format_last_defenses(logs, group.GROUP_WIDE, "Jeff", limit=2)
    lines = body.split("\n")
    assert len(lines) == 2
    assert lines[0].startswith("#NEW")
    assert lines[1].startswith("#MID")


def test_format_last_defenses_player_scoped_title_flags_limitation() -> None:
    logs = [_log("#ATK", "20260825T000000.000Z")]
    title, _ = group.format_last_defenses(logs, group.PLAYER_SCOPED, "Jeff")
    assert "Jeff" in title
    assert "group-wide data unavailable" in title


def test_format_last_defenses_group_wide_title_has_no_caveat() -> None:
    logs = [_log("#ATK", "20260825T000000.000Z")]
    title, _ = group.format_last_defenses(logs, group.GROUP_WIDE, "Jeff")
    assert "unavailable" not in title


def test_format_last_defenses_signed_trophy_delta() -> None:
    logs = [_log("#ATK", "20260825T000000.000Z", trophies=5)]
    _, body = group.format_last_defenses(logs, group.GROUP_WIDE, "Jeff")
    assert "+5" in body


@pytest.mark.asyncio
async def test_build_group_dashboard_end_to_end() -> None:
    player = {
        "name": "Jeff",
        "currentLeagueGroupTag": "#GROUP1",
        "currentLeagueSeasonId": 2026008,
    }
    members = [_member("#2PP0JCCL", 4, 4)] + [_member(f"#P{i}", 4, 4) for i in range(99)]
    league_group = {
        "members": members,
        "defenseLogs": [_log("#ATK1", "20260825T120000.000Z")],
    }

    with patch.object(poller, "get_player", AsyncMock(return_value=player)), patch.object(
        poller, "get_league_group", AsyncMock(return_value=league_group)
    ):
        embed = await group.build_group_dashboard("#2PP0JCCL")

    assert "Jeff" in embed.title
    field_names = [f.name for f in embed.fields]
    assert "Defenses Received This Week" in field_names
    assert any("Defenses" in name for name in field_names)
