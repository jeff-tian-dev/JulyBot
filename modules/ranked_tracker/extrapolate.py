"""Extrapolate each group member's Ranked Battles pace to a full 30 attacks.

Unlike group.py (one /leaguegroup call per invocation), this fetches every
eligible member's own attack/defense trophy history individually — the
CoC API only exposes a `trophies` delta per battle on the /leaguegroup
response scoped to whichever tag is passed as `playerTag`, and there is no
confirmed way to get all ~100 members' logs in a single call (see the
detect_log_scope discussion in group.py). So this makes one /leaguegroup
call per eligible member, at bounded concurrency, and averages each
member's own attack/defense trophy deltas.

UNVERIFIED ASSUMPTION, logged so the first real run can confirm or refute
it: passing a different `playerTag` actually scopes attackLogs/defenseLogs
to that member. If it turns out the response is identical regardless of
playerTag, every member's "extrapolation" would silently collapse to the
same numbers — _log_sample_for_verification() exists specifically to make
that failure visible in the logs on first use.
"""
from __future__ import annotations

import asyncio
import logging

import disnake

from modules.ranked_tracker import poller
from modules.ranked_tracker.group import GROUP_EMBED_COLOUR, RankedGroupError, resolve_current_group

logger = logging.getLogger(__name__)

# A member needs more than this many attacks AND more than this many
# defenses this week to be included — too little data to extrapolate
# reliably otherwise.
MIN_BATTLES_FOR_EXTRAPOLATION = 10

# Every member gets the same weekly attack budget.
TOTAL_WEEKLY_ATTACKS = 30

# How many /leaguegroup calls to run at once. Unbounded would fire ~100
# requests simultaneously at the CoC API; bounding avoids tripping its rate
# limiter, matching the SCAN_CONCURRENCY pattern in modules/moderation/purge.py.
EXTRAPOLATE_CONCURRENCY = 10

TOP_N = 3


def _attack_count(member: dict) -> int:
    return member.get("attackWinCount", 0) + member.get("attackLoseCount", 0)


def _defense_count(member: dict) -> int:
    return member.get("defenseWinCount", 0) + member.get("defenseLoseCount", 0)


def eligible_members(members: list[dict], min_battles: int = MIN_BATTLES_FOR_EXTRAPOLATION) -> list[dict]:
    """Members with more than min_battles attacks AND more than min_battles defenses."""
    return [
        m for m in members if _attack_count(m) > min_battles and _defense_count(m) > min_battles
    ]


def _average_trophies(logs: list[dict]) -> float:
    if not logs:
        return 0.0
    return sum(log.get("trophies", 0) for log in logs) / len(logs)


def _log_sample_for_verification(tag: str, attack_logs: list[dict], defense_logs: list[dict]) -> None:
    """Log a small sample so the first real run can confirm playerTag actually
    scopes the response to this member, rather than returning identical data
    regardless of which tag was queried."""
    sample_attack = attack_logs[0].get("trophies") if attack_logs else None
    sample_defense = defense_logs[0].get("trophies") if defense_logs else None
    logger.info(
        "groupextrapolate sample for %s: attackLogs=%d defenseLogs=%d "
        "first_attack_trophies=%r first_defense_trophies=%r",
        tag,
        len(attack_logs),
        len(defense_logs),
        sample_attack,
        sample_defense,
    )


async def _extrapolate_member(
    group_tag: str, season_id: int, member: dict, semaphore: asyncio.Semaphore
) -> dict | None:
    """Fetch one member's own logs and compute their extrapolated total.

    Returns None if the fetch fails for this member — a single member's
    CoC API failure shouldn't abort the whole extrapolation.
    """
    tag = member["playerTag"]
    async with semaphore:
        try:
            data = await poller.get_league_group(group_tag, season_id, tag)
        except poller.LeagueGroupFetchError as exc:
            logger.warning(
                "groupextrapolate: failed to fetch logs for %s: HTTP %s", tag, exc.status
            )
            return None

    attack_logs = data.get("attackLogs", [])
    defense_logs = data.get("defenseLogs", [])
    _log_sample_for_verification(tag, attack_logs, defense_logs)

    avg_attack = _average_trophies(attack_logs)
    avg_defense = _average_trophies(defense_logs)
    extrapolated = (avg_attack + avg_defense) * TOTAL_WEEKLY_ATTACKS

    return {
        "tag": tag,
        "name": member.get("playerName", tag),
        "avg_attack_trophies": avg_attack,
        "avg_defense_trophies": avg_defense,
        "extrapolated_total": extrapolated,
    }


async def extrapolate_group(
    coc_tag: str, min_battles: int = MIN_BATTLES_FOR_EXTRAPOLATION
) -> tuple[str, list[dict], int]:
    """Fetch the queried player's group, then extrapolate every eligible
    member's 30-attack pace, returning (player_name, top TOP_N by
    extrapolated total, count of eligible members considered).

    min_battles sets the eligibility bar (a member needs more than this many
    attacks AND more than this many defenses this week) — lower it early in
    the tournament week when few members have crossed the default of 10.

    Raises RankedGroupError with a user-facing message on failure to resolve
    the group at all. Individual member fetch failures are skipped, not
    fatal, and logged.
    """
    player_name, tag, group_tag, season_id = await resolve_current_group(coc_tag)

    try:
        group_data = await poller.get_league_group(group_tag, season_id, tag)
    except poller.LeagueGroupFetchError as exc:
        raise RankedGroupError(
            f"Couldn't load that Ranked Battles group right now "
            f"(CoC API returned HTTP {exc.status}: {exc.body[:300]})."
        ) from exc

    members = group_data.get("members", [])
    eligible = eligible_members(members, min_battles)
    if not eligible:
        raise RankedGroupError(
            f"No members in {player_name}'s group have more than "
            f"{min_battles} attacks and defenses yet this week."
        )

    semaphore = asyncio.Semaphore(EXTRAPOLATE_CONCURRENCY)
    results = await asyncio.gather(
        *(_extrapolate_member(group_tag, season_id, member, semaphore) for member in eligible)
    )
    scored = [r for r in results if r is not None]
    scored.sort(key=lambda r: r["extrapolated_total"], reverse=True)
    return player_name, scored[:TOP_N], len(eligible)


def build_extrapolate_embed(
    player_name: str,
    top: list[dict],
    eligible_count: int,
    min_battles: int = MIN_BATTLES_FOR_EXTRAPOLATION,
) -> disnake.Embed:
    """Render the extrapolated top-N as an embed."""
    embed = disnake.Embed(
        title=f"Extrapolated Ranked Battles Pace — {player_name}'s Group",
        description=(
            f"Projected to {TOTAL_WEEKLY_ATTACKS} attacks, using each member's own "
            f"average trophies per attack/defense so far this week."
        ),
        colour=GROUP_EMBED_COLOUR,
    )
    embed.add_field(
        name="Eligible Members",
        value=f"{eligible_count} member{'s' if eligible_count != 1 else ''} eligible for extrapolation",
        inline=False,
    )
    if not top:
        embed.add_field(name="Top 3", value="No eligible members to rank.", inline=False)
        return embed

    lines = []
    for rank, entry in enumerate(top, start=1):
        lines.append(
            f"**{rank}. {entry['name']}** — {entry['extrapolated_total']:.0f} 🏆 projected\n"
            f"　 avg {entry['avg_attack_trophies']:+.1f}/atk, "
            f"{entry['avg_defense_trophies']:+.1f}/def"
        )
    embed.add_field(name=f"Top {len(top)}", value="\n".join(lines), inline=False)
    embed.set_footer(
        text=f"Only members with more than {min_battles} attacks and defenses this week are eligible."
    )
    return embed


async def build_extrapolate_dashboard(
    coc_tag: str, min_battles: int = MIN_BATTLES_FOR_EXTRAPOLATION
) -> disnake.Embed:
    """The full /groupextrapolate pipeline: resolve -> extrapolate -> render."""
    player_name, top, eligible_count = await extrapolate_group(coc_tag, min_battles)
    return build_extrapolate_embed(player_name, top, eligible_count, min_battles)
