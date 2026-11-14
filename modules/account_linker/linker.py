"""Account linking: Discord ID <-> CoC player tag.

Verification uses the CoC `verifyToken` endpoint. The user generates a
short-lived API token in-game (Settings -> More Settings -> API Token)
and supplies it to /link; the bot calls
GET /players/{tag}/verifyToken with that token in the JSON body.

Admins can also link a tag without a token (/adminlink); those links are
stored unverified and never take a tag away from another user.
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import quote

import aiohttp
import asyncpg

from config.settings import settings

logger = logging.getLogger(__name__)

VERIFY_TIMEOUT_SECONDS = 10

# CoC tags are uppercase letters + digits only. Anything else is dropped — this
# includes '#', spaces, and the invisible bidi isolate marks (U+2066/U+2069) that
# come along when a tag is copied out of a rendered embed/board.
_TAG_STRIP_RE = re.compile(r"[^A-Z0-9]")


def _normalize_tag(coc_tag: str) -> str:
    """Clean up a user-entered CoC tag.

    Uppercases, fixes the common O->0 typo (CoC tags never contain the letter O),
    and strips every non-tag character ('#', spaces, stray Unicode). Re-adds '#'.
    """
    cleaned = _TAG_STRIP_RE.sub("", (coc_tag or "").upper().replace("O", "0"))
    if not cleaned:
        raise ValueError("Please enter your CoC player tag, e.g. #2PP0JCCL.")
    return f"#{cleaned}"


def _encode_tag(coc_tag: str) -> str:
    """URL-encode the leading '#' for use in CoC API URLs."""
    return quote(coc_tag, safe="")


async def _verify_token(coc_tag: str, token: str) -> str:
    """Call the CoC verifyToken endpoint.

    Returns the verification status: 'ok' (token belongs to this player),
    'invalid' (wrong/expired token), or 'notfound' (no such player tag).
    Raises aiohttp.ClientError on a network or credential failure.
    """
    url = f"{settings.COC_API_BASE_URL}/players/{_encode_tag(coc_tag)}/verifytoken"
    headers = {
        "Authorization": f"Bearer {settings.COC_API_TOKEN}",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, headers=headers, json={"token": token}) as resp:
            if resp.status == 404:
                return "notfound"
            resp.raise_for_status()
            data = await resp.json()
            return data.get("status", "invalid")


async def _lookup_player(coc_tag: str) -> tuple[str, str | None]:
    """Look a player up: ('ok', name), ('notfound', None), or ('error', None)
    when the API couldn't answer."""
    url = f"{settings.COC_API_BASE_URL}/players/{_encode_tag(coc_tag)}"
    headers = {
        "Authorization": f"Bearer {settings.COC_API_TOKEN}",
        "Accept": "application/json",
    }
    timeout = aiohttp.ClientTimeout(total=VERIFY_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    return "notfound", None
                if resp.status != 200:
                    return "error", None
                data = await resp.json()
                return "ok", data.get("name")
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return "error", None


async def _fetch_player_name(coc_tag: str) -> str | None:
    """Fetch the player's in-game name. Returns None on failure."""
    _, name = await _lookup_player(coc_tag)
    return name


async def link_account(
    pool: asyncpg.Pool,
    discord_id: int,
    coc_tag: str,
    token: str,
) -> dict:
    """Verify a CoC account token via the API and store the link."""
    coc_tag = _normalize_tag(coc_tag)

    try:
        status = await _verify_token(coc_tag, token)
    except aiohttp.ClientError as e:
        logger.warning("CoC API unreachable during verifyToken: %s", e)
        return {"success": False, "error": "CoC API is unavailable right now. Try again in a moment."}

    if status == "notfound":
        return {"success": False, "error": f"No player found with tag {coc_tag}. Double-check the tag."}
    if status != "ok":
        return {
            "success": False,
            "error": (
                "Token didn't verify. Generate a fresh one in-game "
                "(Settings -> More Settings -> API Token) and link again right away "
                "— each token is single-use and expires quickly."
            ),
        }

    coc_name = await _fetch_player_name(coc_tag)

    # coc_tag is unique; a Discord user may hold several. Re-linking a tag you
    # already own refreshes the name; a tag passing verification under a new
    # Discord id is reassigned (only the current in-game owner can produce a
    # valid token, so this safely handles account transfers).
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (discord_id, coc_tag, coc_name, verified)
            VALUES ($1, $2, $3, TRUE)
            ON CONFLICT (coc_tag) DO UPDATE
                SET discord_id = EXCLUDED.discord_id,
                    coc_name = EXCLUDED.coc_name,
                    verified = TRUE,
                    linked_at = NOW();
            """,
            discord_id,
            coc_tag,
            coc_name,
        )

    logger.info("Linked discord_id=%s to coc_tag=%s (name=%s)", discord_id, coc_tag, coc_name)
    return {"success": True, "coc_name": coc_name or "", "coc_tag": coc_tag}


async def admin_link_account(
    pool: asyncpg.Pool,
    discord_id: int,
    coc_tag: str,
    admin_id: int,
) -> dict:
    """Link a CoC tag to a Discord user on an admin's say-so, without a token.

    Stored unverified. Links are global (every server sees them), so an admin
    link never takes a tag from someone else and never downgrades an existing
    verified link; a later /link by the real owner (verified) still takes the
    tag over, as it does today.

    Returns {"success": True, coc_tag, coc_name, already, verified} or
    {"success": False, error, holder_id}. holder_id is set only when the tag
    belongs to another Discord user.
    """
    coc_tag = _normalize_tag(coc_tag)

    status, coc_name = await _lookup_player(coc_tag)
    if status == "notfound":
        return {"success": False, "error": f"No player found with tag {coc_tag}. Double-check the tag.", "holder_id": None}
    if status != "ok":
        return {"success": False, "error": "CoC API is unavailable right now. Try again in a moment.", "holder_id": None}

    async with pool.acquire() as conn:
        holder = await conn.fetchrow(
            "SELECT discord_id, verified FROM users WHERE coc_tag = $1;", coc_tag
        )
        if holder is not None:
            if holder["discord_id"] != discord_id:
                return {
                    "success": False,
                    "error": f"{coc_tag} is already linked to another Discord user.",
                    "holder_id": holder["discord_id"],
                }
            return {
                "success": True,
                "coc_tag": coc_tag,
                "coc_name": coc_name or "",
                "already": True,
                "verified": bool(holder["verified"]),
            }
        result = await conn.execute(
            """
            INSERT INTO users (discord_id, coc_tag, coc_name, verified)
            VALUES ($1, $2, $3, FALSE)
            ON CONFLICT (coc_tag) DO NOTHING;
            """,
            discord_id,
            coc_tag,
            coc_name,
        )
    if not result.endswith(" 1"):
        # Someone linked it between the check and the insert.
        return {"success": False, "error": f"{coc_tag} was just linked by someone else. Try again.", "holder_id": None}

    logger.info(
        "Admin %s linked discord_id=%s to coc_tag=%s (name=%s, unverified)",
        admin_id, discord_id, coc_tag, coc_name,
    )
    return {"success": True, "coc_tag": coc_tag, "coc_name": coc_name or "", "already": False, "verified": False}


async def unlink_account(pool: asyncpg.Pool, discord_id: int, coc_tag: str) -> bool:
    """Remove one of a user's linked accounts. Returns True if a row was deleted.

    Scoped to the caller's own discord_id so a user can only unlink their own
    accounts, even if they pass someone else's tag.
    """
    coc_tag = _normalize_tag(coc_tag)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM users WHERE discord_id = $1 AND coc_tag = $2;",
            discord_id,
            coc_tag,
        )
    deleted = result.endswith(" 1")
    if deleted:
        logger.info("Unlinked discord_id=%s coc_tag=%s", discord_id, coc_tag)
    return deleted


async def get_linked_accounts(pool: asyncpg.Pool, discord_id: int) -> list[dict]:
    """Return all CoC accounts linked to a Discord user, oldest link first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT discord_id, coc_tag, coc_name, verified
            FROM users
            WHERE discord_id = $1
            ORDER BY linked_at ASC;
            """,
            discord_id,
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


async def get_all_accounts(pool: asyncpg.Pool, discord_ids: list[int]) -> list[dict]:
    """Return the linked accounts of the given Discord users, grouped by user.

    Used by the admin dump command, which passes the invoking server's member
    ids: links are global, but one server's admin must only see its own members.
    Ordered by discord_id then linked_at so the caller can group consecutive
    rows by the same user without sorting.
    """
    if not discord_ids:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT discord_id, coc_tag, coc_name, verified, linked_at
            FROM users
            WHERE discord_id = ANY($1::bigint[])
            ORDER BY discord_id, linked_at ASC;
            """,
            list(discord_ids),
        )
    return [
        {
            "discord_id": r["discord_id"],
            "coc_tag": r["coc_tag"],
            "coc_name": r["coc_name"],
            "verified": r["verified"],
            "linked_at": r["linked_at"],
        }
        for r in rows
    ]


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
