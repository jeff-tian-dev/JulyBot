"""Unit tests for modules.legend_tracker."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.legend_tracker import poller, snapshots


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


@pytest.mark.asyncio
async def test_get_legend_stats_not_in_legend() -> None:
    not_in_legend_player = {"league": {"id": 29000021}, "trophies": 4500}
    with patch.object(poller, "get_player", AsyncMock(return_value=not_in_legend_player)):
        result = await poller.get_legend_stats("#ABC123")
    assert result is None


@pytest.mark.asyncio
async def test_compute_day_diff_missing_snapshot() -> None:
    pool = _fake_pool(MagicMock())
    with patch.object(snapshots, "get_snapshot", AsyncMock(return_value=None)):
        result = await snapshots.compute_day_diff(pool, "#ABC123")
    assert result is None


@pytest.mark.asyncio
async def test_save_snapshot_duplicate() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 0")
    pool = _fake_pool(conn)

    stats = {
        "coc_tag": "#ABC123",
        "trophies": 5500,
        "attacks_done": 3,
        "defenses_done": 2,
        "attack_wins": 60,
        "defense_wins": 30,
    }
    inserted = await snapshots.save_snapshot(pool, stats)
    assert inserted is False
