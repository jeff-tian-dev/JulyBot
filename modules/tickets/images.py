"""Banner images for the ticket panel and ticket types, kept on local disk.

A type's banner is re-uploaded into every new ticket, and the panel's on every
re-render, so the bytes have to live somewhere the bot can read them again.
Discord's own attachment URLs are signed and expire, so they can't be that
place. Files sit under `settings.TICKET_IMAGE_DIR/<guild_id>/` (data/ is
gitignored); the database stores only the filename.

Losing this directory loses the pictures, not the tickets: everything renders
without an image when its file is missing.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

# Formats Discord renders inline in a media gallery.
ALLOWED_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
# Discord's upload limit for a bot on an unboosted server is 10 MB; stay under it.
MAX_IMAGE_BYTES = 8 * 1024 * 1024
PANEL_STEM = "panel"

_REPO_ROOT = Path(__file__).resolve().parents[2]


def type_stem(type_id: int) -> str:
    return f"type-{type_id}"


def extension_of(filename: str | None) -> str | None:
    """The lowercased extension if it's an allowed image type, else None."""
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in ALLOWED_EXTENSIONS else None


def _guild_dir(guild_id: int) -> Path:
    root = Path(settings.TICKET_IMAGE_DIR)
    if not root.is_absolute():
        # launchd doesn't promise a working directory, so anchor on the repo.
        root = _REPO_ROOT / root
    return root / str(int(guild_id))


def _path(guild_id: int, filename: str) -> Path:
    # Filenames are generated here (panel.png, type-3.png), never user input —
    # but refuse anything with a directory part anyway.
    if Path(filename).name != filename:
        raise ValueError(f"Bad ticket image filename {filename!r}")
    return _guild_dir(guild_id) / filename


def _save(guild_id: int, filename: str, data: bytes) -> None:
    path = _path(guild_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _load(guild_id: int, filename: str) -> bytes | None:
    try:
        return _path(guild_id, filename).read_bytes()
    except FileNotFoundError:
        return None


def _delete(guild_id: int, filename: str) -> None:
    try:
        _path(guild_id, filename).unlink()
    except FileNotFoundError:
        pass


async def save(guild_id: int, stem: str, extension: str, data: bytes) -> str:
    """Write an image and return its filename (`<stem><extension>`)."""
    filename = f"{stem}{extension}"
    await asyncio.to_thread(_save, guild_id, filename, data)
    return filename


async def load(guild_id: int, filename: str | None) -> bytes | None:
    """The image's bytes, or None if there is no image or its file is gone."""
    if not filename:
        return None
    try:
        return await asyncio.to_thread(_load, guild_id, filename)
    except (OSError, ValueError):
        logger.exception("Couldn't read ticket image %s for guild=%s", filename, guild_id)
        return None


async def delete(guild_id: int, filename: str | None) -> None:
    if not filename:
        return
    try:
        await asyncio.to_thread(_delete, guild_id, filename)
    except (OSError, ValueError):
        logger.exception("Couldn't delete ticket image %s for guild=%s", filename, guild_id)
