"""Unit tests for modules.moderation."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from datetime import timedelta

import disnake.utils

from modules.moderation import logging, messages, purge
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


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


def _message(*, author_id: int, content: str, age_days: float) -> MagicMock:
    msg = MagicMock()
    msg.id = int(abs(hash((author_id, content, age_days))) % 10**9)
    msg.author = SimpleNamespace(id=author_id)
    msg.content = content
    msg.created_at = disnake.utils.utcnow() - timedelta(days=age_days)
    msg.delete = AsyncMock()
    return msg


def _purge_channel(messages_list: list[MagicMock]) -> MagicMock:
    """A channel/thread mock whose history() yields the given messages."""
    channel = MagicMock()
    channel.id = int(abs(hash(tuple(m.id for m in messages_list))) % 10**9) or 1

    def history(*, limit=None):
        async def _gen():
            for m in messages_list:
                yield m

        return _gen()

    channel.history = history
    channel.delete_messages = AsyncMock()
    return channel


def _purge_guild(channels: list[MagicMock]) -> MagicMock:
    """Guild whose text_channels are all fully manageable; no threads."""
    guild = MagicMock(spec=disnake.Guild)
    guild.me = SimpleNamespace(id=999)
    guild.threads = []
    perms = SimpleNamespace(read_message_history=True, manage_messages=True)
    for ch in channels:
        ch.permissions_for = MagicMock(return_value=perms)
    guild.text_channels = channels
    return guild


@pytest.mark.asyncio
async def test_purge_rejects_empty_word() -> None:
    guild = _purge_guild([])
    mod = SimpleNamespace(id=42)
    target = SimpleNamespace(id=55)
    with pytest.raises(ModerationError, match="non-empty word"):
        await purge.purge_user_messages(guild, target, "   ", mod)


@pytest.mark.asyncio
async def test_purge_allows_self_target() -> None:
    """A moderator may purge their own messages (no self-target guard)."""
    mod_id = 42
    channel = _purge_channel([_message(author_id=mod_id, content="dog", age_days=1)])
    guild = _purge_guild([channel])
    mod = SimpleNamespace(id=mod_id)

    result = await purge.purge_user_messages(guild, mod, "dog", mod)

    assert result.deleted == 1


@pytest.mark.asyncio
async def test_purge_matches_case_insensitive_substring_and_author() -> None:
    target_id = 55
    msgs = [
        _message(author_id=target_id, content="This is a REPORT", age_days=1),   # match
        _message(author_id=target_id, content="nothing here", age_days=1),        # no word
        _message(author_id=999, content="report from someone else", age_days=1),  # wrong author
    ]
    channel = _purge_channel(msgs)
    guild = _purge_guild([channel])

    result = await purge.purge_user_messages(
        guild, SimpleNamespace(id=target_id), "report", SimpleNamespace(id=42)
    )

    assert result.deleted == 1
    assert result.channels_scanned == 1
    # A single recent match is deleted individually, not via bulk.
    channel.delete_messages.assert_not_awaited()
    msgs[0].delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_bulk_deletes_recent_and_individually_deletes_old() -> None:
    target_id = 55
    recent = [_message(author_id=target_id, content=f"spam {i}", age_days=1) for i in range(3)]
    old = _message(author_id=target_id, content="spam old", age_days=30)
    channel = _purge_channel(recent + [old])
    guild = _purge_guild([channel])

    result = await purge.purge_user_messages(
        guild, SimpleNamespace(id=target_id), "spam", SimpleNamespace(id=42)
    )

    assert result.deleted == 4
    # 3 recent -> one bulk call; 1 old -> individual delete.
    channel.delete_messages.assert_awaited_once()
    bulk_arg = channel.delete_messages.await_args.args[0]
    assert len(bulk_arg) == 3
    old.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_counts_unmanageable_channels_as_skipped() -> None:
    ok_channel = _purge_channel([_message(author_id=55, content="spam", age_days=1)])
    blocked = MagicMock()
    blocked.id = 12345
    blocked.permissions_for = MagicMock(
        return_value=SimpleNamespace(read_message_history=False, manage_messages=False)
    )

    guild = MagicMock(spec=disnake.Guild)
    guild.me = SimpleNamespace(id=999)
    guild.threads = []
    ok_channel.permissions_for = MagicMock(
        return_value=SimpleNamespace(read_message_history=True, manage_messages=True)
    )
    guild.text_channels = [ok_channel, blocked]

    result = await purge.purge_user_messages(
        guild, SimpleNamespace(id=55), "spam", SimpleNamespace(id=42)
    )

    assert result.deleted == 1
    assert result.channels_scanned == 1
    assert result.channels_skipped == 1


@pytest.mark.asyncio
async def test_purge_stops_at_deletion_cap(monkeypatch) -> None:
    monkeypatch.setattr(purge, "MAX_DELETIONS_PER_RUN", 3)
    target_id = 55
    # 5 old matches; cap is 3, so only 3 should be deleted this run.
    msgs = [_message(author_id=target_id, content="dog", age_days=30) for _ in range(5)]
    channel = _purge_channel(msgs)
    guild = _purge_guild([channel])

    result = await purge.purge_user_messages(
        guild, SimpleNamespace(id=target_id), "dog", SimpleNamespace(id=42)
    )

    assert result.deleted == 3
    assert result.capped is True


@pytest.mark.asyncio
async def test_purge_cap_counts_only_matches_not_scanned(monkeypatch) -> None:
    """Cap tracks deletions, so non-matching messages never consume the budget."""
    monkeypatch.setattr(purge, "MAX_DELETIONS_PER_RUN", 2)
    target_id = 55
    # Many non-matches interleaved with exactly 2 matches -> deletes 2, not capped early.
    msgs = [_message(author_id=target_id, content="cat", age_days=30) for _ in range(50)]
    msgs.insert(10, _message(author_id=target_id, content="dog", age_days=30))
    msgs.insert(40, _message(author_id=target_id, content="dog", age_days=30))
    channel = _purge_channel(msgs)
    guild = _purge_guild([channel])

    result = await purge.purge_user_messages(
        guild, SimpleNamespace(id=target_id), "dog", SimpleNamespace(id=42)
    )

    assert result.deleted == 2
    assert result.capped is True


@pytest.mark.asyncio
async def test_purge_not_capped_when_under_limit() -> None:
    target_id = 55
    channel = _purge_channel([_message(author_id=target_id, content="dog", age_days=1)])
    guild = _purge_guild([channel])

    result = await purge.purge_user_messages(
        guild, SimpleNamespace(id=target_id), "dog", SimpleNamespace(id=42)
    )

    assert result.deleted == 1
    assert result.capped is False
