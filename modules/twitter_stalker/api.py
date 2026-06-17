"""twitterapi.io HTTP client.

Uses a single shared aiohttp.ClientSession for all calls.
"""
from __future__ import annotations

import logging

import aiohttp

from config.settings import settings

logger = logging.getLogger(__name__)

API_BASE_URL = "https://api.twitterapi.io"
REQUEST_TIMEOUT_SECONDS = 10

_session: aiohttp.ClientSession | None = None


class TwitterApiError(Exception):
    """Raised when twitterapi.io returns an error response."""


def api_key_configured() -> bool:
    return bool(settings.TWITTERAPI_IO_KEY)


def _headers() -> dict[str, str]:
    if not settings.TWITTERAPI_IO_KEY:
        raise TwitterApiError(
            "TWITTERAPI_IO_KEY is not set. Add it to .env (see .env.example)."
        )
    return {"x-api-key": settings.TWITTERAPI_IO_KEY}


async def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        _session = aiohttp.ClientSession(timeout=timeout)
        logger.info("Created shared aiohttp session for twitter_stalker")
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        logger.info("Closed shared aiohttp session for twitter_stalker")
    _session = None


async def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json_body: dict | None = None,
) -> dict:
    session = await get_session()
    url = f"{API_BASE_URL}{path}"
    async with session.request(
        method,
        url,
        headers=_headers(),
        params=params,
        json=json_body,
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            msg = data.get("msg") or data.get("message") or resp.reason
            raise TwitterApiError(f"HTTP {resp.status}: {msg}")
        if isinstance(data, dict) and data.get("status") == "error":
            raise TwitterApiError(data.get("msg", "Unknown twitterapi.io error"))
        return data


async def get_user_info(username: str) -> dict:
    """Fetch user profile by screen name. Returns the inner user dict."""
    data = await _request(
        "GET",
        "/twitter/user/info",
        params={"userName": username},
    )
    user = data.get("data")
    if not user:
        raise TwitterApiError(f"User @{username} not found")
    return user


async def get_filter_rules() -> list[dict]:
    data = await _request("GET", "/oapi/tweet_filter/get_rules")
    return data.get("rules") or []


async def add_filter_rule(tag: str, value: str, interval_seconds: int) -> str:
    data = await _request(
        "POST",
        "/oapi/tweet_filter/add_rule",
        json_body={
            "tag": tag,
            "value": value,
            "interval_seconds": interval_seconds,
        },
    )
    rule_id = data.get("rule_id")
    if not rule_id:
        raise TwitterApiError("add_rule did not return rule_id")
    return str(rule_id)


async def update_filter_rule(
    rule_id: str,
    tag: str,
    value: str,
    interval_seconds: int,
    *,
    activate: bool = False,
) -> None:
    body: dict = {
        "rule_id": rule_id,
        "tag": tag,
        "value": value,
        "interval_seconds": interval_seconds,
    }
    if activate:
        body["is_effect"] = 1
    await _request("POST", "/oapi/tweet_filter/update_rule", json_body=body)


async def delete_filter_rule(rule_id: str) -> None:
    await _request(
        "DELETE",
        "/oapi/tweet_filter/delete_rule",
        json_body={"rule_id": rule_id},
    )
