"""Pre-flight checks for moderation actions."""
from __future__ import annotations

import disnake


class ModerationError(Exception):
    """User-facing moderation failure."""


def parse_user_id(user_id: str) -> int:
    try:
        parsed = int(user_id)
    except ValueError as e:
        raise ModerationError(f"Invalid user ID: {user_id!r}") from e
    if parsed <= 0:
        raise ModerationError(f"Invalid user ID: {user_id!r}")
    return parsed


def validate_member_target(
    guild: disnake.Guild,
    target: disnake.Member,
    moderator: disnake.Member,
) -> None:
    if target.id == moderator.id:
        raise ModerationError("You can't moderate yourself.")

    if target.id == guild.owner_id:
        raise ModerationError("You can't moderate the server owner.")

    bot_member = guild.me
    if bot_member is None:
        raise ModerationError("I'm not in this server.")

    if target.top_role.position >= bot_member.top_role.position and target.id != guild.owner_id:
        raise ModerationError("I can't moderate someone with an equal or higher role than me.")

    if target.top_role.position >= moderator.top_role.position and moderator.id != guild.owner_id:
        raise ModerationError("You can't moderate someone with an equal or higher role than you.")
