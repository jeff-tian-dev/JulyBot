"""Unit tests for modules.moderation."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from modules.moderation import logging, messages
from modules.moderation.validation import ModerationError, parse_user_id, validate_member_target


def _role(position: int) -> SimpleNamespace:
    return SimpleNamespace(position=position)


def _member(
    member_id: int,
    *,
    top_role_position: int = 1,
    display_name: str = "TestUser",
) -> MagicMock:
    member = MagicMock(spec=disnake.Member)
    member.id = member_id
    member.top_role = _role(top_role_position)
    member.__str__ = MagicMock(return_value=f"{display_name}#0001")
    return member


def _guild(*, owner_id: int = 100, bot_role_position: int = 10) -> MagicMock:
    guild = MagicMock(spec=disnake.Guild)
    guild.owner_id = owner_id
    bot_member = _member(999, top_role_position=bot_role_position)
    guild.me = bot_member
    return guild


def test_pick_ban_quip_returns_known_string() -> None:
    assert messages.pick_ban_quip() in messages.BAN_QUIPS


def test_pick_kick_quip_returns_known_string() -> None:
    assert messages.pick_kick_quip() in messages.KICK_QUIPS


def test_format_public_message_includes_user_and_quip() -> None:
    user = MagicMock()
    user.__str__ = MagicMock(return_value="BadActor#1234")
    result = messages.format_public_message(user, "go to your room.")
    assert result == "BadActor#1234 go to your room."


def test_parse_user_id_accepts_valid_snowflake() -> None:
    assert parse_user_id("1514111681222148219") == 1514111681222148219


def test_parse_user_id_rejects_invalid() -> None:
    with pytest.raises(ModerationError, match="Invalid user ID"):
        parse_user_id("not-a-number")


def test_validate_member_target_rejects_self() -> None:
    guild = _guild()
    moderator = _member(42, top_role_position=5)
    target = _member(42, top_role_position=1)
    with pytest.raises(ModerationError, match="yourself"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_rejects_owner() -> None:
    guild = _guild(owner_id=7)
    moderator = _member(42, top_role_position=20)
    target = _member(7, top_role_position=1)
    with pytest.raises(ModerationError, match="server owner"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_rejects_higher_role_than_bot() -> None:
    guild = _guild(bot_role_position=5)
    moderator = _member(42, top_role_position=20)
    target = _member(55, top_role_position=5)
    with pytest.raises(ModerationError, match="equal or higher role than me"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_rejects_higher_role_than_moderator() -> None:
    guild = _guild(bot_role_position=20)
    moderator = _member(42, top_role_position=3)
    target = _member(55, top_role_position=8)
    with pytest.raises(ModerationError, match="equal or higher role than you"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_allows_valid_target() -> None:
    guild = _guild(bot_role_position=10)
    moderator = _member(42, top_role_position=8)
    target = _member(55, top_role_position=2)
    validate_member_target(guild, target, moderator)


@pytest.mark.asyncio
async def test_send_mod_log_posts_embed_with_reason() -> None:
    channel = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    moderator = _member(42)
    moderator.__str__ = MagicMock(return_value="ModUser#0001")

    with patch("modules.moderation.logging.settings") as mock_settings:
        mock_settings.MOD_LOG_CHANNEL_ID = 1514111681222148219
        await logging.send_mod_log(
            bot,
            action="ban",
            target_label="BadActor#1234",
            target_id=1234,
            moderator=moderator,
            reason="spam",
        )

    bot.get_channel.assert_called_once_with(1514111681222148219)
    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "Member Banned"
    assert embed.colour.value == 0xE74C3C
    fields = {field.name: field.value for field in embed.fields}
    assert "BadActor#1234" in fields["Target"]
    assert "1234" in fields["Target"]
    assert fields["Reason"] == "spam"


@pytest.mark.asyncio
async def test_send_mod_log_uses_dash_when_no_reason() -> None:
    channel = AsyncMock()
    bot = MagicMock()
    bot.get_channel.return_value = channel
    moderator = _member(42)

    with patch("modules.moderation.logging.settings") as mock_settings:
        mock_settings.MOD_LOG_CHANNEL_ID = 1514111681222148219
        await logging.send_mod_log(
            bot,
            action="kick",
            target_label="User#0001",
            target_id=99,
            moderator=moderator,
            reason=None,
        )

    embed = channel.send.await_args.kwargs["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Reason"] == "—"
