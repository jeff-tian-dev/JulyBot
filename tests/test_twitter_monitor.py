"""Unit tests for modules.twitter_monitor."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from modules.twitter_monitor import client, embeds, poller, storage


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


def test_normalize_username_strips_at_and_lowercases() -> None:
    assert storage.normalize_username("@ElonMusk") == "elonmusk"


def test_normalize_username_rejects_invalid() -> None:
    with pytest.raises(ValueError):
        storage.normalize_username("bad handle!")


def test_build_tweet_embed() -> None:
    tweet = SimpleNamespace(
        text="Hello world",
        url="https://x.com/testuser/status/123",
        created_on=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media=[],
        author=SimpleNamespace(
            name="Test User",
            username="testuser",
            profile_image_url_https="https://pbs.twimg.com/profile.jpg",
        ),
    )
    embed = embeds.build_tweet_embed(tweet)
    components = embeds.build_tweet_components(tweet)
    assert embed.description == "Hello world"
    assert "View on X" not in (embed.description or "")
    assert embed.author.name == "Test User (@testuser)"
    assert embed.author.url == "https://x.com/testuser"
    assert embed.author.icon_url == "https://pbs.twimg.com/profile.jpg"
    assert embed.footer.text == "@testuser"
    button = components[0].children[0]
    assert button.label == "View on X"
    assert button.url == "https://x.com/testuser/status/123"


def test_build_tweet_embed_strips_media_links_from_text() -> None:
    tweet = SimpleNamespace(
        text="https://t.co/vpb2il3IIM",
        url="https://x.com/cynicynic/status/123",
        created_on=None,
        media=[
            SimpleNamespace(
                type="photo",
                media_url_https="https://pbs.twimg.com/media/abc.jpg",
                direct_url="https://pbs.twimg.com/media/abc.jpg",
                url="https://t.co/vpb2il3IIM",
                display_url="pic.x.com/vpb2il3IIM",
                expanded_url="https://x.com/cynicynic/status/123/photo/1",
            ),
        ],
        author=SimpleNamespace(name="cyn", username="cynicynic", profile_image_url_https=None),
    )
    embed = embeds.build_tweet_embed(tweet)
    components = embeds.build_tweet_components(tweet)
    assert embed.description in (None, "")
    assert embed.footer.text == "@cynicynic"
    assert components[0].children[0].url == "https://x.com/cynicynic/status/123"
    assert embed.image.url == "https://pbs.twimg.com/media/abc.jpg"


def test_build_tweet_embed_keeps_real_text_strips_tco() -> None:
    tweet = SimpleNamespace(
        text="Hello world https://t.co/abc123",
        url="https://x.com/testuser/status/456",
        created_on=None,
        media=[
            SimpleNamespace(
                type="photo",
                media_url_https="https://pbs.twimg.com/media/a.jpg",
                direct_url=None,
                url="https://t.co/abc123",
                display_url="pic.x.com/abc123",
                expanded_url=None,
            ),
        ],
        author=SimpleNamespace(name="Test", username="testuser", profile_image_url_https=None),
    )
    embed = embeds.build_tweet_embed(tweet)
    assert "Hello world" in (embed.description or "")
    assert "t.co" not in (embed.description or "")
    assert "View on X" not in (embed.description or "")


def test_build_tweet_embeds_with_media() -> None:
    tweet = SimpleNamespace(
        text="Photo tweet",
        url="https://x.com/testuser/status/456",
        created_on=None,
        media=[
            SimpleNamespace(type="photo", media_url_https="https://pbs.twimg.com/media/a.jpg", direct_url=None),
            SimpleNamespace(type="photo", media_url_https="https://pbs.twimg.com/media/b.jpg", direct_url=None),
        ],
        author=SimpleNamespace(name="Test User", username="testuser", profile_image_url_https=None),
    )
    result = embeds.build_tweet_embeds(tweet)
    assert len(result) == 2
    assert result[0].image.url == "https://pbs.twimg.com/media/a.jpg"
    assert result[1].image.url == "https://pbs.twimg.com/media/b.jpg"
    assert result[0].author.icon_url == "https://unavatar.io/x/testuser"


def test_build_tweet_embed_profile_fallback() -> None:
    tweet = SimpleNamespace(
        text="",
        url="https://x.com/cynicynic/status/999",
        created_on=None,
        media=[],
        author=SimpleNamespace(name="cyn", username="cynicynic", profile_image_url_https=None),
    )
    embed = embeds.build_tweet_embed(tweet)
    components = embeds.build_tweet_components(tweet)
    assert embed.author.icon_url == "https://unavatar.io/x/cynicynic"
    assert embed.description in (None, "")
    assert components[0].children[0].label == "View on X"


@pytest.mark.asyncio
async def test_mark_tweets_seen_returns_only_new_ids() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {"tweet_id": 100},
            None,
            {"tweet_id": 102},
        ]
    )
    pool = _fake_pool(conn)

    result = await storage.mark_tweets_seen(pool, guild_id=1, username="testuser", tweet_ids=[100, 101, 102])
    assert result == [100, 102]
    assert conn.fetchrow.await_count == 3


@pytest.mark.asyncio
async def test_add_watched_account_invalid_username() -> None:
    pool = _fake_pool(MagicMock())
    with pytest.raises(ValueError):
        await storage.add_watched_account(pool, guild_id=1, username="!!!")


@pytest.mark.asyncio
async def test_poll_twitter_accounts_skips_when_not_configured() -> None:
    bot = MagicMock(spec=disnake.Client)
    pool = _fake_pool(MagicMock())

    with patch.object(client, "is_configured", return_value=False):
        summary = await poller.poll_twitter_accounts(pool, bot)

    assert summary == {"accounts_polled": 0, "tweets_posted": 0, "errors": 0}


def test_content_dedup_id_uses_retweeted_tweet() -> None:
    retweeted = SimpleNamespace(id=50)
    tweet = SimpleNamespace(id=101, is_retweet=True, retweeted_tweet=retweeted)
    assert poller._content_dedup_id(tweet) == 50


def test_content_dedup_id_falls_back_to_timeline_id() -> None:
    tweet = SimpleNamespace(id=101, is_retweet=True, retweeted_tweet=None)
    assert poller._content_dedup_id(tweet) == 101


def test_build_tweet_embed_repost() -> None:
    tweet = SimpleNamespace(
        id=200,
        text="Original content",
        url="https://x.com/original/status/50",
        is_retweet=True,
        is_quoted=False,
        created_on=None,
        media=[],
        author=SimpleNamespace(name="Watcher", username="watcher", profile_image_url_https=None),
        retweeted_tweet=SimpleNamespace(
            id=50,
            text="Original content",
            url="https://x.com/original/status/50",
            media=[],
            author=SimpleNamespace(name="Original", username="original", profile_image_url_https=None),
        ),
    )
    embed = embeds.build_tweet_embed(tweet)
    components = embeds.build_tweet_components(tweet)
    assert "reposted Original" in embed.author.name
    assert embed.description == "Original content"
    assert components[0].children[0].url == "https://x.com/original/status/50"


def test_build_tweet_embed_quote() -> None:
    tweet = SimpleNamespace(
        id=300,
        text="My take on this",
        url="https://x.com/watcher/status/300",
        is_retweet=False,
        is_quoted=True,
        created_on=None,
        media=[],
        author=SimpleNamespace(name="Watcher", username="watcher", profile_image_url_https=None),
        quoted_tweet=SimpleNamespace(
            id=50,
            text="Quoted text",
            media=[],
            author=SimpleNamespace(name="Original", username="original", profile_image_url_https=None),
        ),
    )
    result = embeds.build_tweet_embeds(tweet)
    assert len(result) == 2
    assert result[0].description == "My take on this"
    assert "Quoted:" in result[1].author.name
    assert result[1].description == "Quoted text"


def test_role_ping_content() -> None:
    assert poller._role_ping_content(1515021940090474557) == "<@&1515021940090474557>"
    assert poller._role_ping_content(0) is None


@pytest.mark.asyncio
async def test_poll_twitter_accounts_posts_new_tweets() -> None:
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel

    tweet_old = SimpleNamespace(
        id=50,
        text="old",
        url="https://x.com/u/status/50",
        is_retweet=False,
        author=SimpleNamespace(name="U", username="u", profile_image_url_https=None),
        created_on=None,
    )
    tweet_new = SimpleNamespace(
        id=100,
        text="new tweet",
        url="https://x.com/u/status/100",
        is_retweet=False,
        author=SimpleNamespace(name="U", username="u", profile_image_url_https=None),
        created_on=None,
    )
    tweet_rt = SimpleNamespace(
        id=101,
        text="retweet",
        url="https://x.com/u/status/101",
        is_retweet=True,
        retweeted_tweet=SimpleNamespace(
            id=60,
            text="retweet",
            url="https://x.com/other/status/60",
            media=[],
            author=SimpleNamespace(name="Other", username="other", profile_image_url_https=None),
        ),
        author=SimpleNamespace(name="U", username="u", profile_image_url_https=None),
        created_on=None,
        media=[],
    )

    tweets_page = [tweet_new, tweet_rt, tweet_old]

    mock_app = MagicMock()
    mock_app.get_tweets = AsyncMock(return_value=tweets_page)

    conn = MagicMock()
    pool = _fake_pool(conn)

    accounts = [
        {
            "guild_id": 1,
            "username": "testuser",
            "last_seen_tweet_id": 50,
            "twitter_channel_id": 999,
            "twitter_enabled": True,
        }
    ]

    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "get_client", AsyncMock(return_value=mock_app)),
        patch.object(storage, "get_all_watched_accounts", AsyncMock(return_value=accounts)),
        patch.object(storage, "mark_tweets_seen", AsyncMock(return_value=[100, 60])),
        patch.object(storage, "update_last_seen", AsyncMock()) as mock_update,
        patch.object(poller, "_role_ping_content", return_value="<@&1515021940090474557>"),
        patch.object(poller, "_role_ping_allowed_mentions", return_value=MagicMock()),
        patch("modules.twitter_monitor.poller.asyncio.sleep", AsyncMock()),
    ):
        summary = await poller.poll_twitter_accounts(pool, bot)

    assert summary["accounts_polled"] == 1
    assert summary["tweets_posted"] == 2
    assert channel.send.await_count == 2
    send_kwargs = channel.send.await_args.kwargs
    assert send_kwargs["content"] == "<@&1515021940090474557>"
    mock_update.assert_awaited_once_with(pool, 1, "testuser", 101)


@pytest.mark.asyncio
async def test_poll_skips_repost_when_content_already_seen() -> None:
    bot = MagicMock(spec=disnake.Client)
    bot.is_ready.return_value = True

    channel = MagicMock()
    channel.send = AsyncMock()
    bot.get_channel.return_value = channel

    tweet_rt = SimpleNamespace(
        id=101,
        text="already seen content",
        url="https://x.com/u/status/101",
        is_retweet=True,
        retweeted_tweet=SimpleNamespace(
            id=60,
            text="already seen content",
            url="https://x.com/other/status/60",
            media=[],
            author=SimpleNamespace(name="Other", username="other", profile_image_url_https=None),
        ),
        author=SimpleNamespace(name="U", username="u", profile_image_url_https=None),
        created_on=None,
        media=[],
    )

    mock_app = MagicMock()
    mock_app.get_tweets = AsyncMock(return_value=[tweet_rt])

    pool = _fake_pool(MagicMock())
    accounts = [
        {
            "guild_id": 1,
            "username": "testuser",
            "last_seen_tweet_id": 50,
            "twitter_channel_id": 999,
            "twitter_enabled": True,
        }
    ]

    with (
        patch.object(client, "is_configured", return_value=True),
        patch.object(client, "get_client", AsyncMock(return_value=mock_app)),
        patch.object(storage, "get_all_watched_accounts", AsyncMock(return_value=accounts)),
        patch.object(storage, "mark_tweets_seen", AsyncMock(return_value=[])),
        patch.object(storage, "update_last_seen", AsyncMock()) as mock_update,
        patch("modules.twitter_monitor.poller.asyncio.sleep", AsyncMock()),
    ):
        summary = await poller.poll_twitter_accounts(pool, bot)

    assert summary["tweets_posted"] == 0
    channel.send.assert_not_awaited()
    mock_update.assert_awaited_once_with(pool, 1, "testuser", 101)
