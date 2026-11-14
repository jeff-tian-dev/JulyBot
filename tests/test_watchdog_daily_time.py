"""The daily watchdog board follows the CoC legend-day reset (05:00 UTC), not DST."""

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger

from config.settings import settings

EASTERN = ZoneInfo("America/New_York")


def _next_run_eastern(after: datetime) -> datetime:
    # Same trigger create_scheduler builds for the daily_watchlist job.
    trigger = CronTrigger(
        hour=settings.CLAN_WATCH_DAILY_HOUR, minute=0, timezone=settings.CLAN_WATCH_TIMEZONE
    )
    return trigger.get_next_fire_time(None, after).astimezone(EASTERN)


def test_daily_board_runs_at_midnight_eastern_in_winter() -> None:
    run = _next_run_eastern(datetime(2026, 11, 13, 12, 0, tzinfo=EASTERN))
    assert (run.day, run.hour, run.minute) == (14, 0, 0)


def test_daily_board_runs_at_1am_eastern_in_summer() -> None:
    run = _next_run_eastern(datetime(2026, 7, 13, 12, 0, tzinfo=EASTERN))
    assert (run.day, run.hour, run.minute) == (14, 1, 0)
