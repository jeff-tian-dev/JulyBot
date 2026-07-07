"""Poll watched X accounts and post new posts to Discord."""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import disnake
from tweety.exceptions import RateLimitReached

from config.settings import settings
from modules.x_monitor import client as x_client
from modules.x_monitor import embeds as embeds_mod
from modules.x_monitor import storage

logger = logging.getLogger(__name__)

ACCOUNT_POLL_DELAY_SECONDS = 2
DEFAULT_RATE_LIMIT_SLEEP_SECONDS = 900


def _role_ping_content(role_id: int | None = None) -> str | None:
    """Discord mention string for the configured ping role, or None."""
    rid = settings.X_PING_ROLE_ID if role_id is None else role_id
    if rid:
        return f"<@&{rid}>"
    return None


def _role_ping_allowed_mentions(role_id: int | None = None) -> disnake.AllowedMentions | None:
    """Allow only the configured role to be pinged."""
    rid = settings.X_PING_ROLE_ID if role_id is None else role_id
    if not rid:
        return None
    return disnake.AllowedMentions(roles=[disnake.Object(id=rid)])


def _tweet_id(tweet) -> int | None:
    """Return a numeric tweet ID, or None for non-tweet timeline entries."""
    raw = getattr(tweet, "id", None)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_retweet(tweet) -> bool:
    return bool(getattr(tweet, "is_retweet", False))


def _content_dedup_id(tweet) -> int | None:
    """Tweet ID used for seen_tweets dedup (underlying content for reposts)."""
    if _is_retweet(tweet):
        retweeted = getattr(tweet, "retweeted_tweet", None)
        if retweeted is not None:
            dedup_id = _tweet_id(retweeted)
            if dedup_id is not None:
                return dedup_id
    return _tweet_id(tweet)


async def poll_x_accounts(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Poll all watched accounts and post new X posts to configured Discord channels."""
    summary = {"accounts_polled": 0, "tweets_posted": 0, "errors": 0}

    if not x_client.is_configured():
        return summary

    if not bot.is_ready():
        logger.debug("Discord bot not ready; skipping X poll")
        return summary

    try:
        accounts = await storage.get_all_watched_accounts(pool)
    except Exception:
        logger.exception("Failed to fetch watched X accounts")
        summary["errors"] += 1
        return summary

    if not accounts:
        return summary

    try:
        app = await x_client.get_client()
    except Exception as exc:
        if x_client.is_auth_error(exc):
            logger.error("X auth failed; check X_COOKIES")
        else:
            logger.exception("Failed to connect X client")
        summary["errors"] += 1
        return summary

    for account in accounts:
        guild_id = account["guild_id"]
        username = account["username"]
        channel_id = account.get("x_channel_id")
        enabled = account.get("x_enabled")

        if not channel_id or enabled is False:
            continue

        channel = bot.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "x_channel_id=%s not found for guild_id=%s",
                channel_id,
                guild_id,
            )
            summary["errors"] += 1
            continue

        summary["accounts_polled"] += 1
        last_seen = account.get("last_seen_tweet_id") or 0

        try:
            tweets_page = await app.get_tweets(username, pages=1, replies=False)
        except RateLimitReached as exc:
            wait = getattr(exc, "retry_after", None) or DEFAULT_RATE_LIMIT_SLEEP_SECONDS
            logger.warning("X rate limit hit; sleeping %ss", wait)
            await asyncio.sleep(wait)
            summary["errors"] += 1
            continue
        except Exception:
            logger.exception("get_tweets failed for @%s", username)
            summary["errors"] += 1
            continue

        candidates = []
        for tweet in tweets_page:
            tweet_id = _tweet_id(tweet)
            if tweet_id is None:
                continue
            if tweet_id <= last_seen:
                break
            candidates.append(tweet)

        if not candidates:
            continue

        # Oldest-first so the high-water mark only advances past tweets we've
        # actually delivered. A failed send stops the run and leaves the rest
        # for the next poll instead of silently skipping them.
        candidates.sort(key=lambda t: _tweet_id(t) or 0)

        highwater = last_seen
        for tweet in candidates:
            timeline_id = _tweet_id(tweet)
            dedup_id = _content_dedup_id(tweet)
            if timeline_id is None or dedup_id is None:
                continue

            # Atomically claim the underlying content. An empty result means it
            # was already posted (e.g. a repost of content seen via another
            # account), so skip it but still let the high-water mark advance.
            newly_seen = await storage.mark_tweets_seen(pool, guild_id, username, [dedup_id])
            if not newly_seen:
                highwater = max(highwater, timeline_id)
                continue

            # Only ping the role if one is configured AND we haven't pinged this
            # guild's X feed within the cooldown window. The post is still sent
            # either way — just without the mention when muted.
            mention = _role_ping_content()
            ping = bool(mention) and await storage.claim_ping_slot(
                pool, guild_id, settings.X_PING_COOLDOWN_HOURS
            )

            try:
                tweet_embeds, components = embeds_mod.build_tweet_message(tweet)
                await channel.send(
                    content=mention if ping else None,
                    embeds=tweet_embeds,
                    components=components,
                    allowed_mentions=_role_ping_allowed_mentions() if ping else None,
                )
            except Exception:
                logger.exception(
                    "Failed to post tweet id=%s for @%s in guild_id=%s",
                    timeline_id,
                    username,
                    guild_id,
                )
                summary["errors"] += 1
                # Release the dedup claim and stop so the high-water mark stays
                # below this tweet; both are retried on the next poll.
                await storage.unmark_tweet_seen(pool, dedup_id)
                break

            summary["tweets_posted"] += 1
            highwater = max(highwater, timeline_id)

        if highwater > last_seen:
            await storage.update_last_seen(pool, guild_id, username, highwater)

        await asyncio.sleep(ACCOUNT_POLL_DELAY_SECONDS)

    logger.info(
        "X poll complete: accounts=%d tweets_posted=%d errors=%d",
        summary["accounts_polled"],
        summary["tweets_posted"],
        summary["errors"],
    )
    return summary
