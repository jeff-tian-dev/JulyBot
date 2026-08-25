"""Unit tests for modules.ranked_tracker.tracking."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from modules.ranked_tracker import poller, tracking
from modules.ranked_tracker.group import LIKELY_TARGET, NOT_FLAGGED, UNKNOWN


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
async def test_start_tracking_normalizes_and_inserts() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    tag = await tracking.start_tracking(_fake_pool(conn), 42, "2pp0jccl")
    assert tag == "#2PP0JCCL"
    conn.execute.assert_awaited_once()
    args = conn.execute.call_args.args
    assert args[1] == 42
    assert args[2] == "#2PP0JCCL"


@pytest.mark.asyncio
async def test_start_tracking_rejects_invalid_tag() -> None:
    conn = MagicMock()
    with pytest.raises(ValueError):
        await tracking.start_tracking(_fake_pool(conn), 42, "")


@pytest.mark.asyncio
async def test_stop_tracking_returns_true_when_removed() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 1")
    removed = await tracking.stop_tracking(_fake_pool(conn), 42, "#2PP0JCCL")
    assert removed is True


@pytest.mark.asyncio
async def test_stop_tracking_returns_false_when_nothing_removed() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 0")
    removed = await tracking.stop_tracking(_fake_pool(conn), 42, "#2PP0JCCL")
    assert removed is False


@pytest.mark.asyncio
async def test_list_tracked_returns_tags() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"coc_tag": "#A"}, {"coc_tag": "#B"}])
    tags = await tracking.list_tracked(_fake_pool(conn), 42)
    assert tags == ["#A", "#B"]


@pytest.mark.asyncio
async def test_check_current_status_unknown_on_resolve_failure() -> None:
    with patch.object(
        tracking, "resolve_current_group", AsyncMock(side_effect=Exception("boom"))
    ):
        status = await tracking._check_current_status("#2PP0JCCL")
    assert status == UNKNOWN


@pytest.mark.asyncio
async def test_check_current_status_unknown_on_group_fetch_failure() -> None:
    with patch.object(
        tracking,
        "resolve_current_group",
        AsyncMock(return_value=("Jeff", "#2PP0JCCL", "#GROUP1", 2026008)),
    ), patch.object(
        poller,
        "get_league_group",
        AsyncMock(side_effect=poller.LeagueGroupFetchError(500, "err")),
    ):
        status = await tracking._check_current_status("#2PP0JCCL")
    assert status == UNKNOWN


@pytest.mark.asyncio
async def test_check_current_status_returns_real_status() -> None:
    members = [{"playerTag": "#2PP0JCCL", "defenseWinCount": 4, "defenseLoseCount": 4}]
    with patch.object(
        tracking,
        "resolve_current_group",
        AsyncMock(return_value=("Jeff", "#2PP0JCCL", "#GROUP1", 2026008)),
    ), patch.object(
        poller, "get_league_group", AsyncMock(return_value={"members": members})
    ):
        status = await tracking._check_current_status("#2PP0JCCL")
    assert status in (LIKELY_TARGET, NOT_FLAGGED)


@pytest.mark.asyncio
async def test_poll_seeds_first_check_silently_without_dm() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"id": 1, "discord_id": 42, "coc_tag": "#A", "last_status": None}]
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    bot = MagicMock()
    bot.fetch_user = AsyncMock()

    with patch.object(tracking, "_check_current_status", AsyncMock(return_value=NOT_FLAGGED)):
        summary = await tracking.poll_ranked_tracking(_fake_pool(conn), bot)

    bot.fetch_user.assert_not_called()
    assert summary["checked"] == 1
    assert summary["notified"] == 0


@pytest.mark.asyncio
async def test_poll_skips_when_status_unchanged() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"id": 1, "discord_id": 42, "coc_tag": "#A", "last_status": NOT_FLAGGED}]
    )
    bot = MagicMock()
    bot.fetch_user = AsyncMock()

    with patch.object(tracking, "_check_current_status", AsyncMock(return_value=NOT_FLAGGED)):
        summary = await tracking.poll_ranked_tracking(_fake_pool(conn), bot)

    bot.fetch_user.assert_not_called()
    assert summary["skipped"] == 1


@pytest.mark.asyncio
async def test_poll_skips_when_status_unknown() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"id": 1, "discord_id": 42, "coc_tag": "#A", "last_status": NOT_FLAGGED}]
    )
    bot = MagicMock()
    bot.fetch_user = AsyncMock()

    with patch.object(tracking, "_check_current_status", AsyncMock(return_value=UNKNOWN)):
        summary = await tracking.poll_ranked_tracking(_fake_pool(conn), bot)

    bot.fetch_user.assert_not_called()
    assert summary["skipped"] == 1


@pytest.mark.asyncio
async def test_poll_dms_on_real_status_change() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"id": 1, "discord_id": 42, "coc_tag": "#A", "last_status": NOT_FLAGGED}]
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")
    user = MagicMock()
    user.send = AsyncMock()
    bot = MagicMock()
    bot.fetch_user = AsyncMock(return_value=user)

    with patch.object(tracking, "_check_current_status", AsyncMock(return_value=LIKELY_TARGET)):
        summary = await tracking.poll_ranked_tracking(_fake_pool(conn), bot)

    user.send.assert_awaited_once()
    assert "#A" in user.send.call_args.args[0]
    assert summary["notified"] == 1


@pytest.mark.asyncio
async def test_poll_handles_dm_failure_without_aborting_other_rows() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"id": 1, "discord_id": 42, "coc_tag": "#A", "last_status": NOT_FLAGGED},
            {"id": 2, "discord_id": 99, "coc_tag": "#B", "last_status": NOT_FLAGGED},
        ]
    )
    conn.execute = AsyncMock(return_value="UPDATE 1")

    blocked_user = MagicMock()
    blocked_user.send = AsyncMock(
        side_effect=disnake.HTTPException(MagicMock(status=403), "Cannot send messages")
    )
    ok_user = MagicMock()
    ok_user.send = AsyncMock()

    bot = MagicMock()
    bot.fetch_user = AsyncMock(side_effect=[blocked_user, ok_user])

    with patch.object(tracking, "_check_current_status", AsyncMock(return_value=LIKELY_TARGET)):
        summary = await tracking.poll_ranked_tracking(_fake_pool(conn), bot)

    assert summary["notified"] == 1
    assert summary["errors"] == 1
    ok_user.send.assert_awaited_once()
