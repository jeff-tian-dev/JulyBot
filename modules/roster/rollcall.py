"""Roll call: at the daily legend reset (05:00 UTC), report who wasn't in the family.

Runs once a day alongside the watchdog's daily board. For every server with a
clan family and roll call not off, it fetches each family clan fresh and checks
every member of the server's watched rosters (/roster watch). Anyone not in a
family clan at that moment "missed the reset" and is listed, one message per
roster, with the clan they were in instead.

Mode (/toggle feature:Roll call) decides where it goes and how people are named:
- on:      the roll call channel (/setup rollcall); Discord users are pinged.
- testing: the test channel (/setup testchannel); the same message, but each
           ping is replaced by the user's plain @name so nobody is notified.
- off:     nothing.

If any of a server's family clans can't be fetched, that server's roll call is
not posted (a short notice says why instead), because everyone in the missing
clan would otherwise look like they'd missed the reset.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
import disnake

from modules.guild_config import modes
from modules.guild_config import storage as guild_config
from modules.legend_tracker.poller import get_player
from modules.roster import storage
from modules.roster.watcher import _fetch_clan, _isolate

logger = logging.getLogger(__name__)

FEATURE = "rollcall"
MESSAGE_LIMIT = 2000  # Discord's cap on a plain message's content


def _reset_stamp(now: datetime) -> str:
    """A Discord date timestamp for the reset being reported (renders in each viewer's zone)."""
    return f"<t:{int(now.timestamp())}:D>"


def format_rollcall(
    roster_name: str, missed: list[dict], mode: str, names: dict[int, str], now: datetime
) -> list[str]:
    """The roll call message(s) for one roster, split to fit Discord's message cap.

    `missed` is [{tag, name, clan, discord_id}]. In on mode a Discord user is a
    real mention; in testing mode it's their plain @name from `names`, so the
    message reads the same without notifying anyone.
    """
    title = f"**Roll call — {_isolate(roster_name)}**"
    if not missed:
        return [f"✅ {title} — everyone was in the family at the {_reset_stamp(now)} reset."]

    header = f"📋 {title} — {len(missed)} missed the {_reset_stamp(now)} reset:"
    lines = []
    for m in missed:
        player = f"**{_isolate(m['name'] or m['tag'])}** (`{m['tag']}`)"
        where = f"in {_isolate(m['clan'])}" if m.get("clan") else "not in a clan"
        who = ""
        if m.get("discord_id"):
            if mode == modes.ON:
                who = f"<@{m['discord_id']}> — "
            else:
                who = f"@{names.get(m['discord_id'], 'unknown user')} — "
        lines.append(f"• {who}{player} — {where}")

    messages: list[str] = []
    current = header
    for line in lines:
        if len(current) + 1 + len(line) > MESSAGE_LIMIT:
            messages.append(current)
            current = line
        else:
            current = f"{current}\n{line}"
    messages.append(current)
    return messages


def _allowed_mentions(mode: str, missed: list[dict]) -> disnake.AllowedMentions:
    """On mode pings exactly the listed users; testing pings nobody."""
    if mode != modes.ON:
        return disnake.AllowedMentions.none()
    ids = [m["discord_id"] for m in missed if m.get("discord_id")]
    return disnake.AllowedMentions(
        everyone=False, roles=False, users=[disnake.Object(id=i) for i in ids]
    )


async def _display_names(bot: disnake.Client, guild, ids: list[int]) -> dict[int, str]:
    """Plain names for testing mode: server nickname, else username."""
    names: dict[int, str] = {}
    for user_id in ids:
        member = guild.get_member(user_id) if guild is not None else None
        if member is not None:
            names[user_id] = member.display_name
            continue
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except disnake.HTTPException:
                continue
        names[user_id] = user.name
    return names


async def _family_members(family: list[str], clans: dict[str, dict | None]) -> set[str] | None:
    """Every tag in any of the family clans, or None if one couldn't be fetched."""
    tags: set[str] = set()
    for clan_tag in family:
        if clan_tag not in clans:
            clans[clan_tag] = await _fetch_clan(clan_tag)
        clan = clans[clan_tag]
        if clan is None:
            return None
        tags.update(m["tag"] for m in clan.get("memberList", []))
    return tags


async def _missed(
    pool: asyncpg.Pool, guild_id: int, members: list[dict], in_family: set[str]
) -> list[dict]:
    """The members not in the family, with their name and the clan they're in."""
    out = [m for m in members if m["coc_tag"] not in in_family]
    if not out:
        return []
    known = await storage.get_membership_map(pool, guild_id, [m["coc_tag"] for m in out])
    missed = []
    for m in out:
        tag = m["coc_tag"]
        player = await get_player(tag)
        if player is not None:
            name = player.get("name")
            clan = (player.get("clan") or {}).get("name")
        else:
            name = (known.get(tag) or {}).get("coc_name")
            clan = None
        missed.append({"tag": tag, "name": name, "clan": clan, "discord_id": m["discord_id"]})
    return missed


async def _send(channel, messages: list[str], allowed: disnake.AllowedMentions) -> None:
    for content in messages:
        await channel.send(content, allowed_mentions=allowed)


async def _run_guild(
    pool: asyncpg.Pool,
    bot: disnake.Client,
    guild_id: int,
    rosters: list[dict],
    family: list[str],
    clans: dict[str, dict | None],
    now: datetime,
) -> dict:
    mode, channel = await modes.feature_channel(bot, pool, guild_id, FEATURE)
    if mode == modes.OFF:
        return {"skipped": "off"}
    if channel is None:
        return {"skipped": "no channel"}

    in_family = await _family_members(family, clans)
    if in_family is None:
        logger.warning("Roll call: a family clan fetch failed for guild=%s; not posting", guild_id)
        await channel.send(
            f"⚠️ Roll call for the {_reset_stamp(now)} reset couldn't run: the Clash of "
            "Clans API didn't return every family clan, so nobody is reported.",
            allowed_mentions=disnake.AllowedMentions.none(),
        )
        return {"error": "clan fetch failed"}

    guild = getattr(channel, "guild", None)
    reported = 0
    for roster in rosters:
        members = await storage.get_rollcall_members(pool, roster["id"])
        missed = await _missed(pool, guild_id, members, in_family)
        names: dict[int, str] = {}
        if mode == modes.TESTING:
            names = await _display_names(
                bot, guild, [m["discord_id"] for m in missed if m["discord_id"]]
            )
        messages = format_rollcall(roster["name"], missed, mode, names, now)
        await _send(channel, messages, _allowed_mentions(mode, missed))
        reported += len(missed)
    return {"rosters": len(rosters), "missed": reported}


async def run_rollcall(pool: asyncpg.Pool, bot: disnake.Client, now: datetime | None = None) -> dict:
    """Scheduled daily job: post each server's roll call. Servers never block each other."""
    now = now or datetime.now(timezone.utc)
    watched = await storage.get_watched_rosters(pool)
    if not watched:
        return {"guilds": 0}
    families = await guild_config.clans_by_guild(pool)

    by_guild: dict[int, list[dict]] = {}
    for roster in watched:
        by_guild.setdefault(roster["guild_id"], []).append(roster)

    clans: dict[str, dict | None] = {}  # each clan fetched once, shared between servers
    summary = {"guilds": 0, "missed": 0, "failed_guilds": 0}
    for guild_id, rosters in by_guild.items():
        family = families.get(guild_id)
        if not family:
            continue
        try:
            result = await _run_guild(pool, bot, guild_id, rosters, family, clans, now)
        except Exception:
            logger.exception("Roll call failed for guild=%s", guild_id)
            summary["failed_guilds"] += 1
            continue
        if result.get("error"):
            summary["failed_guilds"] += 1
        elif "skipped" not in result:
            summary["guilds"] += 1
            summary["missed"] += result["missed"]
    return summary
