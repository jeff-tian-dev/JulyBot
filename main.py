"""Main entry point.

Starts the database pool, scheduler, and Discord bot together.
Handles clean shutdown of all on Ctrl-C / SIGTERM.
"""
from __future__ import annotations

import asyncio
import logging
import signal

from config.settings import settings
from database.connection import close_pool, get_pool
from database.models import create_tables
from discord_bot.bot import create_bot
from modules.legend_tracker.poller import close_session as close_legend_session
from modules.ping_automator.scheduler import create_scheduler
from modules.x_monitor.client import close_client as close_x_client
from modules.youtube_feed.storage import seed_unseeded_channels

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def _run() -> None:
    pool = await get_pool()
    await create_tables(pool)

    seeded = await seed_unseeded_channels(pool)
    if seeded:
        logger.info("Seeded %d unseeded YouTube channel(s) on startup", seeded)

    bot = create_bot(pool)

    scheduler = create_scheduler(pool, bot)
    scheduler.start()
    logger.info("Scheduler started")

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
        scheduler.shutdown(wait=False)
        if not bot_task.done():
            await bot.close()
            bot_task.cancel()
            try:
                await bot_task
            except (asyncio.CancelledError, Exception):
                pass
        await close_legend_session()
        await close_x_client()
        await close_pool()


def main() -> None:
    _configure_logging()
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrupted; exiting")


if __name__ == "__main__":
    main()
