"""Tests for relative last-seen formatting."""
from __future__ import annotations

from datetime import datetime, timezone

from discord_bot.time_format import discord_relative_timestamp
from modules.x_monitor.snowflake import tweet_id_to_datetime


def test_tweet_id_to_datetime_decodes_snowflake() -> None:
    # Known tweet from seventhmonth7 (fetched 2026-06-24).
    dt = tweet_id_to_datetime(2069666543714377785)
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.year == 2026


def test_tweet_id_to_datetime_rejects_zero() -> None:
    assert tweet_id_to_datetime(0) is None


def test_discord_relative_timestamp_format() -> None:
    dt = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    expected = f"<t:{int(dt.timestamp())}:R>"
    assert discord_relative_timestamp(dt) == expected
