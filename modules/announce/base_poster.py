"""Render and validate /postbase content.

Pool-free and Discord-send-free: this builds the embed and validates user input,
so it's unit-testable without a bot or a database. Persistence lives in
base_storage.py; the interactive buttons live in the Cog.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import disnake

from modules.announce.poster import (
    IMAGE_EXTENSIONS,
    PostError,
    is_image,
    safe_filename,
    validate_target,
)

logger = logging.getLogger(__name__)

# Embed accent for base posts — CoC gold, distinct from /post's pink.
BASE_EMBED_COLOUR = 0xE8B923
# A literal run of dashes. Markdown `---` does NOT become a horizontal rule
# inside an embed description — Discord renders it as three bare dashes, which
# looks broken. Keep the long literal run; it reads as a deliberate separator.
DIVIDER = "------------------------"
# The only section heading. The description is rendered bare — a poster who
# wants a "Notes:" label types it themselves as part of the description.
CC_HEADING = "**CC:**"
# Discord limits. The post is a plain message + attachment (attachments render
# wider than embed images), so the whole body shares the 2000-char message
# budget rather than an embed's 4096.
MAX_MESSAGE_LENGTH = 2000
MAX_EMBED_DESCRIPTION_LENGTH = 4096
MAX_TITLE_LENGTH = 256
# Leaves room for the title, CC, divider, and the blank lines between them.
MAX_DESCRIPTION_LENGTH = 1200
MAX_CC_LENGTH = 200
MAX_LINK_LENGTH = 1000
# Downloader lists are truncated to stay under the embed description limit.
MAX_DOWNLOADER_LINES = 40
# Only these schemes are accepted for the layout link — a base link is always a
# normal web/deep link, and anything else (javascript:, data:) is a trap.
ALLOWED_LINK_SCHEMES = ("http", "https")
# Clash layout links look like https://link.clashofclans.com/...?action=OpenLayout
_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str | None) -> str | None:
    """Turn a slash-command option into display text.

    Slash options can't contain real newlines, so a literal ``\\n`` typed by the
    user becomes a line break. Empty/blank input normalises to None.
    """
    if value is None:
        return None
    cleaned = value.replace("\\n", "\n").strip()
    return cleaned or None


def validate_link(link: str) -> str:
    """Return the cleaned layout link, or raise PostError if it's unusable."""
    cleaned = _WHITESPACE.sub("", link or "")
    if not cleaned:
        raise PostError("The link can't be empty.")
    if len(cleaned) > MAX_LINK_LENGTH:
        raise PostError(f"That link is too long (limit {MAX_LINK_LENGTH} characters).")

    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ALLOWED_LINK_SCHEMES or not parsed.netloc:
        raise PostError(
            "The link must be a full http:// or https:// URL "
            "(e.g. a https://link.clashofclans.com/... layout link)."
        )
    return cleaned


def validate_base_input(
    *,
    link: str,
    title: str | None = None,
    cc: str | None = None,
    description: str | None = None,
) -> dict[str, str | None]:
    """Validate + normalise every text field. Returns the cleaned values."""
    cleaned_link = validate_link(link)
    cleaned_title = normalize_text(title)
    cleaned_cc = normalize_text(cc)
    cleaned_description = normalize_text(description)

    if cleaned_title and len(cleaned_title) > MAX_TITLE_LENGTH:
        raise PostError(f"The title is limited to {MAX_TITLE_LENGTH} characters.")
    if cleaned_cc and len(cleaned_cc) > MAX_CC_LENGTH:
        raise PostError(f"The CC text is limited to {MAX_CC_LENGTH} characters.")
    if cleaned_description and len(cleaned_description) > MAX_DESCRIPTION_LENGTH:
        raise PostError(
            f"The description is {len(cleaned_description)} characters — "
            f"the limit is {MAX_DESCRIPTION_LENGTH}."
        )

    return {
        "link": cleaned_link,
        "title": cleaned_title,
        "cc": cleaned_cc,
        "description": cleaned_description,
    }


def validate_image(attachment: disnake.Attachment) -> None:
    """Raise PostError unless the attachment is an image."""
    if not is_image(attachment):
        raise PostError(
            f"`{attachment.filename}` isn't an image. "
            f"Supported: {', '.join(ext.lstrip('.') for ext in IMAGE_EXTENSIONS)}."
        )


def build_base_body(
    *,
    title: str | None = None,
    cc: str | None,
    description: str | None,
) -> str:
    """Compose the whole post body: title, a rule, the CC block, then the notes.

    The title is a `##` markdown heading, which renders larger than an embed's
    title field. Any section the poster left blank is skipped entirely, and the
    description gets no heading of its own (a "Notes:" label is the poster's to
    type).
    """
    parts: list[str] = []
    if title:
        parts.append(f"## {title}")
    parts.append(DIVIDER)
    if cc:
        parts.append(f"{CC_HEADING}\n{cc}")
    if description:
        parts.append(description)

    body = "\n\n".join(parts)
    if len(body) > MAX_MESSAGE_LENGTH:
        # The post is a plain message + attachment (an attachment renders wider
        # than an embed image), so the whole body shares one 2000-char budget.
        raise PostError(
            "The title, CC, and description are too long together "
            f"({len(body)} characters — the message limit is {MAX_MESSAGE_LENGTH})."
        )
    return body


def content_from_record(record) -> str:
    """Rebuild a stored base post's message text (used after an edit)."""
    return build_base_body(
        title=record["title"],
        cc=record["cc"],
        description=record["description"],
    )


def _relative_timestamp(dt: datetime) -> str:
    """A Discord `<t:…:R>` token, which renders as e.g. '3 hours ago'.

    Duplicated from discord_bot.time_format rather than imported: modules/ must
    not depend on the Discord layer. `fetched_at` is a naive TIMESTAMP written
    by NOW(), so it's read as UTC (Supabase runs UTC).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"


def downloaders_embed(rows) -> disnake.Embed:
    """The download-stats panel: a numbered list of everyone who fetched."""
    if not rows:
        return disnake.Embed(
            title="Downloads",
            description="Nobody has fetched this link yet.",
            colour=BASE_EMBED_COLOUR,
        )

    shown = list(rows)[:MAX_DOWNLOADER_LINES]
    lines = []
    for i, row in enumerate(shown, start=1):
        line = f"{i}. <@{row['user_id']}>"
        # `fetched_at` is nullable on rows written before it was recorded, and
        # a Discord <t:…> token renders in each viewer's own timezone.
        fetched_at = row.get("fetched_at") if hasattr(row, "get") else row["fetched_at"]
        if fetched_at is not None:
            line += f" — {_relative_timestamp(fetched_at)}"
        lines.append(line)
    if len(rows) > len(shown):
        lines.append(f"…and {len(rows) - len(shown)} more.")

    embed = disnake.Embed(
        title="Downloads",
        description="\n".join(lines),
        colour=BASE_EMBED_COLOUR,
    )
    embed.set_footer(text=f"{len(rows)} unique download(s)")
    return embed




__all__ = [
    "BASE_EMBED_COLOUR",
    "MAX_CC_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_LINK_LENGTH",
    "MAX_MESSAGE_LENGTH",
    "MAX_TITLE_LENGTH",
    "PostError",
    "build_base_body",
    "content_from_record",
    "downloaders_embed",
    "normalize_text",
    "validate_base_input",
    "validate_image",
    "validate_link",
    "validate_target",
]
