"""Legend League polling against the CoC API.

Uses a single shared aiohttp.ClientSession for all calls.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import aiohttp
import asyncpg

from config.settings import settings
from modules.account_linker.linker import get_all_linked_accounts

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 10
LEGEND_LEAGUE_ID = 29000022

_session: aiohttp.ClientSession | None = None


async def get_session() -> aiohttp.ClientSession:
    """Return the module-level aiohttp session, creating it on first call."""
    global _session
    if _session is None or _session.closed:
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        _session = aiohttp.ClientSession(timeout=timeout)
        logger.info("Created shared aiohttp session for legend_tracker")
    return _session


async def close_session() -> None:
    """Close the shared aiohttp session on shutdown."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        logger.info("Closed shared aiohttp session for legend_tracker")
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
    except aiohttp.ClientError as e:
        logger.warning("CoC API error for %s: %s", coc_tag, e)
        return None


async def get_legend_stats(coc_tag: str) -> dict | None:
    """Extract legend league stats for the current season."""
    player = await get_player(coc_tag)
    if player is None:
        return None

    league = player.get("league") or {}
    if league.get("id") != LEGEND_LEAGUE_ID:
        return None

    legend_stats = player.get("legendStatistics") or {}
    current = legend_stats.get("currentSeason") or {}

    return {
        "coc_tag": coc_tag,
        "trophies": player.get("trophies", 0),
        "attacks_done": player.get("attackWins", 0),
        "defenses_done": player.get("defenseWins", 0),
        "attack_wins": current.get("trophies", player.get("attackWins", 0)),
        "defense_wins": player.get("defenseWins", 0),
    }


async def poll_all_legend_players(pool: asyncpg.Pool) -> list[dict]:
    """Fetch legend stats for every verified linked account."""
    accounts = await get_all_linked_accounts(pool)
    results: list[dict] = []
    for acct in accounts:
        tag = acct["coc_tag"]
        try:
            stats = await get_legend_stats(tag)
        except Exception:
            logger.exception("Unexpected error polling %s", tag)
            continue
        if stats is not None:
            results.append(stats)
    logger.info("Polled %d/%d accounts for legend stats", len(results), len(accounts))
    return results
