"""Tests for the X poller circuit breaker in modules.ping_automator.scheduler.

After X_MAX_CONSECUTIVE_FAILURES consecutive *connection* failures the scheduled
poll_x job alerts once and pauses itself, so a broken X session doesn't spam.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from config.settings import settings
from modules.ping_automator import scheduler


@pytest.fixture(autouse=True)
def _reset_breaker_state():
    """Each test starts with a clean failure streak."""
    scheduler._x_consecutive_connect_failures = 0
    scheduler._x_polling_disabled = False
    yield
    scheduler._x_consecutive_connect_failures = 0
    scheduler._x_polling_disabled = False


def _bot_with_channel():
    bot = MagicMock(spec=disnake.Client)
    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel
    return bot, channel


@pytest.mark.asyncio
async def test_connect_failures_below_threshold_do_not_pause():
    bot, channel = _bot_with_channel()
    sched = MagicMock()
    pool = MagicMock()

    with patch.object(
        scheduler, "poll_x_accounts", AsyncMock(return_value={"connect_failed": True, "errors": 1})
    ):
        for _ in range(settings.X_MAX_CONSECUTIVE_FAILURES - 1):
            await scheduler.poll_x(pool, bot, sched)

    assert scheduler._x_consecutive_connect_failures == settings.X_MAX_CONSECUTIVE_FAILURES - 1
    assert scheduler._x_polling_disabled is False
    sched.pause_job.assert_not_called()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_reaching_threshold_alerts_once_and_pauses():
    bot, channel = _bot_with_channel()
    sched = MagicMock()
    pool = MagicMock()

    with patch.object(
        scheduler, "poll_x_accounts", AsyncMock(return_value={"connect_failed": True, "errors": 1})
    ):
        for _ in range(settings.X_MAX_CONSECUTIVE_FAILURES + 2):
            await scheduler.poll_x(pool, bot, sched)

    assert scheduler._x_polling_disabled is True
    # Alert + pause happen exactly once, even though we polled past the threshold.
    channel.send.assert_awaited_once()
    sched.pause_job.assert_called_once_with("poll_x_accounts")
    alert_text = channel.send.await_args.args[0]
    assert "X monitoring stopped" in alert_text


@pytest.mark.asyncio
async def test_success_resets_failure_streak():
    bot, channel = _bot_with_channel()
    sched = MagicMock()
    pool = MagicMock()

    fail = {"connect_failed": True, "errors": 1}
    ok = {"accounts_polled": 1, "tweets_posted": 0, "errors": 0}
    with patch.object(scheduler, "poll_x_accounts", AsyncMock(side_effect=[fail, fail, ok])):
        await scheduler.poll_x(pool, bot, sched)
        await scheduler.poll_x(pool, bot, sched)
        await scheduler.poll_x(pool, bot, sched)

    assert scheduler._x_consecutive_connect_failures == 0
    assert scheduler._x_polling_disabled is False
    sched.pause_job.assert_not_called()


@pytest.mark.asyncio
async def test_per_account_errors_do_not_trip_breaker():
    """errors>0 without connect_failed (transient per-account) never pauses."""
    bot, channel = _bot_with_channel()
    sched = MagicMock()
    pool = MagicMock()

    with patch.object(
        scheduler,
        "poll_x_accounts",
        AsyncMock(return_value={"accounts_polled": 2, "tweets_posted": 0, "errors": 5}),
    ):
        for _ in range(settings.X_MAX_CONSECUTIVE_FAILURES + 3):
            await scheduler.poll_x(pool, bot, sched)

    assert scheduler._x_consecutive_connect_failures == 0
    assert scheduler._x_polling_disabled is False
    sched.pause_job.assert_not_called()


@pytest.mark.asyncio
async def test_alert_falls_back_to_mod_log_channel():
    """With X_ALERT_CHANNEL_ID unset (0), the alert uses MOD_LOG_CHANNEL_ID."""
    bot, channel = _bot_with_channel()
    sched = MagicMock()
    pool = MagicMock()

    # Settings is a frozen dataclass, so swap the module-level reference wholesale.
    fake_settings = SimpleNamespace(
        X_ALERT_CHANNEL_ID=0,
        MOD_LOG_CHANNEL_ID=4242,
        X_MAX_CONSECUTIVE_FAILURES=3,
    )
    with (
        patch.object(scheduler, "settings", fake_settings),
        patch.object(
            scheduler,
            "poll_x_accounts",
            AsyncMock(return_value={"connect_failed": True, "errors": 1}),
        ),
    ):
        for _ in range(fake_settings.X_MAX_CONSECUTIVE_FAILURES):
            await scheduler.poll_x(pool, bot, sched)

    bot.get_channel.assert_called_with(4242)
    channel.send.assert_awaited_once()
