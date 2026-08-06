"""Unit tests for modules.announce.poster."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import disnake
import pytest

from modules.announce.poster import (
    MAX_EMBED_DESCRIPTION_LENGTH,
    PostError,
    build_embed,
    is_image,
    post_image,
    safe_filename,
    validate_ping_role,
    validate_target,
)


def _perms(**overrides) -> MagicMock:
    perms = MagicMock(spec=disnake.Permissions)
    perms.view_channel = True
    perms.send_messages = True
    perms.send_messages_in_threads = True
    perms.attach_files = True
    for key, value in overrides.items():
        setattr(perms, key, value)
    return perms


def _guild(guild_id: int = 1, *, mention_everyone: bool = True) -> MagicMock:
    guild = MagicMock(spec=disnake.Guild)
    guild.id = guild_id
    me = MagicMock(spec=disnake.Member)
    me.guild_permissions = MagicMock(mention_everyone=mention_everyone)
    guild.me = me
    return guild


def _channel(guild: MagicMock, *, channel_id: int = 55, perms: MagicMock | None = None) -> MagicMock:
    channel = MagicMock(spec=disnake.TextChannel)
    channel.id = channel_id
    channel.guild = guild
    channel.mention = f"<#{channel_id}>"
    channel.permissions_for.return_value = perms or _perms()
    message = MagicMock(spec=disnake.Message)
    message.id = 777
    message.jump_url = "https://discord.com/channels/1/55/777"
    channel.send = AsyncMock(return_value=message)
    return channel


def _attachment(filename: str = "base.png", content_type: str | None = "image/png") -> MagicMock:
    attachment = MagicMock(spec=disnake.Attachment)
    attachment.filename = filename
    attachment.content_type = content_type
    # disnake's Attachment.to_file() carries the attachment's name onto the File.
    file = MagicMock(spec=disnake.File)
    file.filename = filename
    attachment.to_file = AsyncMock(return_value=file)
    return attachment


def _role(guild: MagicMock, *, role_id: int = 42, mentionable: bool = True) -> MagicMock:
    role = MagicMock(spec=disnake.Role)
    role.id = role_id
    role.guild = guild
    role.name = "Members"
    role.mentionable = mentionable
    role.mention = f"<@&{role_id}>"
    return role


def test_is_image_accepts_image_content_type() -> None:
    assert is_image(_attachment("shot.png", "image/png"))


def test_is_image_rejects_non_image_content_type() -> None:
    assert not is_image(_attachment("layout.pdf", "application/pdf"))


def test_is_image_falls_back_to_extension_when_content_type_missing() -> None:
    assert is_image(_attachment("shot.WEBP", None))
    assert not is_image(_attachment("notes.txt", None))


def test_validate_target_rejects_channel_from_another_guild() -> None:
    guild = _guild(1)
    channel = _channel(_guild(2))
    with pytest.raises(PostError, match="isn't in this server"):
        validate_target(channel, guild)


def test_validate_target_rejects_missing_attach_files() -> None:
    guild = _guild()
    channel = _channel(guild, perms=_perms(attach_files=False))
    with pytest.raises(PostError, match="attach files"):
        validate_target(channel, guild)


def test_validate_ping_role_rejects_unmentionable_role_without_permission() -> None:
    guild = _guild(mention_everyone=False)
    with pytest.raises(PostError, match="can't ping"):
        validate_ping_role(_role(guild, mentionable=False), guild)


def test_validate_ping_role_allows_unmentionable_role_with_mention_everyone() -> None:
    guild = _guild(mention_everyone=True)
    validate_ping_role(_role(guild, mentionable=False), guild)


def test_safe_filename_replaces_unsafe_characters() -> None:
    assert safe_filename("my base (v2).png") == "my_base__v2_.png"


def test_safe_filename_falls_back_when_empty() -> None:
    assert safe_filename("") == "image.png"


def test_build_embed_points_at_the_attachment() -> None:
    embed = build_embed("hello", "base.png")
    assert embed.image.url == "attachment://base.png"
    assert embed.description == "hello"


def test_build_embed_converts_literal_newlines() -> None:
    assert build_embed(r"line one\nline two", "base.png").description == "line one\nline two"


def test_build_embed_without_text_has_no_description() -> None:
    embed = build_embed(None, "base.png")
    assert embed.description in (None, "")
    assert embed.image.url == "attachment://base.png"


def test_build_embed_rejects_over_long_text() -> None:
    with pytest.raises(PostError, match="embed limit"):
        build_embed("x" * (MAX_EMBED_DESCRIPTION_LENGTH + 1), "base.png")


@pytest.mark.asyncio
async def test_post_image_sends_embed_without_ping() -> None:
    guild = _guild()
    channel = _channel(guild)
    image = _attachment()

    result = await post_image(channel, image, guild=guild, text="patch notes")

    channel.send.assert_awaited_once()
    kwargs = channel.send.await_args.kwargs
    assert kwargs["content"] is None
    assert kwargs["file"] is image.to_file.return_value
    assert kwargs["embed"].description == "patch notes"
    assert kwargs["embed"].image.url == "attachment://base.png"
    # AllowedMentions.none() => roles is False (suppress all role mentions).
    assert kwargs["allowed_mentions"].roles is False
    assert result.message_id == 777
    assert result.pinged_role_id is None


@pytest.mark.asyncio
async def test_post_image_mentions_ping_role_in_content_not_embed() -> None:
    guild = _guild()
    channel = _channel(guild)
    role = _role(guild)

    result = await post_image(channel, _attachment(), guild=guild, text="war!", ping_role=role)

    kwargs = channel.send.await_args.kwargs
    # A mention inside an embed never pings, so it has to live in the content.
    assert kwargs["content"] == "<@&42>"
    assert "<@&42>" not in (kwargs["embed"].description or "")
    assert kwargs["allowed_mentions"].roles == [role]
    assert result.pinged_role_id == 42


@pytest.mark.asyncio
async def test_post_image_sanitises_filename_for_embed_reference() -> None:
    guild = _guild()
    channel = _channel(guild)
    image = _attachment("my base (v2).png")

    await post_image(channel, image, guild=guild)

    kwargs = channel.send.await_args.kwargs
    assert kwargs["file"].filename == "my_base__v2_.png"
    assert kwargs["embed"].image.url == "attachment://my_base__v2_.png"


@pytest.mark.asyncio
async def test_post_image_rejects_non_image_attachment() -> None:
    guild = _guild()
    channel = _channel(guild)

    with pytest.raises(PostError, match="isn't an image"):
        await post_image(channel, _attachment("plan.pdf", "application/pdf"), guild=guild)

    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_image_wraps_http_failure() -> None:
    guild = _guild()
    channel = _channel(guild)
    error = disnake.HTTPException(MagicMock(status=413), "Payload Too Large")
    channel.send = AsyncMock(side_effect=error)

    with pytest.raises(PostError, match="Failed to post the image"):
        await post_image(channel, _attachment(), guild=guild)
