"""Kick, ban, and unban actions."""
from __future__ import annotations

import disnake

from modules.moderation.validation import ModerationError, parse_user_id, validate_member_target


async def kick_member(
    guild: disnake.Guild,
    target: disnake.Member,
    moderator: disnake.Member,
    reason: str | None,
) -> None:
    validate_member_target(guild, target, moderator)
    try:
        await target.kick(reason=reason)
    except disnake.Forbidden as e:
        raise ModerationError("I don't have permission to kick that member.") from e
    except disnake.HTTPException as e:
        raise ModerationError(f"Failed to kick member: {e.text}") from e


async def ban_member(
    guild: disnake.Guild,
    target: disnake.Member,
    moderator: disnake.Member,
    reason: str | None,
) -> None:
    validate_member_target(guild, target, moderator)
    try:
        await guild.ban(target, reason=reason)
    except disnake.Forbidden as e:
        raise ModerationError("I don't have permission to ban that member.") from e
    except disnake.HTTPException as e:
        raise ModerationError(f"Failed to ban member: {e.text}") from e


async def unban_user(
    guild: disnake.Guild,
    user_id: str,
    moderator: disnake.Member,
    reason: str | None,
) -> tuple[str, int]:
    parsed_id = parse_user_id(user_id)
    try:
        user = await guild.fetch_ban(disnake.Object(id=parsed_id))
    except disnake.NotFound as e:
        raise ModerationError("That user is not banned.") from e
    except disnake.Forbidden as e:
        raise ModerationError("I don't have permission to view bans.") from e
    except disnake.HTTPException as e:
        raise ModerationError(f"Failed to look up ban: {e.text}") from e

    try:
        await guild.unban(user.user, reason=reason)
    except disnake.Forbidden as e:
        raise ModerationError("I don't have permission to unban that user.") from e
    except disnake.HTTPException as e:
        raise ModerationError(f"Failed to unban user: {e.text}") from e

    return str(user.user), parsed_id
