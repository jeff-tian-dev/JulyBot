"""Ranked Battles tournament group lookup, histogram, and embed rendering.

Pool-free: no DB involvement, matching x_monitor/client.py and
youtube_feed/fetcher.py. Everything here is refresh-on-demand against the live
CoC API — there is no stored history, matching /group's design (see CLAUDE.md).

The official API does not document whether GET /leaguegroup/{tag}/{seasonId}'s
attackLogs/defenseLogs cover the whole ~100-player group or are scoped to just
the queried tag. detect_log_scope() resolves this at runtime by reconciling
log volume against the group's total recorded defenses vs. the queried
member's own total, and fails safe to the more conservative "player_scoped"
label rather than risk presenting partial data as if it were group-wide.
"""
from __future__ import annotations

import collections
import logging
from datetime import datetime, timezone

import disnake

from modules.account_linker.linker import _normalize_tag as normalize_tag
from modules.ranked_tracker import poller

logger = logging.getLogger(__name__)

GROUP_EMBED_COLOUR = 0x5865F2

# CoC API timestamps look like "20260825T142530.000Z".
_COC_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%S.%fZ"

# How close a candidate total has to be to the observed log volume to count as
# a match, as a fraction of the candidate. Not an exact API guarantee (some
# defenses may fall outside whatever window the API returns), so this is a
# tolerance band rather than an equality check.
_SCOPE_MATCH_TOLERANCE = 0.3

GROUP_WIDE = "group_wide"
PLAYER_SCOPED = "player_scoped"

# The matchmaker appears to try to keep defenses received even across the
# group. Once the pool of members at-or-below a given (below-majority) defense
# count shrinks under this many, that pool is a small target for the attacks
# still left in the week, so anyone still in it is increasingly likely to be
# picked next. Tune once real usage confirms the right cutoff.
LOW_DEFENSE_POOL_THRESHOLD = 40


class RankedGroupError(Exception):
    """A user-facing failure resolving or fetching a Ranked Battles group."""


def _defense_count(member: dict) -> int:
    return member.get("defenseWinCount", 0) + member.get("defenseLoseCount", 0)


async def resolve_current_group(coc_tag: str) -> tuple[str, str, str, int]:
    """Normalize a tag and resolve it to its current Ranked Battles group.

    Returns (player_name, normalized_tag, group_tag, season_id). Raises
    RankedGroupError with a user-facing message if the player can't be fetched
    or isn't currently in a ranked group.
    """
    tag = normalize_tag(coc_tag)
    player = await poller.get_player(tag)
    if player is None:
        raise RankedGroupError("Couldn't reach the CoC API right now — try again in a moment.")

    group_tag = player.get("currentLeagueGroupTag")
    season_id = player.get("currentLeagueSeasonId")
    if not group_tag or not season_id:
        name = player.get("name", tag)
        raise RankedGroupError(f"{name} isn't currently in a Ranked Battles group this week.")

    return player.get("name", tag), tag, group_tag, season_id


async def fetch_group(group_tag: str, season_id: int, player_tag: str) -> dict:
    """Fetch a tournament group, raising RankedGroupError on failure.

    player_tag is required by the CoC API as a query parameter on this
    endpoint (confirmed live: it 400s "Required parameter 'playerTag' missing"
    without it) — pass the tag whose group membership is being looked up.

    Surfaces the real CoC API status/body in the error message — this
    endpoint's shape was reverse-engineered from library source and never
    confirmed against a live response, so a generic failure message would
    hide exactly the detail needed to diagnose a schema/path mismatch.
    """
    try:
        return await poller.get_league_group(group_tag, season_id, player_tag)
    except poller.LeagueGroupFetchError as exc:
        logger.warning(
            "get_league_group failed for %s/%s: status=%s body=%s",
            group_tag,
            season_id,
            exc.status,
            exc.body[:500],
        )
        raise RankedGroupError(
            f"Couldn't load that Ranked Battles group right now "
            f"(CoC API returned HTTP {exc.status}: {exc.body[:300]})."
        ) from exc


def compute_defense_histogram(members: list[dict]) -> list[tuple[int, int]]:
    """Bucket members by defenses received this week, sorted highest-first.

    A bucket for 0 defenses is included if any member has exactly 0 — hiding it
    would make the buckets fail to sum to the group size, silently understating
    the total.
    """
    counts = collections.Counter(_defense_count(m) for m in members)
    return sorted(counts.items(), key=lambda kv: kv[0], reverse=True)


def is_likely_to_be_hit_next(members: list[dict], queried_tag: str) -> bool:
    """Flag whether the queried player looks likely to be attacked next.

    The matchmaker seems to keep defenses received roughly even across the
    group, so members sitting below the majority (mode) defense count are
    preferential targets. This flags the queried player specifically (not
    the whole group) when both hold:
      - their own defense count is below the group's mode, and
      - the pool of members at or below that count is small (< threshold) —
        a small pool means the remaining attacks this week are more likely
        to land on someone still in it, including the queried player.
    Returns False if the queried player isn't found in members, or if there's
    no meaningful mode to compare against (e.g. an empty group).
    """
    own_count = next(
        (_defense_count(m) for m in members if m.get("playerTag") == queried_tag), None
    )
    if own_count is None or not members:
        return False

    histogram = compute_defense_histogram(members)
    if not histogram:
        return False
    mode_count = max(histogram, key=lambda kv: kv[1])[0]

    if own_count >= mode_count:
        return False

    pool_size = sum(count for defenses, count in histogram if defenses <= own_count)
    return pool_size < LOW_DEFENSE_POOL_THRESHOLD


def detect_log_scope(defense_logs: list[dict], members: list[dict], queried_tag: str) -> str:
    """Determine whether defense_logs covers the whole group or just one player.

    Tag identity alone can't distinguish the two cases — both would show many
    distinct attacker tags. Instead this reconciles volume: the observed log
    count is compared against the group's total recorded defenses and against
    just the queried member's own defense count. Ambiguous cases fail safe to
    PLAYER_SCOPED (the more conservative label) with the raw numbers logged so
    real production data can refine this heuristic.
    """
    observed = len(defense_logs)
    group_total = sum(_defense_count(m) for m in members)
    own_total = next((_defense_count(m) for m in members if m.get("playerTag") == queried_tag), 0)

    def _close(candidate: int) -> bool:
        if candidate <= 0:
            return observed == 0
        return abs(observed - candidate) <= _SCOPE_MATCH_TOLERANCE * candidate

    group_match = _close(group_total)
    player_match = _close(own_total)

    if group_match and not player_match:
        return GROUP_WIDE
    if player_match and not group_match:
        return PLAYER_SCOPED

    logger.warning(
        "Ambiguous ranked defense-log scope: observed=%d group_total=%d own_total=%d "
        "(group_match=%s player_match=%s) — falling back to player_scoped",
        observed,
        group_total,
        own_total,
        group_match,
        player_match,
    )
    return PLAYER_SCOPED


def _parse_coc_timestamp(value: str) -> datetime:
    return datetime.strptime(value, _COC_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)


def format_last_defenses(
    defense_logs: list[dict], scope: str, player_name: str, limit: int = 10
) -> tuple[str, str]:
    """Return (section_title, body_text) for the last N defenses.

    In the group_wide case there is no per-entry defender field in the
    confirmed API shape, so lines name only the attacker. In the
    player_scoped case the title itself says so, surfacing the limitation to
    the reader rather than silently showing partial data as if it were
    group-wide.
    """
    ordered = sorted(defense_logs, key=lambda log: log.get("creationTime", ""), reverse=True)
    recent = ordered[:limit]

    if scope == GROUP_WIDE:
        title = f"Last {len(recent)} Defenses"
    else:
        title = f"Last {len(recent)} Defenses (for {player_name} only — group-wide data unavailable)"

    if not recent:
        return title, "No defenses recorded yet this week."

    lines = []
    for log in recent:
        attacker = log.get("opponentName", "Unknown")
        stars = log.get("stars", 0)
        pct = log.get("destructionPercentage", 0)
        trophies = log.get("trophies", 0)
        sign = "+" if trophies > 0 else ""
        try:
            when = _parse_coc_timestamp(log.get("creationTime", ""))
            timestamp = f"<t:{int(when.timestamp())}:R>"
        except ValueError:
            timestamp = ""
        line = f"{attacker} attacked — ⭐{stars} {pct}% ({sign}{trophies} 🏆)"
        if timestamp:
            line += f" — {timestamp}"
        lines.append(line)

    return title, "\n".join(lines)


def build_group_embed(
    player_name: str,
    queried_tag: str,
    group_tag: str,
    season_id: int,
    members: list[dict],
    defense_logs: list[dict],
) -> disnake.Embed:
    """Assemble the /group dashboard embed from a fetched league group."""
    histogram = compute_defense_histogram(members)
    histogram_lines = [
        f"{count} player{'s' if count != 1 else ''} have received {defenses} "
        f"defense{'s' if defenses != 1 else ''}"
        for defenses, count in histogram
    ]
    histogram_body = "\n".join(histogram_lines) if histogram_lines else "No data available."
    histogram_body += f"\n\n{len(members)} members total"
    if is_likely_to_be_hit_next(members, queried_tag):
        histogram_body += f"\n\n⚠️ {player_name} is likely to be hit next."

    scope = detect_log_scope(defense_logs, members, queried_tag)
    defenses_title, defenses_body = format_last_defenses(defense_logs, scope, player_name)

    embed = disnake.Embed(
        title=f"Ranked Battles Group — {player_name}",
        colour=GROUP_EMBED_COLOUR,
    )
    embed.add_field(name="Defenses Received This Week", value=histogram_body, inline=False)
    embed.add_field(name=defenses_title, value=defenses_body, inline=False)
    embed.set_footer(text=f"Season {season_id} — {len(members)} members")
    return embed


async def build_group_dashboard(coc_tag: str) -> disnake.Embed:
    """The full /group pipeline: resolve -> fetch -> render.

    Both the initial command and the refresh button call this single function
    so first-post and refreshed views can never drift apart.
    """
    player_name, tag, group_tag, season_id = await resolve_current_group(coc_tag)
    group = await fetch_group(group_tag, season_id, tag)
    members = group.get("members", [])
    defense_logs = group.get("defenseLogs", [])
    return build_group_embed(player_name, tag, group_tag, season_id, members, defense_logs)
