"""Watchdog poller: alert when watched-roster members leave/rejoin their server's family.

Each server sets up its own "family" — one or more clans (guild_clans, added with
/setup clan add) — and its own alert channel (/setup watchdog). A watched tag is
"in" if it's in ANY of its server's family clans, "out" if in none, so members
hopping between family clans never trigger an alert. Membership state
(clan_membership) is kept per server: the same player can be in one server's
family and out of another's.

Transitions (in->out, out->in) update that server's state and post plain-text
alerts to that server's channel, naming the clan the member moved from and to.
First sight of a tag is seeded silently so a restart never spams alerts. Each
clan is fetched once per poll and shared by every server that uses it; if one of
a server's clans can't be fetched, only that server's poll is skipped, to avoid
false "left" alerts there.
"""
from __future__ import annotations

import asyncio
import logging

import asyncpg
import disnake

from config.settings import settings
from modules.guild_config import modes
from modules.guild_config import storage as guild_config
from modules.legend_tracker.poller import get_clan, get_player
from modules.roster import render, storage

logger = logging.getLogger(__name__)

_DISCORD_OP_TIMEOUT_SECONDS = 30

# CoC clan fetches time out now and then (and the odd 525). One failed clan
# skips that server's poll, so retry it before giving up on the cycle.
CLAN_FETCH_ATTEMPTS = 3
CLAN_FETCH_RETRY_DELAY_SECONDS = 5

# CoC clan names are frequently Arabic/RTL. In a plain-text alert next to " → "
# and Latin text the Unicode bidi algorithm reorders them, so each clan name is
# wrapped in a LEFT-TO-RIGHT ISOLATE / POP DIRECTIONAL ISOLATE pair.
_LRI = "⁦"
_PDI = "⁩"


def _isolate(text: str) -> str:
    return f"{_LRI}{text}{_PDI}"


async def _fetch_clan(clan_tag: str) -> dict | None:
    """get_clan with retries; None only if every attempt failed."""
    for attempt in range(1, CLAN_FETCH_ATTEMPTS + 1):
        clan = await get_clan(clan_tag)
        if clan is not None or attempt == CLAN_FETCH_ATTEMPTS:
            return clan
        logger.info(
            "Watchdog: fetch failed for %s (attempt %d/%d); retrying",
            clan_tag, attempt, CLAN_FETCH_ATTEMPTS,
        )
        await asyncio.sleep(CLAN_FETCH_RETRY_DELAY_SECONDS)
    return None


def _format_move(from_clan: str | None, to_clan: str | None) -> str:
    """A ' — <from> → <to>' clan-movement fragment. Empty when neither is known."""
    if not from_clan and not to_clan:
        return ""
    left = _isolate(from_clan or "No clan")
    right = _isolate(to_clan or "No clan")
    return f" — {left} → {right}"


def _format_alert(alert: dict) -> str:
    name = alert["name"] or alert["tag"]
    move = _format_move(alert.get("from_clan"), alert.get("to_clan"))
    if alert["kind"] == "left":
        return f"🔴 **{name}** (`{alert['tag']}`) left the family{move}"
    duration = storage.format_duration(alert["seconds"])
    if duration:
        return (
            f"🟢 **{name}** (`{alert['tag']}`) rejoined the family{move} — "
            f"was out for **{duration}**"
        )
    return f"🟢 **{name}** (`{alert['tag']}`) joined the family{move}"


async def _destination_clan(coc_tag: str) -> str | None:
    """The clan a just-left member is now in (None if clanless or the API fails)."""
    player = await get_player(coc_tag)
    if player is None:
        return None
    return (player.get("clan") or {}).get("name")


async def _watch_channel(bot: disnake.Client, pool: asyncpg.Pool, guild_id: int):
    """Where the server's watchdog posts right now (its channel, the test channel or None).

    Follows the watchdog's /toggle mode; see modules/guild_config/modes.py.
    """
    _mode, channel = await modes.feature_channel(bot, pool, guild_id, "watchdog")
    return channel


async def _post_alerts(bot: disnake.Client, pool: asyncpg.Pool, guild_id: int, alerts: list[dict]) -> None:
    channel = await _watch_channel(bot, pool, guild_id)
    if channel is None:
        return
    for alert in alerts:
        try:
            await asyncio.wait_for(
                channel.send(
                    _format_alert(alert), allowed_mentions=disnake.AllowedMentions.none()
                ),
                timeout=_DISCORD_OP_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning("Watchdog: timed out sending alert for %s", alert["tag"])
        except disnake.HTTPException:
            logger.exception("Watchdog: failed to send alert for %s", alert["tag"])


async def build_daily_rows(
    pool: asyncpg.Pool, guild_id: int, roster_id: int, family: set[str]
) -> list[dict]:
    """Trophy-sorted leaderboard rows for a roster: trophies, IGN, in-family, out-time."""
    tags = await storage.get_roster_tags(pool, roster_id)
    state = await storage.get_daily_board_state(pool, guild_id, tags)

    rows: list[dict] = []
    for tag in tags:
        s = state.get(tag, {})
        player = await get_player(tag)
        if player is not None:
            trophies = player.get("trophies", 0)
            name = player.get("name") or s.get("coc_name") or tag
            in_family = ((player.get("clan") or {}).get("tag") in family)
        else:
            # API miss — fall back to the last-known state from clan_membership.
            trophies = 0
            name = s.get("coc_name") or tag
            in_family = bool(s.get("in_clan"))
        rows.append(
            {
                "trophies": trophies,
                "ign": name,
                "in_family": in_family,
                "daily_seconds": s.get("daily_absent_seconds", 0),
            }
        )
    rows.sort(key=lambda r: r["trophies"], reverse=True)
    return rows


async def _post_daily_board(
    pool: asyncpg.Pool, channel, guild_id: int, roster: dict, family: set[str]
) -> None:
    """Post one roster's daily leaderboard (trophy-sorted)."""
    rows = await build_daily_rows(pool, guild_id, roster["id"], family)
    embeds = render.leaderboard_embeds(f"{roster['name']} 👁", rows)
    # Each day's report stays in the channel as a running history — no deletion.
    # A big roster spans several messages (Discord caps a message's embeds at
    # 6000 chars total); the first one is recorded as the board's message.
    first = None
    for embed in embeds:
        message = await channel.send(embed=embed)
        first = first or message
    await storage.set_watch_message_id(pool, roster["id"], first.id)


async def _post_guild_boards(
    pool: asyncpg.Pool, bot: disnake.Client, guild_id: int, rosters: list[dict], family: set[str]
) -> dict:
    """Post the daily boards for one server's watched rosters."""
    mode, channel = await modes.feature_channel(bot, pool, guild_id, "watchdog")
    if mode == modes.OFF:
        # Off means no posts, not no tracking: the day still ends, so the caller
        # resets the counters instead of letting them pile up until it's back on.
        return {"off": True, "rosters": 0}
    if channel is None:
        # Can't reach the channel to post the board *or* a failure notice.
        return {"error": "no channel", "failed": len(rosters)}

    failed: list[str] = []
    for roster in rosters:
        try:
            await _post_daily_board(pool, channel, guild_id, roster, family)
        except Exception:
            logger.exception("Daily board failed for %s (guild=%s)", roster["name"], guild_id)
            failed.append(roster["name"])

    if failed:
        names = ", ".join(_isolate(name) for name in failed)
        try:
            await channel.send(
                f"⚠️ Daily leaderboard couldn't be posted for {names} "
                "(Clash of Clans API error). Daily out-of-family counters were kept "
                "and will roll into the next successful run.",
                allowed_mentions=disnake.AllowedMentions.none(),
            )
        except disnake.HTTPException:
            logger.exception("Failed to post daily-board failure notice")

    return {"rosters": len(rosters), "failed": len(failed)}


async def run_daily_watchlist(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Scheduled daily job: post each server's boards, then reset its daily counters.

    A server's reset is skipped whenever one of its boards failed to post (or its
    channel was unreachable), so a transient API error never silently discards a
    day's accumulated out-of-family time — it rolls into the next successful run.
    Servers are independent: one server's failure never holds back another's reset.
    """
    watched_rosters = await storage.get_watched_rosters(pool)
    if not watched_rosters:
        return {"guilds": 0}
    families = await guild_config.clans_by_guild(pool)

    by_guild: dict[int, list[dict]] = {}
    for roster in watched_rosters:
        by_guild.setdefault(roster["guild_id"], []).append(roster)

    summary = {"guilds": 0, "rosters": 0, "failed_guilds": 0}
    for guild_id, rosters in by_guild.items():
        family = set(families.get(guild_id, []))
        if not family:
            continue
        summary["guilds"] += 1
        try:
            result = await _post_guild_boards(pool, bot, guild_id, rosters, family)
        except Exception:
            logger.exception("Daily watchlist failed for guild=%s", guild_id)
            result = {"error": "exception"}
        summary["rosters"] += result.get("rosters", 0)
        if result.get("failed") or result.get("error"):
            summary["failed_guilds"] += 1
            logger.warning(
                "Daily watchlist incomplete for guild=%s (%s); keeping daily-absent counters",
                guild_id,
                result,
            )
            continue
        await storage.reset_daily_absent(pool, guild_id)
    return summary


async def _poll_guild(
    pool: asyncpg.Pool, guild_id: int, tags: list[str], family: list[str], clans: dict[str, dict | None]
) -> list[dict] | None:
    """Diff one server's watched tags against its family. None if a clan fetch failed.

    `clans` is the poll-wide cache of fetched clans, shared between servers so a
    clan used by several servers is only fetched once per poll.
    """
    # Union every family clan's members. If any clan can't be fetched, skip this
    # server's poll — otherwise its members would look like they left the family.
    # Track each member's player name and the display name of the family clan
    # they're currently in (for the "from → to" movement in alerts).
    player_names: dict[str, str | None] = {}
    player_trophies: dict[str, int | None] = {}
    family_clan_of: dict[str, str | None] = {}
    for clan_tag in family:
        if clan_tag not in clans:
            clans[clan_tag] = await _fetch_clan(clan_tag)
        clan = clans[clan_tag]
        if clan is None:
            logger.warning(
                "Watchdog: fetch failed for %s; skipping guild=%s to avoid false alerts",
                clan_tag,
                guild_id,
            )
            return None
        clan_display = clan.get("name")
        for m in clan.get("memberList", []):
            player_names.setdefault(m["tag"], m.get("name"))
            player_trophies.setdefault(m["tag"], m.get("trophies"))
            family_clan_of.setdefault(m["tag"], clan_display)
    clan_set = set(player_names.keys())

    # Hand the just-fetched member data (incl. trophies) to the player cache so
    # /roster view doesn't re-call the API for anyone currently in the family.
    await storage.warm_player_cache(
        pool,
        [(tag, player_names[tag], family_clan_of[tag], player_trophies[tag]) for tag in player_names],
    )

    membership = await storage.get_membership_map(pool, guild_id, tags)
    alerts: list[dict] = []
    for tag in tags:
        now_in = tag in clan_set
        prev = membership.get(tag)
        name_in_clan = player_names.get(tag)
        family_clan = family_clan_of.get(tag)

        if prev is None:
            await storage.seed_membership(pool, guild_id, tag, name_in_clan, now_in, family_clan)
            continue

        if prev["in_clan"] and not now_in:
            to_clan = await _destination_clan(tag)
            await storage.mark_left(pool, guild_id, tag, prev["coc_name"], to_clan)
            alerts.append(
                {
                    "kind": "left",
                    "tag": tag,
                    "name": prev["coc_name"],
                    "from_clan": prev.get("clan_name"),
                    "to_clan": to_clan,
                    "seconds": None,
                }
            )
        elif not prev["in_clan"] and now_in:
            seconds = await storage.mark_joined(pool, guild_id, tag, name_in_clan, family_clan)
            alerts.append(
                {
                    "kind": "join",
                    "tag": tag,
                    "name": name_in_clan or prev["coc_name"],
                    "from_clan": prev.get("clan_name"),
                    "to_clan": family_clan,
                    "seconds": seconds,
                }
            )
        elif now_in and (
            (name_in_clan and name_in_clan != prev["coc_name"])
            or (family_clan and family_clan != prev.get("clan_name"))
        ):
            # Still in the family but the name or the specific family clan changed
            # (hopped clans) — refresh the cached values silently, no alert.
            await storage.touch_name(pool, guild_id, tag, name_in_clan, family_clan)

    # Accumulate today's out-of-family time for anyone currently outside it.
    out_tags = [tag for tag in tags if tag not in clan_set]
    if out_tags:
        await storage.add_daily_absent(
            pool, guild_id, out_tags, settings.CLAN_WATCH_POLL_INTERVAL_MINUTES * 60
        )
    return alerts


async def poll_watchdog(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Compare every server's watched-roster members against that server's family."""
    watched = await storage.get_watched_tags(pool)
    if not watched:
        return {"watched_tags": 0}
    families = await guild_config.clans_by_guild(pool)

    # A tag can appear in several of a server's watched rosters; process it once.
    tags_by_guild: dict[int, list[str]] = {}
    for row in watched:
        guild_tags = tags_by_guild.setdefault(row["guild_id"], [])
        if row["coc_tag"] not in guild_tags:
            guild_tags.append(row["coc_tag"])

    clans: dict[str, dict | None] = {}
    summary = {"guilds": 0, "watched_tags": 0, "alerts": 0, "failed_guilds": 0}
    for guild_id, tags in tags_by_guild.items():
        family = families.get(guild_id)
        if not family:
            continue
        summary["guilds"] += 1
        summary["watched_tags"] += len(tags)
        try:
            alerts = await _poll_guild(pool, guild_id, tags, family, clans)
        except Exception:
            logger.exception("Watchdog failed for guild=%s", guild_id)
            summary["failed_guilds"] += 1
            continue
        if alerts is None:
            summary["failed_guilds"] += 1
            continue
        if alerts:
            summary["alerts"] += len(alerts)
            await _post_alerts(bot, pool, guild_id, alerts)
    return summary
