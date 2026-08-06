"""APScheduler wiring for recurring jobs."""
from __future__ import annotations

import logging

import asyncpg
import disnake
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import settings
from modules.base_finder.pipeline import run_pipeline
from modules.legend_tracker.poller import poll_all_legend_players
from modules.legend_tracker.snapshots import save_snapshot
from modules.roster.watcher import poll_clan_watch, run_daily_watchlist
from modules.x_monitor.poller import poll_x_accounts
from modules.youtube_feed.poller import poll_youtube_channels

logger = logging.getLogger(__name__)


async def poll_legend_players(pool: asyncpg.Pool) -> None:
    """Scheduled job: poll legend stats and save snapshots."""
    try:
        results = await poll_all_legend_players(pool)
    except Exception:
        logger.exception("poll_all_legend_players raised")
        return

    inserted = 0
    for stats in results:
        try:
            if await save_snapshot(pool, stats):
                inserted += 1
        except Exception:
            logger.exception("save_snapshot failed for %s", stats.get("coc_tag"))
    logger.info("Legend poll complete: %d players, %d new snapshots", len(results), inserted)


async def refresh_base_cache(pool: asyncpg.Pool) -> None:
    """Scheduled job: ingest new YouTube VODs into the base cache."""
    try:
        summary = await run_pipeline(pool)
        logger.info("Base finder pipeline summary: %s", summary)
    except Exception:
        logger.exception("run_pipeline raised")


async def send_legend_ping(bot, discord_id: int, message: str) -> None:
    """Send a Discord notification to a user. Stub until Discord layer wires it up."""
    # TODO: implement once discord_bot exposes a way to look up the user.
    logger.info("send_legend_ping stub: discord_id=%s message=%r", discord_id, message)


# Circuit breaker: count consecutive systemic (connection) failures so a broken
# X session — expired cookies or an X site change — alerts once and stops polling
# instead of erroring every interval forever.
_x_consecutive_connect_failures = 0
_x_polling_disabled = False


async def _alert_x_polling_stopped(bot: disnake.Client, failures: int) -> None:
    """Post a one-time operator alert that X polling has been paused."""
    channel_id = settings.X_ALERT_CHANNEL_ID or settings.MOD_LOG_CHANNEL_ID
    if not channel_id:
        logger.error(
            "X polling paused after %d consecutive connection failures, but no "
            "alert channel is configured (set X_ALERT_CHANNEL_ID or MOD_LOG_CHANNEL_ID)",
            failures,
        )
        return

    channel = bot.get_channel(channel_id)
    if channel is None:
        logger.error("X alert channel_id=%s not found; cannot post X-down alert", channel_id)
        return

    try:
        await channel.send(
            "⚠️ **X monitoring stopped.** The X poller failed to connect "
            f"{failures} times in a row (likely expired cookies or an X site "
            "change). Polling has been paused to avoid spamming this channel. "
            "Fix the cookies / tweety patch, then restart the bot "
            "(`./deploy/install-service.sh`) to resume.",
            allowed_mentions=disnake.AllowedMentions.none(),
        )
    except Exception:
        logger.exception("Failed to send X-down alert to channel_id=%s", channel_id)


async def poll_x(
    pool: asyncpg.Pool, bot: disnake.Client, scheduler: AsyncIOScheduler | None = None
) -> None:
    """Scheduled job: poll watched X accounts and post new posts to Discord."""
    global _x_consecutive_connect_failures, _x_polling_disabled
    try:
        summary = await poll_x_accounts(pool, bot)
    except Exception:
        logger.exception("poll_x_accounts raised")
        return

    if summary.get("connect_failed"):
        _x_consecutive_connect_failures += 1
        logger.warning(
            "X poll connection failure %d/%d",
            _x_consecutive_connect_failures,
            settings.X_MAX_CONSECUTIVE_FAILURES,
        )
        if (
            not _x_polling_disabled
            and _x_consecutive_connect_failures >= settings.X_MAX_CONSECUTIVE_FAILURES
        ):
            _x_polling_disabled = True
            await _alert_x_polling_stopped(bot, _x_consecutive_connect_failures)
            if scheduler is not None:
                try:
                    scheduler.pause_job("poll_x_accounts")
                    logger.error(
                        "X polling paused after %d consecutive connection failures",
                        _x_consecutive_connect_failures,
                    )
                except Exception:
                    logger.exception("Failed to pause poll_x_accounts job")
        return

    # Reached X successfully — clear the failure streak.
    if _x_consecutive_connect_failures:
        logger.info(
            "X poll recovered after %d consecutive connection failure(s)",
            _x_consecutive_connect_failures,
        )
    _x_consecutive_connect_failures = 0

    if summary["accounts_polled"] or summary["tweets_posted"] or summary["errors"]:
        logger.info("X poll summary: %s", summary)


async def poll_youtube(pool: asyncpg.Pool, bot: disnake.Client) -> None:
    """Scheduled job: poll watched YouTube channels and post new videos to Discord."""
    try:
        summary = await poll_youtube_channels(pool, bot)
        if summary["channels_polled"] or summary["videos_posted"] or summary["errors"]:
            logger.info("YouTube poll summary: %s", summary)
    except Exception:
        logger.exception("poll_youtube_channels raised")


async def poll_clan_watch_job(pool: asyncpg.Pool, bot: disnake.Client) -> None:
    """Scheduled job: alert on watched-roster members leaving/rejoining the family."""
    try:
        summary = await poll_clan_watch(pool, bot)
        if summary.get("alerts") or summary.get("error"):
            logger.info("Clan watch summary: %s", summary)
    except Exception:
        logger.exception("poll_clan_watch raised")


async def daily_watchlist_job(pool: asyncpg.Pool, bot: disnake.Client) -> None:
    """Scheduled job (1am): post each watched roster's daily leaderboard, then reset."""
    try:
        summary = await run_daily_watchlist(pool, bot)
        logger.info("Daily watchlist summary: %s", summary)
    except Exception:
        logger.exception("run_daily_watchlist raised")


def create_scheduler(pool: asyncpg.Pool, bot: disnake.Client) -> AsyncIOScheduler:
    """Build (but do not start) an AsyncIOScheduler with all recurring jobs."""
    scheduler = AsyncIOScheduler()

    scheduler.add_job(
        poll_legend_players,
        trigger=IntervalTrigger(minutes=settings.LEGEND_POLL_INTERVAL_MINUTES),
        kwargs={"pool": pool},
        id="poll_legend_players",
        replace_existing=True,
    )

    scheduler.add_job(
        refresh_base_cache,
        trigger=IntervalTrigger(hours=settings.CACHE_REFRESH_INTERVAL_HOURS),
        kwargs={"pool": pool},
        id="refresh_base_cache",
        replace_existing=True,
    )

    if settings.X_COOKIES:
        scheduler.add_job(
            poll_x,
            trigger=IntervalTrigger(minutes=settings.X_POLL_INTERVAL_MINUTES),
            kwargs={"pool": pool, "bot": bot, "scheduler": scheduler},
            id="poll_x_accounts",
            replace_existing=True,
        )

    scheduler.add_job(
        poll_youtube,
        trigger=IntervalTrigger(minutes=settings.YOUTUBE_FEED_POLL_INTERVAL_MINUTES),
        kwargs={"pool": pool, "bot": bot},
        id="poll_youtube_feed",
        replace_existing=True,
    )

    if settings.COC_CLAN_TAG or settings.COC_FAMILY_CLAN_TAGS:
        scheduler.add_job(
            poll_clan_watch_job,
            trigger=IntervalTrigger(minutes=settings.CLAN_WATCH_POLL_INTERVAL_MINUTES),
            kwargs={"pool": pool, "bot": bot},
            id="poll_clan_watch",
            replace_existing=True,
        )
        scheduler.add_job(
            daily_watchlist_job,
            trigger=CronTrigger(
                hour=settings.CLAN_WATCH_DAILY_HOUR,
                minute=0,
                timezone=settings.CLAN_WATCH_TIMEZONE,
            ),
            kwargs={"pool": pool, "bot": bot},
            id="daily_watchlist",
            replace_existing=True,
        )

    return scheduler
