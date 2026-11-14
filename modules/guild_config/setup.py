"""/setup business logic that goes beyond plain storage: validating a clan against
the CoC API before it joins a server's family.
"""
from __future__ import annotations

import logging

import asyncpg

from modules.guild_config import storage
from modules.legend_tracker.poller import get_clan

logger = logging.getLogger(__name__)

# Every family clan is fetched from the CoC API on each watchdog poll, and anyone
# can add the bot, so cap how many one server can register.
MAX_CLANS_PER_GUILD = 10


class SetupError(Exception):
    """A /setup request the admin can fix (bad tag, limit reached, ...)."""


async def add_family_clan(
    pool: asyncpg.Pool, guild_id: int, clan_tag: str, added_by: int
) -> dict:
    """Look the clan up in CoC and add it to the server's family.

    Returns {"tag", "name", "added"}; added is False when it was already there.
    Raises SetupError for an invalid/unknown tag or a full family.
    """
    try:
        tag = storage.normalize_tag(clan_tag)
    except ValueError as e:
        raise SetupError("Enter a clan tag, e.g. #2PP0JCCL.") from e

    existing = await storage.list_clans(pool, guild_id)
    if any(c["clan_tag"] == tag for c in existing):
        name = next(c["clan_name"] for c in existing if c["clan_tag"] == tag)
        return {"tag": tag, "name": name, "added": False}
    if len(existing) >= MAX_CLANS_PER_GUILD:
        raise SetupError(
            f"This server already has {MAX_CLANS_PER_GUILD} clans. Remove one first."
        )

    clan = await get_clan(tag)
    if clan is None:
        raise SetupError(
            f"Couldn't find clan `{tag}` in Clash of Clans. Check the tag and try again."
        )
    name = clan.get("name")
    added = await storage.add_clan(pool, guild_id, tag, name, added_by)
    return {"tag": tag, "name": name, "added": added}
