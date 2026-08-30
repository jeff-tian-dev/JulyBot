"""FastAPI app factory for the subscription website.

Runs as its own process, independent of the Discord bot's main.py — see
web/main.py for the entrypoint and deploy/start-web.sh / deploy/com.julybot.web.plist.template
for how it's launched. Shares config/settings.py and database/connection.py's
pool singleton pattern with the bot, but never the same running pool instance
(each process opens its own pool via get_pool()).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from database.connection import close_pool, get_pool

logger = logging.getLogger(__name__)

WEB_ROOT = Path(__file__).resolve().parent


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.pool = await get_pool()
    logger.info("Web app started, DB pool ready")
    try:
        yield
    finally:
        await close_pool()
        logger.info("Web app shut down, DB pool closed")


def create_app() -> FastAPI:
    from web.routes import checkout, pages, webhook

    app = FastAPI(title="JulyBot Subscriptions", lifespan=_lifespan)
    app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")
    app.include_router(pages.router)
    app.include_router(checkout.router)
    app.include_router(webhook.router)
    return app


app = create_app()
