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


async def poll_x(pool: asyncpg.Pool, bot: disnake.Client) -> None:
    """Scheduled job: poll watched X accounts and post new posts to Discord."""
    try:
        summary = await poll_x_accounts(pool, bot)
        if summary["accounts_polled"] or summary["tweets_posted"] or summary["errors"]:
            logger.info("X poll summary: %s", summary)
    except Exception:
        logger.exception("poll_x_accounts raised")


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
            kwargs={"pool": pool, "bot": bot},
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
