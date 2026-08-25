"""Unit tests for modules.ranked_tracker.extrapolate."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from modules.ranked_tracker import extrapolate, poller
from modules.ranked_tracker.group import RankedGroupError


def _member(tag: str, attack_wins=6, attack_losses=6, defense_wins=6, defense_losses=6, name=None) -> dict:
    return {
        "playerTag": tag,
        "playerName": name or tag,
        "attackWinCount": attack_wins,
        "attackLoseCount": attack_losses,
        "defenseWinCount": defense_wins,
        "defenseLoseCount": defense_losses,
    }


def _log(trophies: int) -> dict:
    return {"opponentPlayerTag": "#X", "opponentName": "X", "stars": 3, "destructionPercentage": 100, "trophies": trophies}


def test_eligible_members_requires_both_attack_and_defense_over_threshold() -> None:
    eligible = _member("#A", attack_wins=6, attack_losses=6, defense_wins=6, defense_losses=6)  # 12/12
    only_attacks = _member("#B", attack_wins=6, attack_losses=6, defense_wins=2, defense_losses=2)  # 12/4
    only_defenses = _member("#C", attack_wins=2, attack_losses=2, defense_wins=6, defense_losses=6)  # 4/12
    neither = _member("#D", attack_wins=2, attack_losses=2, defense_wins=2, defense_losses=2)  # 4/4

    result = extrapolate.eligible_members([eligible, only_attacks, only_defenses, neither])
    assert result == [eligible]


def test_average_trophies_empty_logs_is_zero() -> None:
    assert extrapolate._average_trophies([]) == 0.0


def test_average_trophies_computes_mean() -> None:
    logs = [_log(10), _log(20), _log(30)]
    assert extrapolate._average_trophies(logs) == 20.0


@pytest.mark.asyncio
async def test_extrapolate_member_computes_projected_total() -> None:
    member = _member("#A", name="Jeff")
    data = {"attackLogs": [_log(10), _log(20)], "defenseLogs": [_log(-5), _log(-5)]}
    with patch.object(poller, "get_league_group", AsyncMock(return_value=data)):
        result = await extrapolate._extrapolate_member("#GROUP1", 2026008, member, asyncio.Semaphore(1))

    assert result["tag"] == "#A"
    assert result["name"] == "Jeff"
    assert result["avg_attack_trophies"] == 15.0
    assert result["avg_defense_trophies"] == -5.0
    assert result["extrapolated_total"] == (15.0 + -5.0) * 30


@pytest.mark.asyncio
async def test_extrapolate_member_returns_none_on_fetch_failure() -> None:
    member = _member("#A")
    error = poller.LeagueGroupFetchError(500, "server error")
    with patch.object(poller, "get_league_group", AsyncMock(side_effect=error)):
        result = await extrapolate._extrapolate_member("#GROUP1", 2026008, member, asyncio.Semaphore(1))
    assert result is None


@pytest.mark.asyncio
async def test_extrapolate_group_returns_top_3_sorted_descending() -> None:
    player = {"name": "Jeff", "currentLeagueGroupTag": "#GROUP1", "currentLeagueSeasonId": 2026008}
    members = [
        _member("#LOW", name="Low"),
        _member("#MID", name="Mid"),
        _member("#HIGH", name="High"),
        _member("#TOP", name="Top"),
        _member("#TINY", attack_wins=1, attack_losses=1, defense_wins=1, defense_losses=1, name="Tiny"),
    ]
    group_data = {"members": members}

    per_member_logs = {
        "#LOW": {"attackLogs": [_log(1)], "defenseLogs": [_log(-1)]},
        "#MID": {"attackLogs": [_log(5)], "defenseLogs": [_log(-1)]},
        "#HIGH": {"attackLogs": [_log(10)], "defenseLogs": [_log(-1)]},
        "#TOP": {"attackLogs": [_log(20)], "defenseLogs": [_log(-1)]},
    }

    async def fake_get_league_group(group_tag, season_id, player_tag):
        if player_tag == "#2PP0JCCL":
            return group_data
        return per_member_logs[player_tag]

    with patch.object(poller, "get_player", AsyncMock(return_value=player)), patch.object(
        poller, "get_league_group", AsyncMock(side_effect=fake_get_league_group)
    ):
        player_name, top = await extrapolate.extrapolate_group("#2pp0jccl")

    assert player_name == "Jeff"
    assert [r["name"] for r in top] == ["Top", "High", "Mid"]
    assert len(top) == extrapolate.TOP_N


@pytest.mark.asyncio
async def test_extrapolate_group_raises_when_no_eligible_members() -> None:
    player = {"name": "Jeff", "currentLeagueGroupTag": "#GROUP1", "currentLeagueSeasonId": 2026008}
    group_data = {"members": [_member("#TINY", 1, 1, 1, 1)]}

    with patch.object(poller, "get_player", AsyncMock(return_value=player)), patch.object(
        poller, "get_league_group", AsyncMock(return_value=group_data)
    ):
        with pytest.raises(RankedGroupError, match="No members"):
            await extrapolate.extrapolate_group("#2PP0JCCL")


@pytest.mark.asyncio
async def test_extrapolate_group_raises_on_group_fetch_failure() -> None:
    player = {"name": "Jeff", "currentLeagueGroupTag": "#GROUP1", "currentLeagueSeasonId": 2026008}
    error = poller.LeagueGroupFetchError(404, "not found")

    with patch.object(poller, "get_player", AsyncMock(return_value=player)), patch.object(
        poller, "get_league_group", AsyncMock(side_effect=error)
    ):
        with pytest.raises(RankedGroupError, match="HTTP 404"):
            await extrapolate.extrapolate_group("#2PP0JCCL")


def test_build_extrapolate_embed_lists_top_members() -> None:
    top = [
        {"tag": "#A", "name": "Alice", "avg_attack_trophies": 20.0, "avg_defense_trophies": -5.0, "extrapolated_total": 450.0},
    ]
    embed = extrapolate.build_extrapolate_embed("Jeff", top)
    assert "Jeff" in embed.title
    assert "Alice" in embed.fields[0].value
    assert "450" in embed.fields[0].value


def test_build_extrapolate_embed_handles_empty_top() -> None:
    embed = extrapolate.build_extrapolate_embed("Jeff", [])
    assert "No eligible members" in embed.fields[0].value
