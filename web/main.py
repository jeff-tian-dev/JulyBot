"""Entry point for the subscription website. Runs as its own process,
independent of the Discord bot's main.py — see deploy/start-web.sh.
"""
from __future__ import annotations

import logging

import uvicorn

from config.settings import settings


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    _configure_logging()
    uvicorn.run("web.app:app", host=settings.WEB_HOST, port=settings.WEB_PORT)


if __name__ == "__main__":
    main()
