"""Discord-friendly relative time formatting."""
from __future__ import annotations

from datetime import datetime, timezone


def discord_relative_timestamp(dt: datetime) -> str:
    """Return a Discord `<t:…:R>` token that renders as e.g. '3 hours ago'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"
