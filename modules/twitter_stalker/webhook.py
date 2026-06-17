"""Parse twitterapi.io webhook payloads and dispatch alerts."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg
import disnake
from aiohttp import web

from config.settings import settings
from modules.twitter_stalker.accounts import get_alert_channel_id, record_seen_tweet
from modules.twitter_stalker.notifier import send_tweet_alert

logger = logging.getLogger(__name__)


def _dig(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _extract_tweets(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    """Normalize webhook body to a list of tweet dicts."""
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]

    if not isinstance(payload, dict):
        return []

    for key in ("tweets", "data", "tweet", "result"):
        val = payload.get(key)
        if isinstance(val, list):
            return [t for t in val if isinstance(t, dict)]
        if isinstance(val, dict):
            nested = val.get("tweets")
            if isinstance(nested, list):
                return [t for t in nested if isinstance(t, dict)]
            return [val]

    if _dig(payload, "id", "id_str", "tweet_id"):
        return [payload]

    return []


def _parse_tweet(raw: dict[str, Any]) -> dict[str, Any] | None:
    tweet_id = str(
        _dig(raw, "id", "id_str", "tweet_id", "tweetId") or ""
    ).strip()
    if not tweet_id:
        return None

    text = _dig(raw, "text", "full_text", "tweet_text", "content") or ""

    user = raw.get("user") or raw.get("author") or {}
    if not isinstance(user, dict):
        user = {}

    username = (
        _dig(raw, "username", "userName", "screen_name")
        or _dig(user, "userName", "username", "screen_name", "name")
        or ""
    ).lstrip("@").lower()

    display_name = _dig(user, "name", "display_name") or username

    media_url = None
    media = raw.get("media") or raw.get("extendedEntities") or raw.get("entities")
    if isinstance(media, dict):
        media_items = media.get("media") or media.get("photos") or []
        if isinstance(media_items, list) and media_items:
            first = media_items[0]
            if isinstance(first, dict):
                media_url = first.get("url") or first.get("media_url_https")

    url = _dig(raw, "url", "tweet_url") or (
        f"https://x.com/{username}/status/{tweet_id}" if username else None
    )

    return {
        "tweet_id": tweet_id,
        "text": text,
        "username": username,
        "display_name": display_name,
        "url": url,
        "media_url": media_url,
        "raw": raw,
    }


def should_notify(raw: dict[str, Any]) -> bool:
    """Return True for original tweets; skip retweets and replies."""
    if raw.get("retweeted_status") or raw.get("retweetedStatus"):
        return False
    if raw.get("is_retweet") or raw.get("isRetweet"):
        return False
    retweeted = raw.get("retweeted_tweet") or raw.get("retweetedTweet")
    if retweeted:
        return False

    if raw.get("in_reply_to_status_id") or raw.get("in_reply_to_status_id_str"):
        return False
    if raw.get("inReplyToStatusId") or raw.get("in_reply_to_tweet_id"):
        return False
    if raw.get("in_reply_to_user_id") or raw.get("inReplyToUserId"):
        return False

    return True


async def process_webhook_payload(
    pool: asyncpg.Pool,
    bot: disnake.Client,
    payload: dict[str, Any] | list[Any],
) -> int:
    """Parse payload, dedup, and send Discord alerts. Returns count sent."""
    channel_id = await get_alert_channel_id(pool)
    if not channel_id:
        logger.warning("Webhook received but no alert channel configured (/settwitterchannel)")
        return 0

    tweets = _extract_tweets(payload)
    logger.info("Webhook: extracted %d tweet(s) from payload keys=%s", len(tweets), list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)

    sent = 0
    for raw in tweets:
        if not should_notify(raw):
            logger.info("Webhook: skipping tweet (retweet/reply filter) id=%s", raw.get("id") or raw.get("tweet_id"))
            continue

        tweet = _parse_tweet(raw)
        if not tweet:
            logger.warning("Webhook: could not parse tweet from raw: %s", str(raw)[:300])
            continue

        is_new = await record_seen_tweet(
            pool,
            tweet["tweet_id"],
            tweet.get("username"),
        )
        if not is_new:
            logger.info("Webhook: duplicate tweet id=%s, skipping", tweet["tweet_id"])
            continue

        await send_tweet_alert(bot, channel_id, tweet)
        sent += 1

    return sent


def create_webhook_app(pool: asyncpg.Pool, bot: disnake.Client) -> web.Application:
    """Build aiohttp app with the twitter webhook route."""
    app = web.Application()

    async def twitter_webhook(request: web.Request) -> web.Response:
        # twitterapi.io sends a GET to verify the endpoint is reachable
        if request.method == "GET":
            return web.Response(status=200, text="OK")

        body = await request.read()
        if not body:
            return web.Response(status=200, text="OK")

        try:
            payload = await request.json()
        except Exception:
            logger.warning("Webhook received non-JSON body (len=%d), ignoring", len(body))
            return web.Response(status=200, text="OK")

        logger.debug("Webhook payload keys: %s", list(payload.keys()) if isinstance(payload, dict) else type(payload))

        async def _dispatch() -> None:
            try:
                count = await process_webhook_payload(pool, bot, payload)
                if count:
                    logger.info("Webhook dispatched %d alert(s)", count)
            except Exception:
                logger.exception("Webhook processing failed")

        asyncio.create_task(_dispatch())
        return web.Response(status=200, text="OK")

    app.router.add_route("*", settings.TWITTER_WEBHOOK_PATH, twitter_webhook)
    return app
