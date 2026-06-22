"""tweety-ns session wrapper for Twitter/X scraping."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from config.settings import settings
from modules.twitter_monitor.tweety_patch import apply_tweety_patch

apply_tweety_patch()

from tweety import TwitterAsync
from tweety.exceptions import AuthenticationRequired, InvalidCredentials

from config.settings import settings

logger = logging.getLogger(__name__)

SESSION_DIR = Path("./data/twitter")
_client: TwitterAsync | None = None


def _session_path() -> str:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return str(SESSION_DIR / settings.TWITTER_SESSION_NAME)


async def get_client() -> TwitterAsync:
    """Return a connected tweety client, creating one on first call."""
    global _client
    if _client is not None:
        return _client

    if not settings.TWITTER_COOKIES:
        raise InvalidCredentials("TWITTER_COOKIES is not configured")

    session_name = _session_path()
    app = TwitterAsync(session_name)

    cwd = os.getcwd()
    try:
        os.chdir(SESSION_DIR)
        try:
            await app.connect()
            logger.info("Twitter session restored for @%s", app.me.username)
        except Exception:
            await app.load_cookies(settings.TWITTER_COOKIES)
            logger.info("Twitter session loaded from cookies for @%s", app.me.username)
    finally:
        os.chdir(cwd)

    _client = app
    return _client


async def close_client() -> None:
    """Release the tweety client singleton."""
    global _client
    _client = None


async def fetch_latest_tweet_id(username: str) -> int:
    """Return the newest tweet ID for a user, or 0 if none found."""
    app = await get_client()
    tweets = await app.get_tweets(username, pages=1, replies=False)
    ids = []
    for t in tweets:
        raw = getattr(t, "id", None)
        if raw is None:
            continue
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return max(ids) if ids else 0


def is_configured() -> bool:
    """True when Twitter cookie auth is available."""
    return bool(settings.TWITTER_COOKIES)


def is_auth_error(exc: BaseException) -> bool:
    """True for tweety auth/credential failures."""
    return isinstance(exc, (InvalidCredentials, AuthenticationRequired))
