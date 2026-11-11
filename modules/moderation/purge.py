"""Purge messages containing a given word, optionally narrowed by author or channel.

Discord exposes no "all messages by user" (or "search by word") endpoint, so
this walks every readable channel and thread -- or just the one channel the
caller names -- filters history by word (and author, if given), and deletes
matches.

A purge runs as a background task (``start_purge``) so it is not bound to the
15-minute interaction window and has no per-run deletion cap: it keeps going
until every channel has been read, or until someone cancels it.

Where the time goes, and how each part is kept moving:

* **Reading history** is one request per 100 messages, and disnake serialises
  every request to the same channel+route behind one lock -- so within one
  channel, reads are strictly one page at a time. The speedup is across
  channels: ``SCAN_CONCURRENCY`` channels are read at once.
* **Deleting** never blocks reading. Each channel has a bulk-delete worker
  (messages under 14 days, 100 per call) and a single-delete worker (older
  messages, one call each), fed by queues while the reader keeps paging.
  Those are different Discord routes, so they run alongside the read.
* **With a member named**, history isn't read at all: Discord's message
  search (``GET /guilds/{id}/messages/search``, ``author_id``) returns only
  that member's messages, 25 per request, and the word is checked here. If
  search can't be used it falls back to the full scan, so a search problem
  never makes a purge fail.
* **Every request** (page read, search, or delete) passes ``_RateLimiter`` so the whole
  purge stays under Discord's global 50 requests/second. disnake only reacts
  to a global 429 after the fact, and a flood of 429s counts toward
  Cloudflare's invalid-request ban.
"""
from __future__ import annotations

import asyncio
import logging as _logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import aiohttp
import disnake
from disnake.http import Route

from modules.moderation.validation import ModerationError

logger = _logging.getLogger(__name__)

# Discord only allows bulk_delete on messages younger than 14 days.
BULK_DELETE_MAX_AGE_DAYS = 14
# A message is only bulk-deleted if it is at least this far inside the 14-day
# window *at the moment the batch is sent*. Discord rejects the whole batch if
# a single message has crossed the line, and a long purge can let queued
# messages age past it.
BULK_DELETE_SAFETY_MARGIN = timedelta(minutes=10)
# Max messages Discord accepts in a single bulk_delete call.
BULK_DELETE_BATCH_SIZE = 100
# Max messages Discord returns per history request.
HISTORY_PAGE_SIZE = 100
# How many channels/threads are read in parallel. Within one channel reads are
# serial anyway (disnake holds a per-channel lock), so this is the main lever.
SCAN_CONCURRENCY = 20
# Ceiling on Discord requests per second across the whole purge. Discord's
# global limit is 50/s per bot; this leaves headroom for the rest of the bot.
MAX_REQUESTS_PER_SECOND = 40.0
# Old (single-delete) matches waiting per channel before the reader pauses.
# Bounds memory when reading outruns the ~1-at-a-time old-message deletes.
OLD_DELETE_QUEUE_SIZE = 1000
# Discord's message search returns at most 25 messages per request.
SEARCH_PAGE_SIZE = 25
# Error code in a 202 reply meaning "this server isn't indexed for search yet".
SEARCH_NOT_INDEXED_CODE = 110000
# How many "not indexed yet" replies to wait out before falling back to a full scan.
SEARCH_INDEX_RETRIES = 10
# Wait used when a "not indexed yet" reply says retry_after = 0.
SEARCH_INDEX_DEFAULT_WAIT_SECONDS = 2.0
# Search trails new messages, so this much recent history is also read
# directly from every channel during a member purge.
SEARCH_RECENT_WINDOW = timedelta(hours=1)
# A first search page this size with no message text at all means Discord is
# withholding content; fall back to the full scan rather than delete nothing.
SEARCH_BLANK_CONTENT_MIN_HITS = 10


@dataclass
class PurgeResult:
    """Live progress, then outcome, of a purge. Updated while it runs."""

    deleted: int = 0
    failed: int = 0
    messages_scanned: int = 0
    channels_found: int = 0
    channels_scanned: int = 0
    channels_skipped: int = 0
    # Channels whose archived threads couldn't be listed (bot lacks Manage Threads).
    archived_unreachable: int = 0
    # A member was named, so only their messages were fetched (via search).
    used_search: bool = False
    # Discord said it is still indexing this server's history: older messages
    # may be missing from search results.
    search_incomplete: bool = False
    cancelled: bool = False
    finished: bool = False
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float | None = None

    @property
    def elapsed(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.monotonic()
        return end - self.started_at


@dataclass
class PurgeJob:
    """A purge running in the background. ``await job.task`` for the result."""

    guild_id: int
    result: PurgeResult
    cancel_event: asyncio.Event
    task: asyncio.Task | None = None

    def cancel(self) -> None:
        self.cancel_event.set()


# One running purge per server: two would race to delete the same messages.
_running: dict[int, PurgeJob] = {}


def get_running(guild_id: int) -> PurgeJob | None:
    return _running.get(guild_id)


def cancel_running(guild_id: int) -> PurgeJob | None:
    """Ask this server's running purge to stop. Returns it, or None if none is running.

    The purge stops at its next check, finishes the deletes already in
    flight, and reports as cancelled to whoever started it.
    """
    job = _running.get(guild_id)
    if job is not None:
        job.cancel()
    return job


class _RateLimiter:
    """Spaces calls at least ``1 / per_second`` apart, across all callers."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second if per_second > 0 else 0.0
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            if self._next > now:
                await asyncio.sleep(self._next - now)
                now = self._next
            self._next = now + self._interval


def _content_matches(content: str, word_lower: str) -> bool:
    return word_lower in content.lower()


def _can_manage(channel, me: disnake.Member) -> bool:
    perms = channel.permissions_for(me)
    return bool(perms.read_message_history and perms.manage_messages)


def _bulk_cutoff() -> datetime:
    """Messages created after this may go in a bulk delete right now."""
    return (
        disnake.utils.utcnow()
        - timedelta(days=BULK_DELETE_MAX_AGE_DAYS)
        + BULK_DELETE_SAFETY_MARGIN
    )


class _SearchUnavailable(Exception):
    """Discord's message search can't serve this purge; use the full scan."""


async def _search_page(guild: disnake.Guild, params: list[tuple[str, str]]):
    """GET /guilds/{id}/messages/search. disnake has no wrapper for it yet.

    Goes through disnake's HTTP client, so it gets disnake's rate-limit handling.
    A 202 ("not indexed yet") comes back as a normal dict carrying ``code``.
    """
    route = Route("GET", "/guilds/{guild_id}/messages/search", guild_id=guild.id)
    return await guild._state.http.request(route, params=params)


class _ChannelDeleter:
    """Deletes queued matches in one channel without blocking whoever feeds it.

    A bulk worker (under 14 days, up to 100 per call) and a single-delete
    worker (older) run alongside the reader. They hit different Discord
    routes, so they don't wait on each other or on history reads.
    """

    def __init__(self, run: _Purge, channel) -> None:
        self.run = run
        self.channel = channel
        self._recent: list = []
        self._recent_q: asyncio.Queue = asyncio.Queue()
        self._old_q: asyncio.Queue = asyncio.Queue(maxsize=OLD_DELETE_QUEUE_SIZE)
        self._archived = isinstance(channel, disnake.Thread) and bool(
            getattr(channel, "archived", False)
        )
        self._unarchived = False
        self._bulk_task = asyncio.create_task(run._bulk_worker(channel, self._recent_q, self._old_q))
        self._old_task = asyncio.create_task(run._old_worker(self._old_q))

    async def add(self, message, cutoff: datetime) -> None:
        if self._archived and not self._unarchived:
            # Discord refuses deletes in an archived thread.
            await self.channel.edit(archived=False)
            self._unarchived = True
        if message.created_at > cutoff:
            self._recent.append(message)
            if len(self._recent) >= BULK_DELETE_BATCH_SIZE:
                await self._recent_q.put(self._recent)
                self._recent = []
        else:
            await self._old_q.put(message)

    async def flush(self) -> None:
        """Send queued recent matches now, without waiting for a full batch of 100.

        The full scan doesn't need this (each channel closes when its read
        ends), but a member purge keeps every channel's deleter open for the
        whole search -- without flushing, nothing recent is deleted until the
        search finishes.
        """
        if self._recent:
            await self._recent_q.put(self._recent)
            self._recent = []

    async def close(self) -> None:
        """Flush what's queued, wait for every delete, re-archive if we unarchived."""
        if self._recent:
            await self._recent_q.put(self._recent)
            self._recent = []
        await self._recent_q.put(None)
        await self._bulk_task  # may hand failed batches to the old queue, so it finishes first
        await self._old_q.put(None)
        await self._old_task
        if self._unarchived:
            try:
                await self.channel.edit(archived=True)
            except disnake.HTTPException as exc:
                logger.warning("Couldn't re-archive %s: %s", self.channel, exc)


class _Purge:
    """State shared by every channel of one purge run."""

    def __init__(self, target_id: int | None, word_lower: str, job: PurgeJob) -> None:
        self.target_id = target_id
        self.word_lower = word_lower
        self.result = job.result
        self.cancel = job.cancel_event
        self.limiter = _RateLimiter(MAX_REQUESTS_PER_SECOND)

    def _matches(self, message: disnake.Message) -> bool:
        # No target means "anyone's message" -- the word alone decides.
        if self.target_id is not None and message.author.id != self.target_id:
            return False
        return _content_matches(message.content or "", self.word_lower)

    # -- deletion workers ----------------------------------------------------

    async def _bulk_worker(self, channel, recent_q: asyncio.Queue, old_q: asyncio.Queue) -> None:
        while (batch := await recent_q.get()) is not None:
            if self.cancel.is_set():
                continue
            # Re-check age now: the batch may have waited while the run went on.
            cutoff = _bulk_cutoff()
            fresh = [m for m in batch if m.created_at > cutoff]
            for message in batch:
                if message.created_at <= cutoff:
                    await old_q.put(message)
            if not fresh:
                continue
            await self.limiter.wait()
            try:
                if len(fresh) == 1:
                    await fresh[0].delete()
                else:
                    await channel.delete_messages(fresh)
                self.result.deleted += len(fresh)
            except disnake.HTTPException as exc:
                # One bad message fails the whole batch; retry them one by one
                # so a single problem doesn't cost up to 99 other deletions.
                logger.warning("Bulk delete failed in %s (%s); retrying singly", channel, exc)
                for message in fresh:
                    await old_q.put(message)

    async def _old_worker(self, old_q: asyncio.Queue) -> None:
        while (message := await old_q.get()) is not None:
            if self.cancel.is_set():
                continue
            await self.limiter.wait()
            try:
                await message.delete()
                self.result.deleted += 1
            except disnake.NotFound:
                pass  # already gone -- that's the goal
            except disnake.HTTPException as exc:
                self.result.failed += 1
                logger.warning("Delete failed for message %s: %s", message.id, exc)

    # -- one channel -----------------------------------------------------------

    async def scan_channel(self, channel) -> None:
        """Read one channel's whole history, queueing matches for deletion."""
        deleter = _ChannelDeleter(self, channel)
        before = None
        try:
            while not self.cancel.is_set():
                await self.limiter.wait()
                page = [m async for m in channel.history(limit=HISTORY_PAGE_SIZE, before=before)]
                self.result.messages_scanned += len(page)
                cutoff = _bulk_cutoff()
                for message in page:
                    if self._matches(message):
                        await deleter.add(message, cutoff)
                if len(page) < HISTORY_PAGE_SIZE:
                    break
                before = page[-1]  # history is newest-first; continue past the oldest
        finally:
            await deleter.close()

    # -- one author, via Discord's message search ---------------------------------

    async def _search(self, guild: disnake.Guild, params: list[tuple[str, str]]) -> dict:
        """One search page, waiting out "not indexed yet" (202) replies."""
        for _attempt in range(SEARCH_INDEX_RETRIES):
            await self.limiter.wait()
            try:
                data = await _search_page(guild, params)
            except (disnake.HTTPException, aiohttp.ClientError, ValueError) as exc:
                raise _SearchUnavailable(f"search request failed: {exc}") from exc
            if not isinstance(data, dict):
                raise _SearchUnavailable(f"unexpected search response: {data!r:.200}")
            if data.get("code") == SEARCH_NOT_INDEXED_CODE:
                await asyncio.sleep(data.get("retry_after") or SEARCH_INDEX_DEFAULT_WAIT_SECONDS)
                continue
            return data
        raise _SearchUnavailable("Discord hasn't indexed this server for search yet")

    async def _deleter_for(
        self,
        guild: disnake.Guild,
        me,
        channel_id: int,
        deleters: dict[int, _ChannelDeleter],
        unreachable: set[int],
    ) -> _ChannelDeleter | None:
        if channel_id in deleters:
            return deleters[channel_id]
        if channel_id in unreachable:
            return None
        channel = guild.get_channel_or_thread(channel_id)
        if channel is None:
            # Archived threads aren't cached.
            await self.limiter.wait()
            try:
                channel = await guild.fetch_channel(channel_id)
            except disnake.HTTPException:
                channel = None
        archived = isinstance(channel, disnake.Thread) and bool(getattr(channel, "archived", False))
        if (
            channel is None
            or not _can_manage(channel, me)
            or (archived and not channel.permissions_for(me).manage_threads)
        ):
            unreachable.add(channel_id)
            self.result.channels_skipped += 1
            return None
        deleters[channel_id] = _ChannelDeleter(self, channel)
        self.result.channels_found += 1
        return deleters[channel_id]

    async def search_author(self, guild: disnake.Guild, me, channel) -> None:
        """Delete one author's matching messages, fetching only *their* messages.

        Searches by author only, never by content: Discord's search matches
        whole words, while /purgeword matches substrings ("dog" in "dogs"), so
        a content search would silently miss messages. The word is checked
        here instead, the same way the full scan does it.

        Raises ``_SearchUnavailable`` if search can't be used; the caller then
        falls back to the full scan.
        """
        self.result.used_search = True
        deleters: dict[int, _ChannelDeleter] = {}
        unreachable: set[int] = set()
        # Both the search and the recent-history read can find the same message.
        queued: set[int] = set()
        max_id: int | None = None
        first_page = True
        # Search lags behind new messages, so read the last stretch directly.
        recent_task = asyncio.create_task(
            self._scan_recent(guild, me, channel, deleters, queued)
        )
        try:
            while not self.cancel.is_set():
                params = [
                    ("author_id", str(self.target_id)),
                    ("limit", str(SEARCH_PAGE_SIZE)),
                    ("sort_by", "timestamp"),
                    ("sort_order", "desc"),
                    ("include_nsfw", "true"),
                ]
                if channel is not None:
                    params.append(("channel_id", str(channel.id)))
                if max_id is not None:
                    # Page by id, not offset: offset stops at 9,975 and shifts as we delete.
                    params.append(("max_id", str(max_id)))
                data = await self._search(guild, params)
                if data.get("doing_deep_historical_index"):
                    self.result.search_incomplete = True
                # Each entry was once a list of the hit plus context; now it's just the hit.
                hits = [
                    msg
                    for entry in data.get("messages") or []
                    for msg in (entry if isinstance(entry, list) else [entry])
                ]
                if not hits:
                    break
                if (
                    first_page
                    and len(hits) >= SEARCH_BLANK_CONTENT_MIN_HITS
                    and not any(m.get("content") for m in hits)
                ):
                    # Text withheld from results: nothing could ever match, and
                    # the purge would "succeed" having deleted nothing.
                    raise _SearchUnavailable("search results came back without message text")
                page_min = min(int(m["id"]) for m in hits)
                if max_id is not None and page_min >= max_id:
                    # Paging didn't move past the last page: looping would
                    # re-read the same results forever.
                    raise _SearchUnavailable("search ignored max_id; paging made no progress")
                max_id = page_min
                self.result.messages_scanned += len(hits)
                cutoff = _bulk_cutoff()
                matched = 0
                for payload in hits:
                    message_id = int(payload["id"])
                    if message_id in queued:
                        continue
                    if int(payload["author"]["id"]) != self.target_id:
                        continue
                    if not _content_matches(payload.get("content") or "", self.word_lower):
                        continue
                    deleter = await self._deleter_for(
                        guild, me, int(payload["channel_id"]), deleters, unreachable
                    )
                    matched += 1
                    if deleter is not None:
                        queued.add(message_id)
                        await deleter.add(deleter.channel.get_partial_message(message_id), cutoff)
                if first_page:
                    logger.info(
                        "Purge search first page: hits=%d with_text=%d by_target=%d matched=%d "
                        "total_results=%s deep_indexing=%s",
                        len(hits),
                        sum(1 for m in hits if m.get("content")),
                        sum(1 for m in hits if int(m["author"]["id"]) == self.target_id),
                        matched,
                        data.get("total_results"),
                        data.get("doing_deep_historical_index"),
                    )
                    first_page = False
                for deleter in list(deleters.values()):
                    await deleter.flush()
        finally:
            try:
                await recent_task
            finally:
                await asyncio.gather(*(d.close() for d in deleters.values()))
                self.result.channels_scanned += len(deleters)

    async def _scan_recent(
        self,
        guild: disnake.Guild,
        me,
        channel,
        deleters: dict[int, _ChannelDeleter],
        queued: set[int],
    ) -> None:
        """Read the last ``SEARCH_RECENT_WINDOW`` of history in every channel.

        Discord's search index trails new messages, so a message posted just
        before the purge isn't searchable yet. Reading recent history directly
        covers that gap; for most channels it's one request.
        """
        if channel is not None:
            channels = [channel]
        else:
            channels = [
                c
                for c in (
                    *guild.text_channels,
                    *guild.voice_channels,
                    *guild.stage_channels,
                    *guild.threads,
                )
                if _can_manage(c, me)
            ]
        after = disnake.utils.utcnow() - SEARCH_RECENT_WINDOW
        semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

        async def scan(ch) -> None:
            async with semaphore:
                if self.cancel.is_set():
                    return
                await self.limiter.wait()
                try:
                    recent = [m async for m in ch.history(limit=None, after=after)]
                except disnake.HTTPException as exc:
                    logger.warning("Couldn't read recent history in %s: %s", ch, exc)
                    return
                cutoff = _bulk_cutoff()
                for message in recent:
                    if message.id in queued or not self._matches(message):
                        continue
                    deleter = deleters.get(ch.id)
                    if deleter is None:
                        deleter = deleters[ch.id] = _ChannelDeleter(self, ch)
                        self.result.channels_found += 1
                    queued.add(message.id)
                    await deleter.add(message, cutoff)
                if ch.id in deleters:
                    await deleters[ch.id].flush()

        await asyncio.gather(*(scan(c) for c in channels))

    async def scan_channel_safe(self, channel) -> None:
        if self.cancel.is_set():
            return
        try:
            await self.scan_channel(channel)
            self.result.channels_scanned += 1
        except disnake.Forbidden:
            self.result.channels_skipped += 1
            logger.warning("Lost access mid-scan in %s; skipping", channel)
        except disnake.HTTPException as exc:
            self.result.channels_skipped += 1
            logger.warning("HTTP error scanning %s: %s; skipping", channel, exc)

    # -- which channels --------------------------------------------------------

    async def produce_channels(self, guild: disnake.Guild, me, queue: asyncio.Queue) -> None:
        """Queue every channel/thread to scan, archived threads included."""
        seen: set[int] = set()

        async def add(channel) -> None:
            if channel.id in seen:
                return
            seen.add(channel.id)
            self.result.channels_found += 1
            await queue.put(channel)

        live = [
            *guild.text_channels,
            *guild.voice_channels,  # voice/stage channels have their own text chat
            *guild.stage_channels,
            *guild.threads,  # active threads only; archived ones are listed below
        ]
        for channel in live:
            if _can_manage(channel, me):
                await add(channel)
            else:
                self.result.channels_skipped += 1

        # Archived threads aren't in the cache and must be listed per parent.
        # Deleting in one means un-archiving it first, which needs Manage Threads.
        parents = [*guild.text_channels, *guild.forum_channels, *guild.media_channels]
        for parent in parents:
            if self.cancel.is_set():
                return
            perms = parent.permissions_for(me)
            if not (perms.read_message_history and perms.manage_messages):
                continue
            if not perms.manage_threads:
                self.result.archived_unreachable += 1
                continue
            # Private archived threads only exist under text channels.
            kinds = [False, True] if isinstance(parent, disnake.TextChannel) else [None]
            for private in kinds:
                kwargs = {"limit": None} if private is None else {"limit": None, "private": private}
                try:
                    async for thread in parent.archived_threads(**kwargs):
                        await add(thread)
                except disnake.HTTPException as exc:
                    self.result.archived_unreachable += 1
                    logger.warning("Couldn't list archived threads in %s: %s", parent, exc)


def _validate(guild: disnake.Guild, word: str, channel) -> tuple[str, disnake.Member]:
    word = word.strip()
    if not word:
        raise ModerationError("Provide a non-empty word to purge.")
    me = guild.me
    if me is None:
        raise ModerationError("I'm not in this server.")
    # An explicitly named channel we can't manage is the invoker's to fix,
    # not a silent "skipped 1" in the summary.
    if channel is not None and not _can_manage(channel, me):
        raise ModerationError(
            f"I need **Read Message History** and **Manage Messages** in {channel.mention}."
        )
    return word, me


async def _run(
    job: PurgeJob,
    guild: disnake.Guild,
    me,
    word: str,
    moderator,
    target_id: int | None,
    channel,
) -> PurgeResult:
    run = _Purge(target_id, word.lower(), job)
    result = job.result
    try:
        searched = False
        if target_id is not None:
            # Only this member's messages are fetched, not the whole server's.
            try:
                await run.search_author(guild, me, channel)
                searched = True
            except _SearchUnavailable as exc:
                logger.warning("Message search unavailable (%s); falling back to a full scan", exc)
                result.used_search = False
                result.search_incomplete = False
                result.channels_found = result.channels_scanned = result.channels_skipped = 0
        if searched:
            pass
        elif channel is not None:
            result.channels_found = 1
            await run.scan_channel_safe(channel)
        else:
            queue: asyncio.Queue = asyncio.Queue(maxsize=SCAN_CONCURRENCY * 2)

            async def worker() -> None:
                while (ch := await queue.get()) is not None:
                    await run.scan_channel_safe(ch)

            workers = [asyncio.create_task(worker()) for _ in range(SCAN_CONCURRENCY)]
            try:
                await run.produce_channels(guild, me, queue)
            finally:
                for _ in workers:
                    await queue.put(None)
                await asyncio.gather(*workers)
    finally:
        result.cancelled = job.cancel_event.is_set()
        result.finished = True
        result.finished_at = time.monotonic()
        elapsed = max(result.elapsed, 1e-6)
        logger.info(
            "Purge by %s: word=%r target=%s channel=%s deleted=%d failed=%d "
            "scanned=%d msgs (%.0f/s) channels=%d/%d skipped=%d archived_unreachable=%d "
            "search=%s search_incomplete=%s cancelled=%s elapsed=%.1fs",
            moderator,
            word,
            target_id if target_id is not None else "anyone",
            channel.id if channel is not None else "all",
            result.deleted,
            result.failed,
            result.messages_scanned,
            result.messages_scanned / elapsed,
            result.channels_scanned,
            result.channels_found,
            result.channels_skipped,
            result.archived_unreachable,
            result.used_search,
            result.search_incomplete,
            result.cancelled,
            result.elapsed,
        )
    return result


def start_purge(
    guild: disnake.Guild,
    word: str,
    moderator,
    *,
    target: disnake.Member | disnake.User | None = None,
    channel: disnake.TextChannel | disnake.Thread | None = None,
) -> PurgeJob:
    """Validate, then start a purge in the background and return its job.

    ``target`` limits it to one author; omitted, anyone's matching message is
    deleted. ``channel`` limits the scan to that one channel or thread (its
    threads are NOT included -- name a thread directly to purge it); omitted,
    every channel, voice/stage chat, active and archived thread the bot can
    read + manage is scanned. Raises ``ModerationError`` for invalid input or
    if this server already has a purge running.
    """
    word, me = _validate(guild, word, channel)
    if guild.id in _running:
        raise ModerationError(
            "A purge is already running in this server. Wait for it to finish or cancel it."
        )

    job = PurgeJob(guild_id=guild.id, result=PurgeResult(), cancel_event=asyncio.Event())
    target_id = target.id if target is not None else None
    job.task = asyncio.create_task(_run(job, guild, me, word, moderator, target_id, channel))
    _running[guild.id] = job

    def _done(_task: asyncio.Task) -> None:
        if _running.get(guild.id) is job:
            del _running[guild.id]

    job.task.add_done_callback(_done)
    return job


async def purge_messages(
    guild: disnake.Guild,
    word: str,
    moderator,
    *,
    target: disnake.Member | disnake.User | None = None,
    channel: disnake.TextChannel | disnake.Thread | None = None,
) -> PurgeResult:
    """Run a purge to completion and return its result (see ``start_purge``)."""
    job = start_purge(guild, word, moderator, target=target, channel=channel)
    return await job.task


# -- reply text (shared by the progress message and the final summary) --------


def _duration(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _failed_part(result: PurgeResult) -> str:
    return f" · **{result.failed:,}** failed" if result.failed else ""


def format_progress(result: PurgeResult, word: str) -> str:
    if result.used_search:
        return (
            f"🔎 Purging messages containing `{word}`…\n"
            f"Checked **{result.messages_scanned:,}** of their message(s) · "
            f"deleted **{result.deleted:,}**{_failed_part(result)} · "
            f"{_duration(result.elapsed)} so far"
        )
    return (
        f"🧹 Purging messages containing `{word}`…\n"
        f"Read **{result.messages_scanned:,}** message(s) · "
        f"finished {result.channels_scanned:,}/{result.channels_found:,} channel(s) · "
        f"deleted **{result.deleted:,}**{_failed_part(result)} · {_duration(result.elapsed)} so far"
    )


def format_summary(
    result: PurgeResult,
    word: str,
    *,
    member_label: str | None = None,
    channel_mention: str | None = None,
) -> str:
    author_part = f" from **{member_label}**" if member_label else ""
    if channel_mention:
        scope_part = f"in {channel_mention}"
    elif result.used_search:
        scope_part = f"in {result.channels_scanned:,} channel(s)"
    else:
        scope_part = f"across {result.channels_scanned:,} channel(s)"
    head = "🛑 Purge cancelled." if result.cancelled else "✅ Purge finished."
    summary = (
        f"{head} Deleted **{result.deleted:,}** message(s){author_part} containing "
        f"`{word}` {scope_part} (read {result.messages_scanned:,} in "
        f"{_duration(result.elapsed)})."
    )
    if result.channels_skipped:
        summary += f"\nSkipped {result.channels_skipped:,} channel(s) I can't manage."
    if result.archived_unreachable:
        summary += (
            f"\nCouldn't check archived threads in {result.archived_unreachable:,} "
            "channel(s) — I need **Manage Threads** there."
        )
    if result.failed:
        summary += f"\n{result.failed:,} deletion(s) failed."
    if result.search_incomplete:
        summary += (
            "\n⚠️ Discord is still indexing this server's history, so some older "
            "messages may not have been found. Run it again later to catch them."
        )
    return summary
