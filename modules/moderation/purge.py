"""Purge a member's messages containing a given word across a guild.

Discord exposes no "all messages by user" endpoint, so this walks every
readable text channel and thread, filters history by author + word, and
deletes matches. Messages younger than the bulk-delete cutoff are removed in
batches; older ones are deleted one at a time.
"""
from __future__ import annotations

import asyncio
import logging as _logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import disnake

from modules.moderation.validation import ModerationError

logger = _logging.getLogger(__name__)

# Discord only allows bulk_delete on messages younger than 14 days.
BULK_DELETE_MAX_AGE_DAYS = 14
# Max messages Discord accepts in a single bulk_delete call.
BULK_DELETE_BATCH_SIZE = 100
# Pause between individual (old-message) deletes to stay under rate limits.
INDIVIDUAL_DELETE_DELAY_SECONDS = 0.5
# Stop after this many deletions in one run so old-message deletes stay under
# Discord's rate limits and within the interaction follow-up window. Re-run the
# command to continue where it left off (history is scanned newest-first).
MAX_DELETIONS_PER_RUN = 500


@dataclass
class PurgeResult:
    """Outcome of a purge sweep."""

    deleted: int = 0
    channels_scanned: int = 0
    channels_skipped: int = 0
    failed: int = 0
    capped: bool = False


def _content_matches(content: str, word_lower: str) -> bool:
    return word_lower in content.lower()


def _iter_deletable_channels(guild: disnake.Guild):
    """Yield every text channel and active thread the bot may read history in."""
    me = guild.me
    for channel in guild.text_channels:
        perms = channel.permissions_for(me)
        if perms.read_message_history and perms.manage_messages:
            yield channel
        else:
            # A channel we can't manage is reported as skipped by the caller;
            # signal it by yielding nothing here and letting the caller count.
            continue
    for thread in guild.threads:
        perms = thread.permissions_for(me)
        if perms.read_message_history and perms.manage_messages:
            yield thread


async def _purge_channel(
    channel: disnake.abc.Messageable,
    target_id: int,
    word_lower: str,
    result: PurgeResult,
    cutoff: datetime,
) -> bool:
    """Delete matching messages in one channel/thread, updating ``result``.

    Returns ``True`` if the per-run deletion cap was reached (caller should
    stop scanning further channels).
    """
    recent_batch: list[disnake.Message] = []

    async def flush_recent() -> None:
        while recent_batch:
            chunk = recent_batch[:BULK_DELETE_BATCH_SIZE]
            del recent_batch[:BULK_DELETE_BATCH_SIZE]
            try:
                if len(chunk) == 1:
                    await chunk[0].delete()
                else:
                    await channel.delete_messages(chunk)
                result.deleted += len(chunk)
            except (disnake.Forbidden, disnake.HTTPException) as exc:
                result.failed += len(chunk)
                logger.warning("Bulk delete failed in %s: %s", channel, exc)

    async for message in channel.history(limit=None):
        if message.author.id != target_id:
            continue
        if not _content_matches(message.content, word_lower):
            continue

        if message.created_at >= cutoff:
            recent_batch.append(message)
            if len(recent_batch) >= BULK_DELETE_BATCH_SIZE:
                await flush_recent()
        else:
            try:
                await message.delete()
                result.deleted += 1
            except (disnake.Forbidden, disnake.HTTPException) as exc:
                result.failed += 1
                logger.warning("Delete failed for message %s: %s", message.id, exc)
            await asyncio.sleep(INDIVIDUAL_DELETE_DELAY_SECONDS)

        # Count queued-but-not-yet-deleted messages so we never overshoot.
        if result.deleted + len(recent_batch) >= MAX_DELETIONS_PER_RUN:
            await flush_recent()
            result.capped = True
            return True

    await flush_recent()
    if result.deleted >= MAX_DELETIONS_PER_RUN:
        result.capped = True
        return True
    return False


async def purge_user_messages(
    guild: disnake.Guild,
    target: disnake.Member | disnake.User,
    word: str,
    moderator: disnake.Member,
) -> PurgeResult:
    """Delete every message from ``target`` containing ``word`` (case-insensitive substring).

    Scans all text channels and active threads the bot can read + manage.
    Raises ``ModerationError`` for invalid input.
    """
    word = word.strip()
    if not word:
        raise ModerationError("Provide a non-empty word to purge.")
    if target.id == moderator.id:
        raise ModerationError("You can't purge your own messages with this command.")

    me = guild.me
    if me is None:
        raise ModerationError("I'm not in this server.")

    word_lower = word.lower()
    cutoff = disnake.utils.utcnow() - timedelta(days=BULK_DELETE_MAX_AGE_DAYS)
    result = PurgeResult()

    manageable_channels = list(_iter_deletable_channels(guild))
    manageable_ids = {c.id for c in manageable_channels}

    # Count channels we cannot manage as skipped (transparency in the summary).
    for channel in guild.text_channels:
        if channel.id not in manageable_ids:
            result.channels_skipped += 1

    for channel in manageable_channels:
        try:
            capped = await _purge_channel(channel, target.id, word_lower, result, cutoff)
            result.channels_scanned += 1
        except disnake.Forbidden:
            result.channels_skipped += 1
            logger.warning("Lost access mid-scan in %s; skipping", channel)
            continue
        except disnake.HTTPException as exc:
            result.channels_skipped += 1
            logger.warning("HTTP error scanning %s: %s; skipping", channel, exc)
            continue

        if capped:
            logger.info("Hit per-run deletion cap (%d); stopping early", MAX_DELETIONS_PER_RUN)
            break

    logger.info(
        "Purge by %s: word=%r target=%s deleted=%d scanned=%d skipped=%d failed=%d",
        moderator,
        word,
        target.id,
        result.deleted,
        result.channels_scanned,
        result.channels_skipped,
        result.failed,
    )
    return result
