"""Render and validate /postbase content.

Pool-free and Discord-send-free: this builds the embed and validates user input,
so it's unit-testable without a bot or a database. Persistence lives in
base_storage.py; the interactive buttons live in the Cog.
"""
from __future__ import annotations

import logging
import re
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
# Discord limits.
MAX_TITLE_LENGTH = 256
MAX_EMBED_DESCRIPTION_LENGTH = 4096
# Room for the divider + heading that get prepended to the user's text.
MAX_DESCRIPTION_LENGTH = 2000
MAX_CC_LENGTH = 500
MAX_LINK_LENGTH = 1000
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
    cc: str | None,
    description: str | None,
) -> str:
    """Compose the embed description: a rule, the CC block, then the body.

    The title is NOT part of this — it lives in the embed's native title field,
    which Discord renders bold in its own slot. Any section the poster left
    blank is skipped entirely, and the description gets no heading of its own
    (a "Notes:" label is the poster's to type).
    """
    parts: list[str] = [DIVIDER]
    if cc:
        parts.append(f"{CC_HEADING}\n{cc}")
    if description:
        parts.append(description)

    body = "\n\n".join(parts)
    if len(body) > MAX_EMBED_DESCRIPTION_LENGTH:
        # The title has its own field and its own limit, so only CC and the
        # description count against the description budget — but those two can
        # still overflow together while each is individually valid.
        raise PostError(
            "The CC and description are too long together "
            f"({len(body)} characters — the embed limit is {MAX_EMBED_DESCRIPTION_LENGTH})."
        )
    return body


def build_base_embed(
    *,
    title: str | None,
    cc: str | None,
    description: str | None,
    image_url: str | None = None,
    image_filename: str | None = None,
    author: disnake.abc.User | None = None,
) -> disnake.Embed:
    """Build the base-post embed.

    Pass `image_filename` when the image is being uploaded alongside the embed
    (referenced as ``attachment://``), or `image_url` when re-rendering an
    already-posted image on edit.
    """
    embed = disnake.Embed(
        title=title or None,
        description=build_base_body(cc=cc, description=description),
        colour=BASE_EMBED_COLOUR,
    )
    if image_filename:
        embed.set_image(url=f"attachment://{safe_filename(image_filename)}")
    elif image_url:
        embed.set_image(url=image_url)
    if author is not None:
        embed.set_footer(
            text=f"Posted by {author.display_name}",
            icon_url=author.display_avatar.url if author.display_avatar else None,
        )
    return embed


def embed_from_record(record, author: disnake.abc.User | None = None) -> disnake.Embed:
    """Re-render a stored base post row (used after an edit)."""
    return build_base_embed(
        title=record["title"],
        cc=record["cc"],
        description=record["description"],
        image_url=record["image_url"],
        author=author,
    )


__all__ = [
    "BASE_EMBED_COLOUR",
    "MAX_CC_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    "MAX_LINK_LENGTH",
    "MAX_TITLE_LENGTH",
    "PostError",
    "build_base_body",
    "build_base_embed",
    "embed_from_record",
    "normalize_text",
    "validate_base_input",
    "validate_image",
    "validate_link",
    "validate_target",
]
