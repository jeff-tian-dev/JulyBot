"""tweety-ns session wrapper for X scraping."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from config.settings import settings
from modules.x_monitor.tweety_patch import apply_tweety_patch

apply_tweety_patch()

from tweety import TwitterAsync
from tweety.exceptions import AuthenticationRequired, InvalidCredentials

logger = logging.getLogger(__name__)

SESSION_DIR = Path("./data/x")
_LEGACY_SESSION_DIR = Path("./data/twitter")
_client: TwitterAsync | None = None


def _session_dir() -> Path:
    """Prefer data/x; fall back to legacy data/twitter if the session file lives there."""
    name = settings.X_SESSION_NAME
    if (_LEGACY_SESSION_DIR / name).exists() and not (SESSION_DIR / name).exists():
        return _LEGACY_SESSION_DIR
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR


def _session_path() -> str:
    session_dir = _session_dir()
    return str(session_dir / settings.X_SESSION_NAME)


async def get_client() -> TwitterAsync:
    """Return a connected tweety client, creating one on first call."""
    global _client
    if _client is not None:
        return _client

    if not settings.X_COOKIES:
        raise InvalidCredentials("X_COOKIES is not configured")

    session_name = _session_path()
    app = TwitterAsync(session_name)

    cwd = os.getcwd()
    session_dir = _session_dir()
    try:
        os.chdir(session_dir)
        try:
            await app.connect()
            logger.info("X session restored for @%s", app.me.username)
        except Exception:
            await app.load_cookies(settings.X_COOKIES)
            logger.info("X session loaded from cookies for @%s", app.me.username)
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
    """True when X cookie auth is available."""
    return bool(settings.X_COOKIES)


def is_auth_error(exc: BaseException) -> bool:
    """True for tweety auth/credential failures."""
    return isinstance(exc, (InvalidCredentials, AuthenticationRequired))
