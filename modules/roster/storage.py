"""Roster storage: raw asyncpg CRUD plus the enriched /roster view.

A roster is a named group scoped to a guild. Each member is EITHER a Discord
user (added via /roster add) OR a raw CoC tag (added via /roster addtag). The
enriched view cross-references the `users` link table so a member added one way
still shows the other side when a link exists; anything unknown is left None for
the Discord layer to render as "Unlinked".
"""
from __future__ import annotations

import logging

import asyncpg

# Reuse the exact tag normalization the linker uses so roster tags match the
# format stored in `users.coc_tag` (leading '#', uppercase, O->0 fix).
from modules.account_linker.linker import _normalize_tag as normalize_tag
from modules.legend_tracker.poller import get_player

logger = logging.getLogger(__name__)

MAX_NAME_LEN = 100
# /roster view reuses cached CoC data (name + clan) this fresh instead of
# calling the API again. Keyed per tag, shared across every roster.
PLAYER_CACHE_TTL_SECONDS = 300


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise ValueError("Roster name can't be empty.")
    if len(cleaned) > MAX_NAME_LEN:
        raise ValueError(f"Roster name must be {MAX_NAME_LEN} characters or fewer.")
    return cleaned


async def get_roster(pool: asyncpg.Pool, guild_id: int, name: str) -> asyncpg.Record | None:
    """Look up a roster by guild + name (case-insensitive). None if not found."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, name, watched FROM rosters WHERE guild_id = $1 AND lower(name) = lower($2);",
            guild_id,
            name.strip(),
        )


def format_duration(seconds: int | float | None) -> str | None:
    """Human-friendly absence like '3d 4h', '5h 12m', '45m'. None if seconds is None."""
    if seconds is None:
        return None
    seconds = int(seconds)
    if seconds < 60:
        return "<1m"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


async def _require_roster(pool: asyncpg.Pool, guild_id: int, name: str) -> asyncpg.Record:
    roster = await get_roster(pool, guild_id, name)
    if roster is None:
        raise ValueError(f"No roster named **{name.strip()}**. Create it with `/roster create`.")
    return roster


# --- roster lifecycle -------------------------------------------------------

async def create_roster(pool: asyncpg.Pool, guild_id: int, name: str) -> dict:
    """Create an empty roster. Raises ValueError if the name is taken."""
    name = _clean_name(name)
    async with pool.acquire() as conn:
        try:
            row = await conn.fetchrow(
                "INSERT INTO rosters (guild_id, name) VALUES ($1, $2) RETURNING id, name;",
                guild_id,
                name,
            )
        except asyncpg.UniqueViolationError:
            raise ValueError(f"A roster named **{name}** already exists.")
    logger.info("Created roster %r in guild=%s", name, guild_id)
    return {"id": row["id"], "name": row["name"]}


async def delete_roster(pool: asyncpg.Pool, guild_id: int, name: str) -> bool:
    """Delete a roster and (via ON DELETE CASCADE) its members. True if removed."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM rosters WHERE guild_id = $1 AND lower(name) = lower($2);",
            guild_id,
            name.strip(),
        )
    deleted = result.endswith(" 1")
    if deleted:
        logger.info("Deleted roster %r in guild=%s", name.strip(), guild_id)
    return deleted


async def rename_roster(pool: asyncpg.Pool, guild_id: int, name: str, new_name: str) -> bool:
    """Rename a roster. Raises ValueError if it's missing or the new name is taken."""
    new_name = _clean_name(new_name)
    roster = await _require_roster(pool, guild_id, name)
    async with pool.acquire() as conn:
        try:
            await conn.execute("UPDATE rosters SET name = $1 WHERE id = $2;", new_name, roster["id"])
        except asyncpg.UniqueViolationError:
            raise ValueError(f"A roster named **{new_name}** already exists.")
    logger.info("Renamed roster %s -> %r in guild=%s", roster["id"], new_name, guild_id)
    return True


async def list_rosters(pool: asyncpg.Pool, guild_id: int) -> list[dict]:
    """Return all rosters in a guild with member counts + watched flag, by name."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.name, r.watched, COUNT(m.id) AS member_count
            FROM rosters r
            LEFT JOIN roster_members m ON m.roster_id = r.id
            WHERE r.guild_id = $1
            GROUP BY r.id, r.name, r.watched
            ORDER BY lower(r.name);
            """,
            guild_id,
        )
    return [
        {"id": r["id"], "name": r["name"], "watched": r["watched"], "member_count": r["member_count"]}
        for r in rows
    ]


async def set_watched(pool: asyncpg.Pool, guild_id: int, name: str, watched: bool) -> str:
    """Toggle clan-watch on a roster. Returns the stored name. Raises if missing."""
    roster = await _require_roster(pool, guild_id, name)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE rosters SET watched = $1 WHERE id = $2;", watched, roster["id"])
    logger.info("Set watched=%s on roster %s in guild=%s", watched, roster["id"], guild_id)
    return roster["name"]


async def get_watched_rosters(pool: asyncpg.Pool) -> list[dict]:
    """Every watched roster (id, guild_id, name, watch_message_id) across all guilds.

    Used by the clan-watch poller to refresh each one's live watchlist board.
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, guild_id, name, watch_message_id FROM rosters WHERE watched = TRUE;"
        )
    return [dict(r) for r in rows]


async def set_watch_message_id(pool: asyncpg.Pool, roster_id: int, message_id: int | None) -> None:
    """Remember the id of the latest watchlist board posted for a roster."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE rosters SET watch_message_id = $2 WHERE id = $1;", roster_id, message_id
        )


async def warm_player_cache(pool: asyncpg.Pool, entries) -> None:
    """Bulk-refresh the CoC player cache from already-fetched (tag, name, clan, trophies).

    The clan-watch poll fetches every family clan's member list; handing that data
    to the cache lets /roster view and the watchlist board skip per-player API
    calls for anyone in the family (their cache row is fresh).
    """
    entries = list(entries)
    if not entries:
        return
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO coc_player_cache (coc_tag, coc_name, clan_name, trophies, fetched_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (coc_tag) DO UPDATE
                SET coc_name = EXCLUDED.coc_name,
                    clan_name = EXCLUDED.clan_name,
                    trophies = EXCLUDED.trophies,
                    fetched_at = NOW();
            """,
            entries,
        )


async def add_daily_absent(pool: asyncpg.Pool, tags: list[str], seconds: int) -> None:
    """Add `seconds` of out-of-family time to each tag's running daily total."""
    tags = list(tags)
    if not tags:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE clan_membership SET daily_absent_seconds = daily_absent_seconds + $2 "
            "WHERE coc_tag = ANY($1::text[]);",
            tags,
            seconds,
        )


async def reset_daily_absent(pool: asyncpg.Pool) -> None:
    """Zero every tag's daily out-of-family counter (run right after the 1am board)."""
    async with pool.acquire() as conn:
        await conn.execute("UPDATE clan_membership SET daily_absent_seconds = 0;")


async def get_roster_tags(pool: asyncpg.Pool, roster_id: int) -> list[str]:
    """Resolved CoC tags for one roster (discord members via their oldest link)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT COALESCE(
                       m.coc_tag,
                       (SELECT coc_tag FROM users WHERE discord_id = m.discord_id
                        ORDER BY linked_at ASC LIMIT 1)
                   ) AS coc_tag
            FROM roster_members m
            WHERE m.roster_id = $1;
            """,
            roster_id,
        )
    tags: list[str] = []
    for r in rows:
        if r["coc_tag"] and r["coc_tag"] not in tags:
            tags.append(r["coc_tag"])
    return tags


async def get_daily_board_state(pool: asyncpg.Pool, tags: list[str]) -> dict[str, dict]:
    """Per-tag {coc_name, in_clan, daily_absent_seconds} for the daily board."""
    unique = list(dict.fromkeys(tags))
    if not unique:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT coc_tag, coc_name, in_clan, daily_absent_seconds "
            "FROM clan_membership WHERE coc_tag = ANY($1::text[]);",
            unique,
        )
    return {r["coc_tag"]: dict(r) for r in rows}


# --- membership -------------------------------------------------------------

async def add_member_by_discord(pool: asyncpg.Pool, guild_id: int, name: str, discord_id: int) -> bool:
    """Add a Discord user to a roster. True if added, False if already present."""
    roster = await _require_roster(pool, guild_id, name)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO roster_members (roster_id, discord_id) VALUES ($1, $2)
            ON CONFLICT (roster_id, discord_id) WHERE discord_id IS NOT NULL DO NOTHING;
            """,
            roster["id"],
            discord_id,
        )
    return result.endswith(" 1")


async def add_member_by_tag(pool: asyncpg.Pool, guild_id: int, name: str, coc_tag: str) -> bool:
    """Add a raw CoC tag to a roster. True if added, False if already present."""
    coc_tag = normalize_tag(coc_tag)
    roster = await _require_roster(pool, guild_id, name)
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            INSERT INTO roster_members (roster_id, coc_tag) VALUES ($1, $2)
            ON CONFLICT (roster_id, coc_tag) WHERE coc_tag IS NOT NULL DO NOTHING;
            """,
            roster["id"],
            coc_tag,
        )
    return result.endswith(" 1")


async def remove_member_by_discord(pool: asyncpg.Pool, guild_id: int, name: str, discord_id: int) -> bool:
    """Remove a Discord user from a roster. True if a row was removed."""
    roster = await _require_roster(pool, guild_id, name)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM roster_members WHERE roster_id = $1 AND discord_id = $2;",
            roster["id"],
            discord_id,
        )
    return result.endswith(" 1")


async def remove_member_by_tag(pool: asyncpg.Pool, guild_id: int, name: str, coc_tag: str) -> bool:
    """Remove a raw CoC tag from a roster. True if a row was removed."""
    coc_tag = normalize_tag(coc_tag)
    roster = await _require_roster(pool, guild_id, name)
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM roster_members WHERE roster_id = $1 AND coc_tag = $2;",
            roster["id"],
            coc_tag,
        )
    return result.endswith(" 1")


async def _move(pool: asyncpg.Pool, guild_id: int, from_name: str, to_name: str, column: str, value) -> str:
    """Shared move: delete from source roster, insert into destination.

    Returns 'moved', or 'not_in_source' if the member wasn't in the source.
    Raises ValueError if either roster is missing. `column` is a trusted literal
    ('discord_id' or 'coc_tag'), never user input.
    """
    src = await _require_roster(pool, guild_id, from_name)
    dst = await _require_roster(pool, guild_id, to_name)
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.execute(
                f"DELETE FROM roster_members WHERE roster_id = $1 AND {column} = $2;",
                src["id"],
                value,
            )
            if not deleted.endswith(" 1"):
                return "not_in_source"
            await conn.execute(
                f"""
                INSERT INTO roster_members (roster_id, {column}) VALUES ($1, $2)
                ON CONFLICT (roster_id, {column}) WHERE {column} IS NOT NULL DO NOTHING;
                """,
                dst["id"],
                value,
            )
    return "moved"


async def move_member_by_discord(
    pool: asyncpg.Pool, guild_id: int, from_name: str, to_name: str, discord_id: int
) -> str:
    """Move a Discord user between two rosters. See _move for return values."""
    return await _move(pool, guild_id, from_name, to_name, "discord_id", discord_id)


async def move_member_by_tag(
    pool: asyncpg.Pool, guild_id: int, from_name: str, to_name: str, coc_tag: str
) -> str:
    """Move a raw CoC tag between two rosters. See _move for return values."""
    return await _move(pool, guild_id, from_name, to_name, "coc_tag", normalize_tag(coc_tag))


# --- enriched view ----------------------------------------------------------

async def build_roster_view(pool: asyncpg.Pool, guild_id: int, name: str) -> dict:
    """Resolve every member to {discord_id, coc_tag, coc_name, clan_name, coc_api_ok}.

    Cross-references the `users` table so a member added by Discord id also shows
    their linked tag (and vice-versa). CoC name / clan come from the live API.
    Missing pieces stay None; the Discord layer renders those as "Unlinked".
    """
    roster = await _require_roster(pool, guild_id, name)

    async with pool.acquire() as conn:
        members = await conn.fetch(
            "SELECT discord_id, coc_tag FROM roster_members WHERE roster_id = $1 ORDER BY added_at ASC;",
            roster["id"],
        )
        resolved: list[dict] = []
        for m in members:
            discord_id = m["discord_id"]
            coc_tag = m["coc_tag"]
            if coc_tag is None and discord_id is not None:
                link = await conn.fetchrow(
                    "SELECT coc_tag FROM users WHERE discord_id = $1 ORDER BY linked_at ASC LIMIT 1;",
                    discord_id,
                )
                if link is not None:
                    coc_tag = link["coc_tag"]
            elif discord_id is None and coc_tag is not None:
                link = await conn.fetchrow(
                    "SELECT discord_id FROM users WHERE coc_tag = $1;", coc_tag
                )
                if link is not None:
                    discord_id = link["discord_id"]
            resolved.append({"discord_id": discord_id, "coc_tag": coc_tag})

    # Resolve CoC name/clan for every tag through the TTL cache (calls the API
    # only for stale/missing tags). Done after the DB connection is released.
    tags = [r["coc_tag"] for r in resolved if r["coc_tag"]]
    players = await _resolve_players(pool, tags)
    # Absence data only exists for watched rosters (the poller maintains it).
    watched = bool(roster.get("watched"))
    membership = await _membership_for_view(pool, tags) if watched else {}

    out: list[dict] = []
    for r in resolved:
        info = players.get(r["coc_tag"]) if r["coc_tag"] else None
        mem = membership.get(r["coc_tag"]) if r["coc_tag"] else None
        out.append(
            {
                "discord_id": r["discord_id"],
                "coc_tag": r["coc_tag"],
                "coc_name": info["coc_name"] if info else None,
                "clan_name": info["clan_name"] if info else None,
                "trophies": info["trophies"] if info else None,
                "coc_api_ok": info["coc_api_ok"] if info else False,
                "in_main_clan": mem["in_clan"] if mem else None,
                "out_seconds": mem["out_seconds"] if mem else None,
                "total_seconds": mem["total_seconds"] if mem else None,
            }
        )
    return {"name": roster["name"], "watched": watched, "members": out}


async def _membership_for_view(pool: asyncpg.Pool, tags: list[str]) -> dict[str, dict]:
    """Per-tag clan status for /roster view: in_clan, current out stint, lifetime total."""
    unique = list(dict.fromkeys(tags))
    if not unique:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT coc_tag, in_clan,
                   CASE WHEN NOT in_clan AND left_at IS NOT NULL
                        THEN EXTRACT(EPOCH FROM (NOW() - left_at))::bigint END AS out_seconds,
                   (total_absent_seconds
                        + COALESCE(CASE WHEN NOT in_clan AND left_at IS NOT NULL
                                        THEN EXTRACT(EPOCH FROM (NOW() - left_at)) ELSE 0 END, 0))::bigint
                        AS total_seconds
            FROM clan_membership
            WHERE coc_tag = ANY($1::text[]);
            """,
            unique,
        )
    return {r["coc_tag"]: dict(r) for r in rows}


# --- clan-watch membership state (maintained by modules/roster/watcher.py) --

async def get_watched_tags(pool: asyncpg.Pool) -> list[dict]:
    """Every (guild_id, roster_name, coc_tag) across all watched rosters.

    Discord-only members resolve to their oldest linked tag; members with no
    resolvable tag are dropped (can't be checked against the clan).
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.guild_id, r.name AS roster_name,
                   COALESCE(
                       m.coc_tag,
                       (SELECT coc_tag FROM users WHERE discord_id = m.discord_id
                        ORDER BY linked_at ASC LIMIT 1)
                   ) AS coc_tag
            FROM rosters r
            JOIN roster_members m ON m.roster_id = r.id
            WHERE r.watched = TRUE;
            """
        )
    return [dict(r) for r in rows if r["coc_tag"]]


async def get_membership_map(pool: asyncpg.Pool, tags: list[str]) -> dict[str, dict]:
    """Current stored membership rows keyed by tag (for transition detection)."""
    unique = list(dict.fromkeys(tags))
    if not unique:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT coc_tag, coc_name, in_clan, left_at, total_absent_seconds, clan_name "
            "FROM clan_membership WHERE coc_tag = ANY($1::text[]);",
            unique,
        )
    return {r["coc_tag"]: dict(r) for r in rows}


async def seed_membership(
    pool: asyncpg.Pool, coc_tag: str, coc_name: str | None, in_clan: bool, clan_name: str | None = None
) -> None:
    """Record a tag's current state on first sight (silent — no alert). Out => left_at NULL."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO clan_membership (coc_tag, coc_name, in_clan, left_at, clan_name)
            VALUES ($1, $2, $3, NULL, $4)
            ON CONFLICT (coc_tag) DO NOTHING;
            """,
            coc_tag,
            coc_name,
            in_clan,
            clan_name,
        )


async def mark_left(
    pool: asyncpg.Pool, coc_tag: str, coc_name: str | None, clan_name: str | None = None
) -> None:
    """Record that a tag left the clan; starts the absence clock at NOW().

    `clan_name` is the clan they left *to* (their new external clan, or None if
    clanless/unknown) — stored so a later rejoin can report where they came from.
    """
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE clan_membership
            SET in_clan = FALSE, left_at = NOW(),
                coc_name = COALESCE($2, coc_name), clan_name = $3, updated_at = NOW()
            WHERE coc_tag = $1;
            """,
            coc_tag,
            coc_name,
            clan_name,
        )


async def mark_joined(
    pool: asyncpg.Pool, coc_tag: str, coc_name: str | None, clan_name: str | None = None
) -> int | None:
    """Record that a tag rejoined; adds the finished stint to the total.

    `clan_name` is the family clan they joined. Returns the just-ended absence in
    seconds, or None if the prior absence length was unknown (left_at was NULL —
    a first-time/seed join).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH prev AS (SELECT left_at FROM clan_membership WHERE coc_tag = $1)
            UPDATE clan_membership c
            SET in_clan = TRUE,
                total_absent_seconds = c.total_absent_seconds
                    + COALESCE(EXTRACT(EPOCH FROM (NOW() - prev.left_at)), 0)::bigint,
                left_at = NULL,
                coc_name = COALESCE($2, c.coc_name),
                clan_name = COALESCE($3, c.clan_name),
                updated_at = NOW()
            FROM prev
            WHERE c.coc_tag = $1
            RETURNING EXTRACT(EPOCH FROM (NOW() - prev.left_at))::bigint AS absent_seconds;
            """,
            coc_tag,
            coc_name,
            clan_name,
        )
    return row["absent_seconds"] if row else None


async def touch_name(
    pool: asyncpg.Pool, coc_tag: str, coc_name: str | None, clan_name: str | None = None
) -> None:
    """Refresh a tag's cached name (and current family clan) without a state change."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE clan_membership
            SET coc_name = COALESCE($2, coc_name),
                clan_name = COALESCE($3, clan_name),
                updated_at = NOW()
            WHERE coc_tag = $1;
            """,
            coc_tag,
            coc_name,
            clan_name,
        )


async def _upsert_player_cache(
    pool: asyncpg.Pool,
    coc_tag: str,
    coc_name: str | None,
    clan_name: str | None,
    trophies: int | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO coc_player_cache (coc_tag, coc_name, clan_name, trophies, fetched_at)
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (coc_tag) DO UPDATE
                SET coc_name = EXCLUDED.coc_name,
                    clan_name = EXCLUDED.clan_name,
                    trophies = EXCLUDED.trophies,
                    fetched_at = NOW();
            """,
            coc_tag,
            coc_name,
            clan_name,
            trophies,
        )


async def _resolve_players(pool: asyncpg.Pool, tags: list[str]) -> dict[str, dict]:
    """Map each tag to {coc_name, clan_name, coc_api_ok}, using the TTL cache.

    A cached row younger than PLAYER_CACHE_TTL_SECONDS is used as-is (no API
    call). Otherwise the CoC API is called and the cache refreshed; if that call
    fails but a stale row exists, the stale data is used rather than nothing.
    """
    unique = list(dict.fromkeys(t for t in tags if t))
    if not unique:
        return {}

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT coc_tag, coc_name, clan_name, trophies,
                   fetched_at > NOW() - make_interval(secs => $1) AS fresh
            FROM coc_player_cache
            WHERE coc_tag = ANY($2::text[]);
            """,
            PLAYER_CACHE_TTL_SECONDS,
            unique,
        )
    cache = {r["coc_tag"]: r for r in rows}

    def _hit(row) -> dict:
        return {
            "coc_name": row["coc_name"],
            "clan_name": row["clan_name"],
            "trophies": row["trophies"],
            "coc_api_ok": True,
        }

    result: dict[str, dict] = {}
    for tag in unique:
        row = cache.get(tag)
        if row is not None and row["fresh"]:
            result[tag] = _hit(row)
            continue

        player = await get_player(tag)
        if player is not None:
            coc_name = player.get("name")
            clan_name = (player.get("clan") or {}).get("name")
            trophies = player.get("trophies")
            await _upsert_player_cache(pool, tag, coc_name, clan_name, trophies)
            result[tag] = {
                "coc_name": coc_name,
                "clan_name": clan_name,
                "trophies": trophies,
                "coc_api_ok": True,
            }
        elif row is not None:
            # API failed but we have a stale copy — better than "Unknown".
            result[tag] = _hit(row)
        else:
            result[tag] = {"coc_name": None, "clan_name": None, "trophies": None, "coc_api_ok": False}
    return result
