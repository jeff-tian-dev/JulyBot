"""Decode X/Twitter snowflake IDs into UTC datetimes."""
from __future__ import annotations

from datetime import datetime, timezone

# Twitter snowflake epoch: 2010-11-04T01:42:54.657Z
SNOWFLAKE_EPOCH_MS = 1_288_834_974_657


def tweet_id_to_datetime(tweet_id: int) -> datetime | None:
    """Return the tweet creation time encoded in a snowflake ID."""
    if tweet_id <= 0:
        return None
    ms = (tweet_id >> 22) + SNOWFLAKE_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
