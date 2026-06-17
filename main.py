"""Main entry point.

Starts the database pool, scheduler, webhook server, and Discord bot together.
Handles clean shutdown of all on Ctrl-C / SIGTERM.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from aiohttp import web

from config.settings import settings
from database.connection import close_pool, get_pool
from database.models import create_tables
from discord_bot.bot import create_bot
from modules.twitter_stalker.api import close_session as close_twitter_session
from modules.twitter_stalker.rules import sync_filter_rule
from modules.twitter_stalker.webhook import create_webhook_app

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run() -> None:
    pool = await get_pool()
    await create_tables(pool)

    bot = create_bot(pool)

    try:
        await sync_filter_rule(pool)
    except Exception:
        logger.exception("Failed to sync twitter filter rule on startup")

    webhook_app = create_webhook_app(pool, bot)
    webhook_runner = web.AppRunner(webhook_app)
    await webhook_runner.setup()
    webhook_site = web.TCPSite(
        webhook_runner,
        settings.TWITTER_WEBHOOK_HOST,
        settings.TWITTER_WEBHOOK_PORT,
    )
    await webhook_site.start()
    logger.info(
        "Webhook server listening on http://%s:%s%s",
        settings.TWITTER_WEBHOOK_HOST,
        settings.TWITTER_WEBHOOK_PORT,
        settings.TWITTER_WEBHOOK_PATH,
    )

    scheduler = None
    if not settings.twitter_only:
        from modules.ping_automator.scheduler import create_scheduler  # noqa: PLC0415
        scheduler = create_scheduler(pool)
        scheduler.start()
        logger.info("Scheduler started")
    else:
        logger.info("BOT_MODE=twitter — scheduler disabled")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for SIGTERM; KeyboardInterrupt covers SIGINT.
            pass

    bot_task = asyncio.create_task(bot.start(settings.DISCORD_TOKEN), name="discord-bot")
    stop_task = asyncio.create_task(stop.wait(), name="stop-signal")

    try:
        done, _ = await asyncio.wait({bot_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            if task is bot_task and task.exception():
                raise task.exception()  # type: ignore[misc]
    finally:
        logger.info("Shutting down")
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await webhook_runner.cleanup()
        if not bot_task.done():
            await bot.close()
            bot_task.cancel()
            try:
                await bot_task
            except (asyncio.CancelledError, Exception):
                pass
        if not settings.twitter_only:
            from modules.legend_tracker.poller import close_session as close_legend_session  # noqa: PLC0415
            await close_legend_session()
        await close_twitter_session()
        await close_pool()


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting")


if __name__ == "__main__":
    main()
