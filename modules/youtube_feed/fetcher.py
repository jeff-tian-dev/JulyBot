"""Fetch latest videos from YouTube RSS feeds via feedparser."""
from __future__ import annotations

import asyncio
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

logger = logging.getLogger(__name__)

YOUTUBE_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
VIDEO_ID_PREFIX = "yt:video:"
FEED_USER_AGENT = "Mozilla/5.0 (compatible; JulyBot/1.0; +https://www.youtube.com/feeds/videos.xml)"
FEED_TIMEOUT_SECONDS = 15


def _fetch_feed_document(channel_id: str):
    """Fetch and parse a YouTube RSS feed, using an explicit User-Agent."""
    url = YOUTUBE_FEED_URL.format(channel_id=channel_id)
    request = urllib.request.Request(url, headers={"User-Agent": FEED_USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FEED_TIMEOUT_SECONDS) as response:
            return feedparser.parse(response.read())
    except urllib.error.URLError as exc:
        logger.warning("YouTube feed request failed for channel_id=%s: %s", channel_id, exc)
        return feedparser.parse(b"")


@dataclass(frozen=True)
class VideoEntry:
    video_id: str
    title: str
    url: str
    published: datetime | None
    channel_title: str


def _parse_published(entry) -> datetime | None:
    published = getattr(entry, "published", None)
    if published:
        try:
            dt = parsedate_to_datetime(published)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt
        except (TypeError, ValueError):
            pass

    published_parsed = getattr(entry, "published_parsed", None)
    if published_parsed:
        try:
            return datetime(*published_parsed[:6], tzinfo=timezone.utc)
        except (TypeError, ValueError):
            pass
    return None


def _extract_video_id(entry) -> str | None:
    yt_id = getattr(entry, "yt_videoid", None)
    if yt_id:
        return str(yt_id)

    entry_id = getattr(entry, "id", None)
    if entry_id and str(entry_id).startswith(VIDEO_ID_PREFIX):
        return str(entry_id)[len(VIDEO_ID_PREFIX) :]
    return None


def _parse_feed(feed) -> VideoEntry | None:
    if not feed.entries:
        return None

    return _parse_feed_entry(feed.entries[0], feed)


def _parse_feed_entry(entry, feed) -> VideoEntry | None:
    video_id = _extract_video_id(entry)
    if not video_id:
        return None

    title = getattr(entry, "title", None) or "New YouTube video"
    url = getattr(entry, "link", None) or f"https://www.youtube.com/watch?v={video_id}"
    channel_title = getattr(feed.feed, "title", None) or "YouTube"

    return VideoEntry(
        video_id=video_id,
        title=title,
        url=url,
        published=_parse_published(entry),
        channel_title=channel_title,
    )


def find_video_published_sync(channel_id: str, video_id: str) -> datetime | None:
    """Return the publish time for a video ID if it appears in the channel RSS feed."""
    feed = _fetch_feed_document(channel_id)
    for entry in feed.entries:
        if _extract_video_id(entry) == video_id:
            return _parse_published(entry)
    return None


async def find_video_published(channel_id: str, video_id: str) -> datetime | None:
    """Async wrapper to look up a video publish time from the RSS feed."""
    return await asyncio.to_thread(find_video_published_sync, channel_id, video_id)


def fetch_latest_video_sync(channel_id: str) -> VideoEntry | None:
    """Parse the YouTube RSS feed and return the latest video entry."""
    feed = _fetch_feed_document(channel_id)
    if getattr(feed, "bozo", False) and not feed.entries:
        logger.warning("YouTube feed parse issue for channel_id=%s: %s", channel_id, feed.bozo_exception)
        return None
    return _parse_feed_entry(feed.entries[0], feed)


async def fetch_latest_video(channel_id: str) -> VideoEntry | None:
    """Async wrapper around feedparser (sync library)."""
    return await asyncio.to_thread(fetch_latest_video_sync, channel_id)


def fetch_channel_title_sync(channel_id: str) -> str | None:
    """Return the channel title from the YouTube RSS feed metadata."""
    feed = _fetch_feed_document(channel_id)
    title = getattr(feed.feed, "title", None)
    if not title:
        return None
    cleaned = str(title).strip()
    if cleaned.endswith(" - YouTube"):
        cleaned = cleaned[: -len(" - YouTube")].strip()
    return cleaned or None


async def fetch_channel_title(channel_id: str) -> str | None:
    """Async wrapper to fetch a YouTube channel title."""
    return await asyncio.to_thread(fetch_channel_title_sync, channel_id)
