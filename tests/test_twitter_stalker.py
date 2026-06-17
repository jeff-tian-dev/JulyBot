"""Unit tests for modules.twitter_stalker."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.twitter_stalker import accounts, webhook
from modules.twitter_stalker.filter_query import build_filter_value, validate_filter_capacity
from modules.twitter_stalker import rules


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


def test_normalize_username_strips_at() -> None:
    assert accounts.normalize_username("@ElonMusk") == "elonmusk"


def test_normalize_username_invalid() -> None:
    with pytest.raises(ValueError):
        accounts.normalize_username("bad handle!")


def test_build_filter_value() -> None:
    assert build_filter_value(["bob", "alice"]) == "from:alice OR from:bob"


def test_validate_filter_capacity_too_long() -> None:
    long_names = [f"user{i}" for i in range(30)]
    with pytest.raises(ValueError, match="too long"):
        validate_filter_capacity(long_names)


def test_should_notify_skips_retweet() -> None:
    assert webhook.should_notify({"retweeted_status": {"id": "1"}, "text": "x"}) is False


def test_should_notify_skips_reply() -> None:
    assert webhook.should_notify({"in_reply_to_status_id": "99", "text": "x"}) is False


def test_should_notify_accepts_original() -> None:
    assert webhook.should_notify({"text": "hello world", "id": "123"}) is True


def test_extract_tweets_from_list() -> None:
    payload = [{"id": "1", "text": "a"}, {"id": "2", "text": "b"}]
    assert len(webhook._extract_tweets(payload)) == 2


def test_extract_tweets_from_envelope() -> None:
    payload = {"tweets": [{"id": "1", "text": "a"}]}
    assert len(webhook._extract_tweets(payload)) == 1


def test_parse_tweet() -> None:
    raw = {
        "id": "12345",
        "text": "hello",
        "user": {"userName": "alice", "name": "Alice"},
    }
    tweet = webhook._parse_tweet(raw)
    assert tweet is not None
    assert tweet["tweet_id"] == "12345"
    assert tweet["username"] == "alice"
    assert tweet["display_name"] == "Alice"


@pytest.mark.asyncio
async def test_remove_account_not_found() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 0")
    pool = _fake_pool(conn)

    result = await accounts.remove_account(pool, "nobody")
    assert result is False


@pytest.mark.asyncio
async def test_record_seen_tweet_dedup() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=["INSERT 0 1", "INSERT 0 0"])
    pool = _fake_pool(conn)

    assert await accounts.record_seen_tweet(pool, "111", "alice") is True
    assert await accounts.record_seen_tweet(pool, "111", "alice") is False


@pytest.mark.asyncio
@patch("modules.twitter_stalker.rules.api.add_filter_rule", new_callable=AsyncMock)
@patch("modules.twitter_stalker.rules.api.api_key_configured", return_value=True)
@patch("modules.twitter_stalker.rules.list_usernames", new_callable=AsyncMock)
@patch("modules.twitter_stalker.rules.get_filter_rule_id", new_callable=AsyncMock)
@patch("modules.twitter_stalker.rules.save_filter_rule_id", new_callable=AsyncMock)
async def test_sync_filter_rule_creates_rule(
    mock_save: AsyncMock,
    mock_get_rule: AsyncMock,
    mock_list: AsyncMock,
    _mock_key: MagicMock,
    mock_add: AsyncMock,
) -> None:
    mock_list.return_value = ["alice", "bob"]
    mock_get_rule.return_value = None
    mock_add.return_value = "rule-abc"

    pool = MagicMock()
    await rules.sync_filter_rule(pool)

    mock_add.assert_awaited_once()
    call_kwargs = mock_add.await_args
    assert call_kwargs[0][1] == "from:alice OR from:bob"
    mock_save.assert_awaited_once_with(pool, "rule-abc")


@pytest.mark.asyncio
@patch("modules.twitter_stalker.rules.api.update_filter_rule", new_callable=AsyncMock)
@patch("modules.twitter_stalker.rules.api.api_key_configured", return_value=True)
@patch("modules.twitter_stalker.rules.list_usernames", new_callable=AsyncMock)
@patch("modules.twitter_stalker.rules.get_filter_rule_id", new_callable=AsyncMock)
async def test_sync_filter_rule_updates_existing(
    mock_get_rule: AsyncMock,
    mock_list: AsyncMock,
    _mock_key: MagicMock,
    mock_update: AsyncMock,
) -> None:
    mock_list.return_value = ["alice"]
    mock_get_rule.return_value = "existing-rule"

    pool = MagicMock()
    await rules.sync_filter_rule(pool)

    mock_update.assert_awaited_once()
    assert mock_update.await_args[0][0] == "existing-rule"


@pytest.mark.asyncio
@patch("modules.twitter_stalker.webhook.send_tweet_alert", new_callable=AsyncMock)
@patch("modules.twitter_stalker.webhook.get_alert_channel_id", new_callable=AsyncMock)
@patch("modules.twitter_stalker.webhook.record_seen_tweet", new_callable=AsyncMock)
async def test_process_webhook_payload_sends_alert(
    mock_seen: AsyncMock,
    mock_channel: AsyncMock,
    mock_send: AsyncMock,
) -> None:
    mock_channel.return_value = 999
    mock_seen.return_value = True

    bot = MagicMock()
    pool = MagicMock()
    payload = {
        "tweets": [
            {
                "id": "999",
                "text": "new post",
                "user": {"userName": "alice", "name": "Alice"},
            }
        ]
    }

    sent = await webhook.process_webhook_payload(pool, bot, payload)
    assert sent == 1
    mock_send.assert_awaited_once()
