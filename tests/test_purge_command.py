"""Tests for the /purgeword command handler (background purge, progress, cancel)."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import moderation_commands
from discord_bot.commands.moderation_commands import ModerationCommands, PurgeCancelView
from modules.moderation import purge


@pytest.fixture(autouse=True)
def _clean():
    purge._running.clear()
    yield
    purge._running.clear()


def _inter() -> MagicMock:
    inter = MagicMock()
    inter.guild = SimpleNamespace(id=1)
    inter.author = MagicMock()
    inter.author.id = 42
    inter.author.send = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


def _job(result: purge.PurgeResult, *, delay: float = 0.0) -> purge.PurgeJob:
    job = purge.PurgeJob(guild_id=1, result=result, cancel_event=asyncio.Event())

    async def run() -> purge.PurgeResult:
        await asyncio.sleep(delay)
        result.finished = True
        return result

    job.task = asyncio.create_task(run())
    return job


async def _run(inter, job, monkeypatch, **budget) -> AsyncMock:
    for name, value in budget.items():
        monkeypatch.setattr(moderation_commands, name, value)
    cog = ModerationCommands(MagicMock())
    mod_log = AsyncMock()
    with patch.object(purge, "start_purge", return_value=job), patch.object(
        moderation_commands.logging, "send_mod_log", mod_log
    ):
        await cog.purgeword.callback(cog, inter, word="dog", member=None, channel=None)
    return mod_log


@pytest.mark.asyncio
async def test_purgeword_shows_progress_then_summary_with_buttons_removed(monkeypatch) -> None:
    inter = _inter()
    job = _job(purge.PurgeResult(deleted=7, messages_scanned=900), delay=0.05)

    mod_log = await _run(inter, job, monkeypatch, PROGRESS_INTERVAL_SECONDS=0.01)

    edits = inter.edit_original_response.await_args_list
    assert "Purging" in edits[0].kwargs["content"]
    assert isinstance(edits[0].kwargs["view"], PurgeCancelView)
    final = edits[-1].kwargs
    assert "Deleted **7**" in final["content"]
    assert final["view"] is None
    inter.author.send.assert_not_awaited()
    assert "Purged 7 message(s)" in mod_log.await_args.kwargs["reason"]


@pytest.mark.asyncio
async def test_purgeword_dms_summary_once_the_reply_can_no_longer_be_edited(monkeypatch) -> None:
    """Past the 15-minute token window, the final summary goes to DMs."""
    inter = _inter()
    job = _job(purge.PurgeResult(deleted=3), delay=0.05)

    await _run(
        inter, job, monkeypatch,
        PROGRESS_INTERVAL_SECONDS=0.01, INTERACTION_EDIT_BUDGET_SECONDS=0,
    )

    inter.author.send.assert_awaited_once()
    assert "Deleted **3**" in inter.author.send.await_args.args[0]
    # Only the initial progress message; no edits after the budget ran out.
    assert inter.edit_original_response.await_count == 1


@pytest.mark.asyncio
async def test_purgeword_reports_validation_error_without_starting(monkeypatch) -> None:
    inter = _inter()
    inter.response.is_done = MagicMock(return_value=True)
    cog = ModerationCommands(MagicMock())
    with patch.object(
        purge, "start_purge", side_effect=moderation_commands.ModerationError("already running")
    ):
        await cog.purgeword.callback(cog, inter, word="dog", member=None, channel=None)

    assert "already running" in inter.edit_original_response.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_cancel_button_cancels_the_job_and_disables_itself() -> None:
    job = _job(purge.PurgeResult(), delay=10)
    view = PurgeCancelView(job)
    inter = MagicMock()
    inter.response.edit_message = AsyncMock()

    await view.children[0].callback(inter)

    assert job.cancel_event.is_set()
    assert view.children[0].disabled is True
    inter.response.edit_message.assert_awaited_once()
    job.task.cancel()


@pytest.mark.asyncio
async def test_mod_log_notes_a_cancelled_purge(monkeypatch) -> None:
    inter = _inter()
    job = _job(purge.PurgeResult(deleted=2, cancelled=True))

    mod_log = await _run(inter, job, monkeypatch, PROGRESS_INTERVAL_SECONDS=0.01)

    assert mod_log.await_args.kwargs["reason"].endswith("(cancelled)")
    assert "cancelled" in inter.edit_original_response.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_purgecancel_stops_the_running_purge() -> None:
    job = _job(purge.PurgeResult(deleted=9), delay=10)
    purge._running[1] = job
    inter = _inter()
    inter.response.send_message = AsyncMock()
    cog = ModerationCommands(MagicMock())

    await cog.purgecancel.callback(cog, inter)

    assert job.cancel_event.is_set()
    assert "Stopping" in inter.response.send_message.await_args.args[0]
    assert inter.response.send_message.await_args.kwargs["ephemeral"] is True
    job.task.cancel()


@pytest.mark.asyncio
async def test_purgecancel_with_nothing_running() -> None:
    inter = _inter()
    inter.response.send_message = AsyncMock()
    cog = ModerationCommands(MagicMock())

    await cog.purgecancel.callback(cog, inter)

    assert "No purge is running" in inter.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_purgecancel_only_touches_its_own_server() -> None:
    other = _job(purge.PurgeResult(), delay=10)
    purge._running[2] = other  # a different server's purge
    inter = _inter()  # guild id 1
    inter.response.send_message = AsyncMock()
    cog = ModerationCommands(MagicMock())

    await cog.purgecancel.callback(cog, inter)

    assert not other.cancel_event.is_set()
    other.task.cancel()
