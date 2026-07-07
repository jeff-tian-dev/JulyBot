"""Unit tests for modules.youtube_feed."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch, ANY

import disnake
import pytest

from modules.youtube_feed import embeds, fetcher, poller, storage


class _FakePoolAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolAcquireCtx(conn))
    return pool


def test_normalize_channel_id_accepts_valid_id() -> None:
    channel_id = "UC" + "a" * 22
    assert storage.normalize_channel_id(channel_id) == channel_id


def test_normalize_channel_id_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        storage.normalize_channel_id("not-a-channel-id")


def test_build_video_embed() -> None:
    entry = fetcher.VideoEntry(
        video_id="dQw4w9WgXcQ",
        title="Test Video",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        published=datetime(2026, 1, 1, tzinfo=timezone.utc),
        channel_title="Test Channel",
    )
    embed = embeds.build_video_embed(entry)
    assert embed.title == "Test Video"
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert embed.author.name == "Test Channel"
    assert embed.image.url == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert embed.timestamp == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_extract_video_id_from_yt_videoid() -> None:
    entry = SimpleNamespace(yt_videoid="abc123XYZ01", id="yt:video:other")
    assert fetcher._extract_video_id(entry) == "abc123XYZ01"


def test_extract_video_id_from_entry_id() -> None:
    entry = SimpleNamespace(id="yt:video:abc123XYZ01")
    assert fetcher._extract_video_id(entry) == "abc123XYZ01"


def test_parse_feed_returns_latest_entry() -> None:
    feed = SimpleNamespace(
        feed=SimpleNamespace(title="My Channel"),
        entries=[
            SimpleNamespace(
                yt_videoid="latestVid01",
                title="Latest Upload",
                link="https://www.youtube.com/watch?v=latestVid01",
                published="Mon, 01 Jan 2026 12:00:00 GMT",
            )
        ],
    )
    entry = fetcher._parse_feed(feed)
    assert entry is not None
    assert entry.video_id == "latestVid01"
    assert entry.title == "Latest Upload"
    assert entry.channel_title == "My Channel"


@pytest.mark.asyncio
async def test_add_watched_channel_primes_last_seen() -> None:
    channel_id = "UC" + "b" * 22
    latest = fetcher.VideoEntry(
        video_id="primedVid01",
        title="Primed",
        url="https://www.youtube.com/watch?v=primedVid01",
        published=None,
        channel_title="Channel",
    )
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        return_value={
            "id": 1,
            "guild_id": 1,
            "channel_id": channel_id,
            "channel_name": "Channel",
            "last_seen_video_id": "primedVid01",
            "added_at": None,
        }
    )
    pool = _fake_pool(conn)

    with patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=latest)):
        row = await storage.add_watched_channel(pool, guild_id=1, channel_id=channel_id)

    assert row["last_seen_video_id"] == "primedVid01"
    assert row["channel_name"] == "Channel"


def test_channel_label_prefers_name() -> None:
    channel_id = "UC" + "a" * 22
    assert storage.channel_label(channel_id, "My Channel") == "My Channel"


def test_channel_label_falls_back_to_id() -> None:
    channel_id = "UC" + "a" * 22
    assert storage.channel_label(channel_id, None) == channel_id


def test_format_channel_reference_includes_name_and_id() -> None:
    channel_id = "UC" + "a" * 22
    assert storage.format_channel_reference(channel_id, "TCD") == f"**TCD** (`{channel_id}`)"


def test_format_channel_reference_id_only_when_name_missing() -> None:
    channel_id = "UC" + "a" * 22
    assert storage.format_channel_reference(channel_id, None) == f"`{channel_id}`"


def test_role_ping_content() -> None:
    assert poller._role_ping_content(1508359179440750602) == "<@&1508359179440750602>"
    assert poller._role_ping_content(0) is None


@pytest.mark.asyncio
async def test_seed_unseeded_channels_sets_id_without_posting() -> None:
    channel_id = "UC" + "c" * 22
    latest = fetcher.VideoEntry(
        video_id="seedVid0001",
        title="Seed",
        url="https://www.youtube.com/watch?v=seedVid0001",
        published=None,
        channel_title="Channel",
    )

    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"guild_id": 1, "channel_id": channel_id}])
    pool = _fake_pool(conn)

    with (
        patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=latest)),
        patch.object(storage, "update_last_seen", AsyncMock()) as mock_update,
    ):
        seeded = await storage.seed_unseeded_channels(pool)

    assert seeded == 1
    mock_update.assert_awaited_once_with(pool, 1, channel_id, "seedVid0001")


@pytest.mark.asyncio
async def test_poll_posts_when_video_id_changes() -> None:
    channel_id = "UC" + "d" * 22
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    discord_channel = MagicMock()
    discord_channel.send = AsyncMock()
    bot.get_channel.return_value = discord_channel

    latest = fetcher.VideoEntry(
        video_id="newVideo001",
        title="New Video",
        url="https://www.youtube.com/watch?v=newVideo001",
        published=None,
        channel_title="Channel",
    )

    pool = _fake_pool(MagicMock())
    watched = [
        {
            "guild_id": 1,
            "channel_id": channel_id,
            "last_seen_video_id": "oldVideo001",
            "youtube_channel_id": 999,
            "youtube_enabled": True,
        }
    ]

    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=latest)),
        patch.object(storage, "update_last_seen", AsyncMock()) as mock_update,
        patch.object(storage, "claim_ping_slot", AsyncMock(return_value=True)),
        patch.object(poller, "_role_ping_content", return_value="<@&1508359179440750602>"),
        patch.object(poller, "_role_ping_allowed_mentions", return_value=MagicMock()),
        patch("modules.youtube_feed.poller.asyncio.sleep", AsyncMock()),
    ):
        summary = await poller.poll_youtube_channels(pool, bot)

    assert summary["channels_polled"] == 1
    assert summary["videos_posted"] == 1
    discord_channel.send.assert_awaited_once_with(
        content="<@&1508359179440750602>",
        embed=ANY,
        allowed_mentions=ANY,
    )
    mock_update.assert_awaited_once_with(pool, 1, channel_id, "newVideo001")


@pytest.mark.asyncio
async def test_poll_mutes_ping_within_cooldown() -> None:
    """Two channels with new videos in one run: the first pings, the second is silent."""
    from config.settings import settings

    ch_a = "UC" + "g" * 22
    ch_b = "UC" + "h" * 22
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    discord_channel = MagicMock()
    discord_channel.send = AsyncMock()
    bot.get_channel.return_value = discord_channel

    def _vid(vid):
        return fetcher.VideoEntry(
            video_id=vid, title=vid, url=f"https://www.youtube.com/watch?v={vid}",
            published=None, channel_title="Channel",
        )

    pool = _fake_pool(MagicMock())
    watched = [
        {"guild_id": 1, "channel_id": ch_a, "last_seen_video_id": "old_a00001", "youtube_channel_id": 999, "youtube_enabled": True},
        {"guild_id": 1, "channel_id": ch_b, "last_seen_video_id": "old_b00001", "youtube_channel_id": 999, "youtube_enabled": True},
    ]

    claim = AsyncMock(side_effect=[True, False])
    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", AsyncMock(side_effect=[_vid("new_a00001"), _vid("new_b00001")])),
        patch.object(storage, "update_last_seen", AsyncMock()),
        patch.object(storage, "claim_ping_slot", claim),
        patch.object(poller, "_role_ping_content", return_value="<@&555>"),
        patch.object(poller, "_role_ping_allowed_mentions", return_value=MagicMock()),
        patch("modules.youtube_feed.poller.asyncio.sleep", AsyncMock()),
    ):
        summary = await poller.poll_youtube_channels(pool, bot)

    assert summary["videos_posted"] == 2
    assert discord_channel.send.await_count == 2
    assert discord_channel.send.await_args_list[0].kwargs["content"] == "<@&555>"
    assert discord_channel.send.await_args_list[1].kwargs["content"] is None
    claim.assert_awaited_with(pool, 1, settings.YOUTUBE_PING_COOLDOWN_HOURS)


@pytest.mark.asyncio
async def test_poll_no_role_skips_claim() -> None:
    """With no ping role configured, the cooldown slot is never claimed."""
    channel_id = "UC" + "i" * 22
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    discord_channel = MagicMock()
    discord_channel.send = AsyncMock()
    bot.get_channel.return_value = discord_channel

    latest = fetcher.VideoEntry(
        video_id="newVideo002", title="V", url="https://www.youtube.com/watch?v=newVideo002",
        published=None, channel_title="Channel",
    )

    pool = _fake_pool(MagicMock())
    watched = [
        {"guild_id": 1, "channel_id": channel_id, "last_seen_video_id": "oldVideo001", "youtube_channel_id": 999, "youtube_enabled": True}
    ]

    claim = AsyncMock(return_value=True)
    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=latest)),
        patch.object(storage, "update_last_seen", AsyncMock()),
        patch.object(storage, "claim_ping_slot", claim),
        patch.object(poller, "_role_ping_content", return_value=None),
        patch("modules.youtube_feed.poller.asyncio.sleep", AsyncMock()),
    ):
        summary = await poller.poll_youtube_channels(pool, bot)

    assert summary["videos_posted"] == 1
    claim.assert_not_awaited()
    assert discord_channel.send.await_args.kwargs["content"] is None


@pytest.mark.asyncio
async def test_poll_skips_when_video_id_unchanged() -> None:
    channel_id = "UC" + "e" * 22
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    discord_channel = MagicMock()
    discord_channel.send = AsyncMock()
    bot.get_channel.return_value = discord_channel

    latest = fetcher.VideoEntry(
        video_id="sameVideo01",
        title="Same Video",
        url="https://www.youtube.com/watch?v=sameVideo01",
        published=None,
        channel_title="Channel",
    )

    pool = _fake_pool(MagicMock())
    watched = [
        {
            "guild_id": 1,
            "channel_id": channel_id,
            "last_seen_video_id": "sameVideo01",
            "youtube_channel_id": 999,
            "youtube_enabled": True,
        }
    ]

    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=latest)),
        patch.object(storage, "update_last_seen", AsyncMock()) as mock_update,
    ):
        summary = await poller.poll_youtube_channels(pool, bot)

    assert summary["videos_posted"] == 0
    discord_channel.send.assert_not_awaited()
    mock_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_seeds_when_last_seen_is_null() -> None:
    channel_id = "UC" + "f" * 22
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    discord_channel = MagicMock()
    discord_channel.send = AsyncMock()
    bot.get_channel.return_value = discord_channel

    latest = fetcher.VideoEntry(
        video_id="seedOnPoll1",
        title="Seed On Poll",
        url="https://www.youtube.com/watch?v=seedOnPoll1",
        published=None,
        channel_title="Channel",
    )

    pool = _fake_pool(MagicMock())
    watched = [
        {
            "guild_id": 1,
            "channel_id": channel_id,
            "last_seen_video_id": None,
            "youtube_channel_id": 999,
            "youtube_enabled": True,
        }
    ]

    with (
        patch.object(storage, "get_all_watched_channels", AsyncMock(return_value=watched)),
        patch.object(fetcher, "fetch_latest_video", AsyncMock(return_value=latest)),
        patch.object(storage, "update_last_seen", AsyncMock()) as mock_update,
    ):
        summary = await poller.poll_youtube_channels(pool, bot)

    assert summary["videos_posted"] == 0
    discord_channel.send.assert_not_awaited()
    mock_update.assert_awaited_once_with(pool, 1, channel_id, "seedOnPoll1")
