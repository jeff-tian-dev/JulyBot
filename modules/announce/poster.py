"""Post an uploaded image as an embed, optionally with text and a role ping.

Pool-free — this only touches the Discord API, so tests mock disnake objects
rather than an asyncpg pool (same shape as modules/moderation/purge.py).
"""
from __future__ import annotations

import logging as _logging
import re
from dataclasses import dataclass

import disnake

logger = _logging.getLogger(__name__)

# Attachments whose content_type starts with this are treated as images.
IMAGE_CONTENT_TYPE_PREFIX = "image/"
# Fallback when Discord doesn't report a content_type on the attachment.
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".heif")
# Discord's per-message content limit. The role mention is all we put there —
# mentions inside an embed never ping, so the ping has to live in the content.
MAX_MESSAGE_LENGTH = 2000
# Discord's embed description limit — where the post text goes.
MAX_EMBED_DESCRIPTION_LENGTH = 4096
# Embed accent stripe (pink).
EMBED_COLOUR = 0xEC4899
# `attachment://` URIs only resolve when the name is a plain filename, so
# anything outside this set is replaced before the file is attached.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")


class PostError(Exception):
    """User-facing failure while posting."""


@dataclass(frozen=True)
class PostResult:
    """Outcome of a successful post."""

    message_id: int
    jump_url: str
    channel_id: int
    pinged_role_id: int | None = None


def is_image(attachment: disnake.Attachment) -> bool:
    """True if the attachment looks like an image (content type, else extension)."""
    content_type = attachment.content_type or ""
    if content_type:
        return content_type.lower().startswith(IMAGE_CONTENT_TYPE_PREFIX)
    return (attachment.filename or "").lower().endswith(IMAGE_EXTENSIONS)


def validate_target(channel: disnake.abc.GuildChannel, guild: disnake.Guild) -> None:
    """Raise PostError unless the bot can post an attachment in `channel`."""
    if getattr(channel, "guild", None) is None or channel.guild.id != guild.id:
        raise PostError("That channel isn't in this server.")

    me = guild.me
    if me is None:
        raise PostError("I'm not in this server.")

    perms = channel.permissions_for(me)
    is_thread = isinstance(channel, disnake.Thread)
    can_send = perms.send_messages_in_threads if is_thread else perms.send_messages
    if not (perms.view_channel and can_send):
        raise PostError(f"I can't send messages in {channel.mention}.")
    if not perms.attach_files:
        raise PostError(f"I don't have permission to attach files in {channel.mention}.")


def validate_ping_role(role: disnake.Role, guild: disnake.Guild) -> None:
    """Raise PostError unless the bot can actually mention `role`."""
    if role.guild.id != guild.id:
        raise PostError("That role isn't from this server.")

    me = guild.me
    if me is None:
        raise PostError("I'm not in this server.")

    # A role that isn't mentionable can still be pinged if the bot has
    # Mention Everyone (allowed_mentions then carries the ping through).
    if not role.mentionable and not me.guild_permissions.mention_everyone:
        raise PostError(
            f"I can't ping **{role.name}** — make the role mentionable, "
            "or grant me the Mention @everyone, @here, and All Roles permission."
        )


def safe_filename(filename: str) -> str:
    """Sanitise an upload's name so `attachment://<name>` resolves in an embed."""
    cleaned = _UNSAFE_FILENAME_CHARS.sub("_", filename or "")
    return cleaned.lstrip(".") or "image.png"


def build_embed(text: str | None, filename: str) -> disnake.Embed:
    """Build the embed carrying the post text and the image.

    Slash-command options can't contain real newlines, so a literal ``\\n``
    typed by the user is converted into a line break.
    """
    description = text.replace("\\n", "\n") if text else None
    if description and len(description) > MAX_EMBED_DESCRIPTION_LENGTH:
        raise PostError(
            f"The text is {len(description)} characters — the embed limit is "
            f"{MAX_EMBED_DESCRIPTION_LENGTH}."
        )

    embed = disnake.Embed(description=description, colour=EMBED_COLOUR)
    embed.set_image(url=f"attachment://{filename}")
    return embed


async def post_image(
    channel: disnake.abc.GuildChannel,
    image: disnake.Attachment,
    *,
    guild: disnake.Guild,
    text: str | None = None,
    ping_role: disnake.Role | None = None,
) -> PostResult:
    """Post `image` into `channel` as an embed, with `text` as the embed body.

    The text sits in the embed description, above the image — one message, one
    embed. `ping_role` is mentioned in the message content instead, because a
    mention inside an embed never notifies anyone.

    Raises PostError for anything the invoker can fix (wrong file type, missing
    permissions, unpingable role, over-long text).
    """
    if not is_image(image):
        raise PostError(
            f"`{image.filename}` isn't an image. "
            f"Supported: {', '.join(ext.lstrip('.') for ext in IMAGE_EXTENSIONS)}."
        )

    validate_target(channel, guild)
    if ping_role is not None:
        validate_ping_role(ping_role, guild)

    content = ping_role.mention if ping_role is not None else None
    allowed_mentions = disnake.AllowedMentions.none()
    if ping_role is not None:
        allowed_mentions.roles = [ping_role]

    try:
        file = await image.to_file()
    except disnake.HTTPException as exc:
        raise PostError(f"Couldn't download the uploaded image: {exc}") from exc

    # The embed references the upload by name, so both must agree after sanitising.
    file.filename = safe_filename(file.filename)
    embed = build_embed(text, file.filename)

    try:
        message = await channel.send(
            content=content,
            embed=embed,
            file=file,
            allowed_mentions=allowed_mentions,
        )
    except disnake.Forbidden as exc:
        raise PostError(f"Discord refused the post in {channel.mention}: {exc.text or exc}") from exc
    except disnake.HTTPException as exc:
        # Most common cause: the file is over the guild's upload size limit.
        raise PostError(f"Failed to post the image: {exc.text or exc}") from exc

    logger.info(
        "Posted image %s to channel %s (ping_role=%s)",
        image.filename,
        channel.id,
        getattr(ping_role, "id", None),
    )
    return PostResult(
        message_id=message.id,
        jump_url=message.jump_url,
        channel_id=channel.id,
        pinged_role_id=ping_role.id if ping_role is not None else None,
    )
