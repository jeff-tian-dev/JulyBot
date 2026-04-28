"""Account linking: Discord ID <-> CoC player tag.

Verification uses the CoC `verifyToken` endpoint. The user generates a
short-lived API token in-game (Settings -> More Settings -> API Token)
and supplies it to /link; the bot calls
GET /players/{tag}/verifyToken with that token in the JSON body.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

import aiohttp
import asyncpg

from config.settings import settings

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_SECONDS = 10


def _normalize_tag(coc_tag: str) -> str:
    """Validate CoC tag format and return it uppercased."""
    if not coc_tag or not coc_tag.startswith("#"):
        raise ValueError(f"CoC tag must start with '#', got {coc_tag!r}")
    return coc_tag.upper()


def _encode_tag(coc_tag: str) -> str:
    """URL-encode the leading '#' for use in CoC API URLs."""
    return quote(coc_tag, safe="")


async def _verify_token(coc_tag: str, token: str) -> dict:
    """Call the CoC verifyToken endpoint. Returns parsed JSON or raises."""
    url = f"{settings.COC_API_BASE_URL}/players/{_encode_tag(coc_tag)}/verifytoken"
    headers = {
        "Authorization": f"Bearer {settings.COC_API_TOKEN}",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json={"token": token}) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _fetch_player_name(coc_tag: str) -> str | None:
    """Fetch the player's in-game name. Returns None on failure."""
    url = f"{settings.COC_API_BASE_URL}/players/{_encode_tag(coc_tag)}"
    headers = {
        "Authorization": f"Bearer {settings.COC_API_TOKEN}",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data.get("name")
    except aiohttp.ClientError:
        return None


async def link_account(
    pool: asyncpg.Pool,
    discord_id: int,
    coc_tag: str,
    token: str,
) -> dict:
    """Verify a CoC account token via the API and store the link."""
    coc_tag = _normalize_tag(coc_tag)

    try:
        result = await _verify_token(coc_tag, token)
    except aiohttp.ClientError as e:
        logger.warning("CoC API unreachable during verifyToken: %s", e)
        return {"success": False, "error": "CoC API unavailable"}

    if result.get("status") != "ok":
        return {"success": False, "error": "Token verification failed"}

    coc_name = await _fetch_player_name(coc_tag)

    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (discord_id, coc_tag, coc_name, verified)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (discord_id) DO UPDATE
                SET coc_tag = EXCLUDED.coc_tag,
                    coc_name = EXCLUDED.coc_name,
                    verified = TRUE,
                    linked_at = NOW();
            """,
            discord_id,
            coc_tag,
            coc_name,
        )

    logger.info("Linked discord_id=%s to coc_tag=%s (name=%s)", discord_id, coc_tag, coc_name)
    return {"success": True, "coc_name": coc_name or ""}


async def unlink_account(pool: asyncpg.Pool, discord_id: int) -> bool:
    """Remove a user's linked account. Returns True if a row was deleted."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM users WHERE discord_id = $1;",
            discord_id,
        )
    deleted = result.endswith(" 1")
    if deleted:
        logger.info("Unlinked discord_id=%s", discord_id)
    return deleted


async def get_linked_account(pool: asyncpg.Pool, discord_id: int) -> dict | None:
    """Return the linked CoC account for a Discord user, or None if not linked."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT discord_id, coc_tag, coc_name, verified FROM users WHERE discord_id = $1;",
            discord_id,
        )
    if row is None:
        return None
    return {
        "discord_id": row["discord_id"],
        "coc_tag": row["coc_tag"],
        "coc_name": row["coc_name"],
        "verified": row["verified"],
    }


async def get_all_linked_accounts(pool: asyncpg.Pool) -> list[dict]:
    """Return all verified linked accounts. Used by the ping automator."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT discord_id, coc_tag, coc_name, verified FROM users WHERE verified = TRUE;"
        )
    return [
        {
            "discord_id": r["discord_id"],
            "coc_tag": r["coc_tag"],
            "coc_name": r["coc_name"],
            "verified": r["verified"],
        }
        for r in rows
    ]
