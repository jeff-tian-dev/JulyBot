"""CoC API client for Ranked Battles tournament groups.

Uses a single shared aiohttp.ClientSession, mirroring
modules/legend_tracker/poller.py. Kept as its own client (rather than added to
legend_tracker) because this is a different bounded context: on-demand lookups
with no DB and no linked-account dependency, the same reasoning that keeps
x_monitor/client.py and youtube_feed/fetcher.py as separate HTTP layers.
"""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote

import aiohttp

from config.settings import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    """Return the module-level aiohttp session, creating it on first call."""
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        _session = aiohttp.ClientSession(timeout=timeout)
        logger.info("Created shared aiohttp session for ranked_tracker")
    return _session


async def close_session() -> None:
    """Close the shared aiohttp session on shutdown."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        logger.info("Closed shared aiohttp session for ranked_tracker")
    _session = None


def _encode_tag(coc_tag: str) -> str:
    return quote(coc_tag, safe="")


async def get_player(coc_tag: str) -> dict | None:
    """Fetch full player data from GET /players/{tag}."""
    url = f"{settings.COC_API_BASE_URL}/players/{_encode_tag(coc_tag)}"
    headers = {
        "Authorization": f"Bearer {settings.COC_API_TOKEN}",
        "Accept": "application/json",
    }
    session = await get_session()
    try:
        async with session.get(url, headers=headers) as resp:
            if resp.status == 403:
                logger.error(
                    "CoC API returned 403 for /players/%s — the token's whitelisted IP "
                    "likely doesn't match the request source. Check the allowed IP at "
                    "developer.clashofclans.com (or whitelist RoyaleAPI's 45.79.218.79 if "
                    "using COC_API_BASE_URL=https://cocproxy.royaleapi.dev/v1).",
                    coc_tag,
                )
                return None
            if resp.status != 200:
                logger.warning("CoC API GET /players/%s returned %s", coc_tag, resp.status)
                return None
            return await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("CoC API error for %s: %s", coc_tag, e)
        return None


class LeagueGroupFetchError(Exception):
    """Carries the raw CoC API failure (status + body) so callers can surface it.

    Kept distinct from returning None (as get_player does) because the
    /leaguegroup endpoint's shape/path was reverse-engineered from library
    source and never confirmed against a live response — when it fails,
    seeing the exact status/body is far more useful than a generic warning
    buried in a log file the operator may not have access to.
    """

    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:300]}")


async def get_league_group(group_tag: str, season_id: int | str, player_tag: str) -> dict:
    """Fetch a Ranked Battles tournament group from GET /leaguegroup/{tag}/{seasonId}.

    Only the group tag is URL-encoded; season_id is a bare path segment.
    player_tag is a REQUIRED query parameter — confirmed live against the API
    (it 400s with "Required parameter 'playerTag' missing" without it), even
    though the clashofclans.js typing that this endpoint was reverse-engineered
    from marks it optional. Raises LeagueGroupFetchError (never returns None)
    so the caller can show the real status/body instead of a generic message.
    """
    query = f"?playerTag={_encode_tag(player_tag)}"
    url = f"{settings.COC_API_BASE_URL}/leaguegroup/{_encode_tag(group_tag)}/{season_id}{query}"
    headers = {
        "Authorization": f"Bearer {settings.COC_API_TOKEN}",
        "Accept": "application/json",
    }
    session = await get_session()
    try:
        async with session.get(url, headers=headers) as resp:
            body_text = await resp.text()
            if resp.status != 200:
                logger.warning(
                    "CoC API GET /leaguegroup/%s/%s returned %s: %s",
                    group_tag,
                    season_id,
                    resp.status,
                    body_text[:500],
                )
                raise LeagueGroupFetchError(resp.status, body_text)
            return json.loads(body_text)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("CoC API error fetching league group %s/%s: %s", group_tag, season_id, e)
        raise LeagueGroupFetchError(0, str(e)) from e
