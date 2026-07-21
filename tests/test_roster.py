"""Unit tests for modules.roster.storage (mocked pool + CoC API)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.roster import storage


class _FakePoolAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolAcquireCtx(conn))
    return pool


def test_clean_name_rejects_blank() -> None:
    with pytest.raises(ValueError):
        storage._clean_name("   ")


@pytest.mark.asyncio
async def test_create_roster_success() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 7, "name": "War A"})
    result = await storage.create_roster(_fake_pool(conn), guild_id=1, name="  War A  ")
    assert result == {"id": 7, "name": "War A"}


@pytest.mark.asyncio
async def test_create_roster_duplicate_raises() -> None:
    import asyncpg

    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))
    with pytest.raises(ValueError):
        await storage.create_roster(_fake_pool(conn), guild_id=1, name="War A")


@pytest.mark.asyncio
async def test_add_member_by_discord_new_vs_existing() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 3, "name": "War A"})  # get_roster
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    added = await storage.add_member_by_discord(_fake_pool(conn), 1, "War A", discord_id=42)
    assert added is True

    conn.execute = AsyncMock(return_value="INSERT 0 0")
    again = await storage.add_member_by_discord(_fake_pool(conn), 1, "War A", discord_id=42)
    assert again is False


@pytest.mark.asyncio
async def test_add_member_unknown_roster_raises() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)  # get_roster -> not found
    with pytest.raises(ValueError):
        await storage.add_member_by_discord(_fake_pool(conn), 1, "Nope", discord_id=42)


@pytest.mark.asyncio
async def test_add_member_by_tag_normalizes() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 3, "name": "War A"})
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    await storage.add_member_by_tag(_fake_pool(conn), 1, "War A", coc_tag=" 2pp0jccl ")
    # tag passed to the INSERT is normalized to #2PP0JCCL
    assert conn.execute.await_args.args[2] == "#2PP0JCCL"


@pytest.mark.asyncio
async def test_move_member_not_in_source() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 3, "name": "x"})  # both rosters exist
    conn.execute = AsyncMock(return_value="DELETE 0")  # nothing deleted from source
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)

    result = await storage.move_member_by_discord(_fake_pool(conn), 1, "A", "B", discord_id=9)
    assert result == "not_in_source"


@pytest.mark.asyncio
async def test_build_roster_view_marks_unlinked(monkeypatch) -> None:
    # One member added by discord id with no CoC link, one added by tag.
    conn = MagicMock()
    conn.fetchrow = AsyncMock(
        side_effect=[
            {"id": 5, "name": "War A"},  # _require_roster -> get_roster
            None,  # users lookup for the discord-only member -> unlinked
            {"discord_id": 111},  # users lookup for the tag member -> linked back
        ]
    )
    conn.fetch = AsyncMock(
        return_value=[
            {"discord_id": 42, "coc_tag": None},
            {"discord_id": None, "coc_tag": "#ABC"},
        ]
    )
    # Player enrichment is covered by the _resolve_players tests below.
    monkeypatch.setattr(
        storage,
        "_resolve_players",
        AsyncMock(
            return_value={
                "#ABC": {"coc_name": "Bob", "clan_name": "July", "trophies": 4000, "coc_api_ok": True}
            }
        ),
    )

    view = await storage.build_roster_view(_fake_pool(conn), 1, "War A")
    m0, m1 = view["members"]
    assert m0["discord_id"] == 42 and m0["coc_tag"] is None  # stays unlinked
    assert m1["coc_tag"] == "#ABC" and m1["discord_id"] == 111 and m1["clan_name"] == "July"


@pytest.mark.asyncio
async def test_resolve_players_uses_fresh_cache(monkeypatch) -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"coc_tag": "#ABC", "coc_name": "Bob", "clan_name": "July", "trophies": 4000, "fresh": True}
        ]
    )
    gp = AsyncMock()
    monkeypatch.setattr(storage, "get_player", gp)

    result = await storage._resolve_players(_fake_pool(conn), ["#ABC", "#ABC"])
    assert result["#ABC"] == {"coc_name": "Bob", "clan_name": "July", "trophies": 4000, "coc_api_ok": True}
    gp.assert_not_awaited()  # within TTL -> no API call


@pytest.mark.asyncio
async def test_resolve_players_refetches_when_stale(monkeypatch) -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"coc_tag": "#ABC", "coc_name": "Old", "clan_name": None, "fresh": False}]
    )
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    monkeypatch.setattr(
        storage, "get_player", AsyncMock(return_value={"name": "New", "clan": {"name": "July"}})
    )

    result = await storage._resolve_players(_fake_pool(conn), ["#ABC"])
    assert result["#ABC"]["coc_name"] == "New" and result["#ABC"]["clan_name"] == "July"
    conn.execute.assert_awaited()  # cache refreshed


@pytest.mark.asyncio
async def test_resolve_players_stale_fallback_on_api_fail(monkeypatch) -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"coc_tag": "#ABC", "coc_name": "Old", "clan_name": "July", "trophies": 3200, "fresh": False}
        ]
    )
    monkeypatch.setattr(storage, "get_player", AsyncMock(return_value=None))

    result = await storage._resolve_players(_fake_pool(conn), ["#ABC"])
    assert result["#ABC"] == {"coc_name": "Old", "clan_name": "July", "trophies": 3200, "coc_api_ok": True}


# --- clan-watch ------------------------------------------------------------

def test_format_duration() -> None:
    assert storage.format_duration(None) is None
    assert storage.format_duration(30) == "<1m"
    assert storage.format_duration(45 * 60) == "45m"
    assert storage.format_duration(5 * 3600) == "5h"
    assert storage.format_duration(5 * 3600 + 12 * 60) == "5h 12m"
    assert storage.format_duration(3 * 86400 + 4 * 3600) == "3d 4h"
    assert storage.format_duration(2 * 86400) == "2d"


@pytest.mark.asyncio
async def test_set_watched_updates_flag() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 9, "name": "War A", "watched": False})
    conn.execute = AsyncMock(return_value="UPDATE 1")
    name = await storage.set_watched(_fake_pool(conn), 1, "War A", True)
    assert name == "War A"
    assert conn.execute.await_args.args[1] is True and conn.execute.await_args.args[2] == 9


@pytest.mark.asyncio
async def test_get_watched_tags_drops_untagged() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"guild_id": 1, "roster_name": "War A", "coc_tag": "#ABC"},
            {"guild_id": 1, "roster_name": "War A", "coc_tag": None},  # unlinked -> dropped
        ]
    )
    result = await storage.get_watched_tags(_fake_pool(conn))
    assert len(result) == 1 and result[0]["coc_tag"] == "#ABC"


@pytest.mark.asyncio
async def test_mark_joined_returns_absence_seconds() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"absent_seconds": 7200})
    seconds = await storage.mark_joined(_fake_pool(conn), "#ABC", "Bob")
    assert seconds == 7200


# --- view rendering (bidi isolation) ---------------------------------------

def test_bubble_isolates_rtl_cells_from_neighbours() -> None:
    """A row with an Arabic clan name must not let it bleed into the Status column.

    Each cell is fenced with LRI/PDI so the bidi algorithm can't reorder an RTL
    clan name into the adjacent status/duration text (the "دردشهm" bug).
    """
    from modules.roster import render as rc

    row = {"ign": "Stress", "clan": "دردشه", "status": "❌ 5m"}
    columns = ("ign", "clan", "status")
    show = {"ign": True, "clan": True, "status": True}
    bubble = rc._bubble(1, row, columns, show)

    # Every visible cell is wrapped in a directional-isolate pair...
    assert bubble.count(rc._LRI) == 3 and bubble.count(rc._PDI) == 3
    # ...and the Arabic clan sits inside its own isolate, never fused to "5m".
    assert f"{rc._LRI}دردشه{rc._PDI}" in bubble
    assert f"{rc._LRI}❌ 5m{rc._PDI}" in bubble
