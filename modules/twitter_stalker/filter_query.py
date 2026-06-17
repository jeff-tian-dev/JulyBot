"""Build and validate twitterapi.io filter rule query strings."""
from __future__ import annotations

MAX_FILTER_VALUE_LEN = 255
_OR_SEPARATOR = " OR "


def build_filter_value(usernames: list[str]) -> str:
    """Build `from:a OR from:b` query string."""
    if not usernames:
        return ""
    return _OR_SEPARATOR.join(f"from:{u}" for u in sorted(usernames))


def validate_filter_capacity(usernames: list[str]) -> None:
    value = build_filter_value(usernames)
    if len(value) > MAX_FILTER_VALUE_LEN:
        raise ValueError(
            f"Stalk list is too long for one filter rule ({len(value)}/{MAX_FILTER_VALUE_LEN} chars). "
            "Remove some accounts with /unstalk."
        )
