"""Clan-watch poller: alert when watched-roster members leave/rejoin the family.

The "family" is one or more clans (COC_FAMILY_CLAN_TAGS, plus COC_CLAN_TAG for
back-compat). Each family clan is fetched and its members unioned: a tag is
"in" if it's in ANY family clan, "out" if in none — so members hopping between
family clans never trigger an alert. Transitions (in->out, out->in) update the
clan_membership state and post plain-text alerts to the hardcoded channel, naming
the clan the member moved from and to. First sight of a tag is seeded silently so
a restart never spams alerts. If any family clan fetch fails, the whole poll is
skipped to avoid false "left" alerts.
"""
from __future__ import annotations

import logging

import asyncpg
import disnake

from config.settings import settings
from modules.legend_tracker.poller import get_clan, get_player
from modules.roster import render, storage

logger = logging.getLogger(__name__)

# CoC clan names are frequently Arabic/RTL. In a plain-text alert next to " → "
# and Latin text the Unicode bidi algorithm reorders them, so each clan name is
# wrapped in a LEFT-TO-RIGHT ISOLATE / POP DIRECTIONAL ISOLATE pair.
_LRI = "⁦"
_PDI = "⁩"


def _isolate(text: str) -> str:
    return f"{_LRI}{text}{_PDI}"


def family_tags() -> list[str]:
    """Normalized, de-duplicated list of family clan tags (COC_CLAN_TAG + family)."""
    raw = list(settings.COC_FAMILY_CLAN_TAGS)
    if settings.COC_CLAN_TAG:
        raw.append(settings.COC_CLAN_TAG)
    tags: list[str] = []
    for tag in raw:
        normalized = storage.normalize_tag(tag)
        if normalized not in tags:
            tags.append(normalized)
    return tags


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


async def _watch_channel(bot: disnake.Client):
    channel = bot.get_channel(settings.CLAN_WATCH_CHANNEL_ID)
    if channel is None:
        try:
            channel = await bot.fetch_channel(settings.CLAN_WATCH_CHANNEL_ID)
        except disnake.HTTPException:
            logger.warning("Clan watch: channel %s not found", settings.CLAN_WATCH_CHANNEL_ID)
            return None
    return channel


async def _post_alerts(bot: disnake.Client, alerts: list[dict]) -> None:
    channel = await _watch_channel(bot)
    if channel is None:
        return
    for alert in alerts:
        try:
            await channel.send(_format_alert(alert), allowed_mentions=disnake.AllowedMentions.none())
        except disnake.HTTPException:
            logger.exception("Clan watch: failed to send alert for %s", alert["tag"])


async def build_daily_rows(pool: asyncpg.Pool, roster_id: int, family: set[str]) -> list[dict]:
    """Trophy-sorted leaderboard rows for a roster: trophies, IGN, in-family, out-time.

    Shared by the daily board and `/roster view` on a watched roster.
    """
    tags = await storage.get_roster_tags(pool, roster_id)
    state = await storage.get_daily_board_state(pool, tags)

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


async def _post_daily_board(pool: asyncpg.Pool, channel, roster: dict, family: set[str]) -> None:
    """Post one roster's daily leaderboard (trophy-sorted) and delete the previous."""
    rows = await build_daily_rows(pool, roster["id"], family)
    embed = render.leaderboard_embed(f"{roster['name']} 👁", rows)
    # Each day's report stays in the channel as a running history — no deletion.
    message = await channel.send(embed=embed)
    await storage.set_watch_message_id(pool, roster["id"], message.id)


async def post_daily_watchlist(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Post the daily leaderboard for every watched roster (no reset — see run_*)."""
    family = set(family_tags())
    if not family:
        return {"skipped": "no clan tags"}
    watched_rosters = await storage.get_watched_rosters(pool)
    if not watched_rosters:
        return {"watched_rosters": 0}
    channel = await _watch_channel(bot)
    if channel is None:
        return {"error": "no channel"}

    for roster in watched_rosters:
        try:
            await _post_daily_board(pool, channel, roster, family)
        except Exception:
            logger.exception("Daily board failed for %s", roster["name"])
    return {"rosters": len(watched_rosters)}


async def run_daily_watchlist(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Scheduled 1am job: post the daily boards, then reset the daily counters."""
    summary = await post_daily_watchlist(pool, bot)
    await storage.reset_daily_absent(pool)
    return summary


async def poll_clan_watch(pool: asyncpg.Pool, bot: disnake.Client) -> dict:
    """Compare watched-roster members against the clan family; alert on changes."""
    family = family_tags()
    if not family:
        return {"skipped": "no clan tags"}

    watched = await storage.get_watched_tags(pool)
    if not watched:
        return {"watched_tags": 0}

    # A tag can appear in several watched rosters; process it once.
    tags = list(dict.fromkeys(row["coc_tag"] for row in watched))

    # Union every family clan's members. If any clan can't be fetched, skip the
    # whole poll — otherwise its members would look like they left the family.
    # Track each member's player name and the display name of the family clan
    # they're currently in (for the "from → to" movement in alerts).
    player_names: dict[str, str | None] = {}
    player_trophies: dict[str, int | None] = {}
    family_clan_of: dict[str, str | None] = {}
    for clan_tag in family:
        clan = await get_clan(clan_tag)
        if clan is None:
            logger.warning(
                "Clan watch: fetch failed for %s; skipping poll to avoid false alerts", clan_tag
            )
            return {"error": "clan fetch failed"}
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

    membership = await storage.get_membership_map(pool, tags)
    alerts: list[dict] = []
    for tag in tags:
        now_in = tag in clan_set
        prev = membership.get(tag)
        name_in_clan = player_names.get(tag)
        family_clan = family_clan_of.get(tag)

        if prev is None:
            await storage.seed_membership(pool, tag, name_in_clan, now_in, family_clan)
            continue

        if prev["in_clan"] and not now_in:
            to_clan = await _destination_clan(tag)
            await storage.mark_left(pool, tag, prev["coc_name"], to_clan)
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
            seconds = await storage.mark_joined(pool, tag, name_in_clan, family_clan)
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
            await storage.touch_name(pool, tag, name_in_clan, family_clan)

    # Accumulate today's out-of-family time for anyone currently outside it.
    out_tags = [tag for tag in tags if tag not in clan_set]
    if out_tags:
        await storage.add_daily_absent(pool, out_tags, settings.CLAN_WATCH_POLL_INTERVAL_MINUTES * 60)

    if alerts:
        await _post_alerts(bot, alerts)

    return {"watched_tags": len(tags), "family_clans": len(family), "alerts": len(alerts)}
