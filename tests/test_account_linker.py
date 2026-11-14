"""Unit tests for modules.account_linker.linker."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.account_linker import linker


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


@pytest.mark.asyncio
async def test_link_account_blank_tag() -> None:
    pool = _fake_pool(MagicMock())
    with pytest.raises(ValueError):
        await linker.link_account(pool, discord_id=1, coc_tag="   ", token="t")


def test_normalize_tag_is_lenient() -> None:
    # accepts a tag without '#', lowercase, with spaces, and fixes the O->0 typo
    assert linker._normalize_tag(" 2ppojccl ") == "#2PP0JCCL"
    assert linker._normalize_tag("#2pp0jccl") == "#2PP0JCCL"
    # strips invisible bidi isolate marks copied from a rendered embed/board
    assert linker._normalize_tag("⁦#98RRVQQVL⁩") == "#98RRVQQVL"


@pytest.mark.asyncio
async def test_link_account_success(monkeypatch) -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    pool = _fake_pool(conn)

    monkeypatch.setattr(linker, "_verify_token", AsyncMock(return_value="ok"))
    monkeypatch.setattr(linker, "_fetch_player_name", AsyncMock(return_value="Chief"))

    result = await linker.link_account(pool, discord_id=1, coc_tag="2pp0jccl", token="t")
    assert result == {"success": True, "coc_name": "Chief", "coc_tag": "#2PP0JCCL"}
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_link_account_invalid_token(monkeypatch) -> None:
    pool = _fake_pool(MagicMock())
    monkeypatch.setattr(linker, "_verify_token", AsyncMock(return_value="invalid"))

    result = await linker.link_account(pool, discord_id=1, coc_tag="#2PP0JCCL", token="bad")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_link_account_tag_not_found(monkeypatch) -> None:
    pool = _fake_pool(MagicMock())
    monkeypatch.setattr(linker, "_verify_token", AsyncMock(return_value="notfound"))

    result = await linker.link_account(pool, discord_id=1, coc_tag="#ZZZZZ", token="t")
    assert result["success"] is False
    assert "No player found" in result["error"]


@pytest.mark.asyncio
async def test_get_linked_accounts_empty() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = _fake_pool(conn)

    result = await linker.get_linked_accounts(pool, discord_id=99999)
    assert result == []
    conn.fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_linked_accounts_returns_all() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"discord_id": 1, "coc_tag": "#AAA", "coc_name": "Main", "verified": True},
            {"discord_id": 1, "coc_tag": "#BBB", "coc_name": "Alt", "verified": True},
        ]
    )
    pool = _fake_pool(conn)

    result = await linker.get_linked_accounts(pool, discord_id=1)
    assert [a["coc_tag"] for a in result] == ["#AAA", "#BBB"]


@pytest.mark.asyncio
async def test_unlink_nonexistent() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 0")
    pool = _fake_pool(conn)

    result = await linker.unlink_account(pool, discord_id=99999, coc_tag="#ZZZ")
    assert result is False


@pytest.mark.asyncio
async def test_unlink_scopes_to_caller() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 1")
    pool = _fake_pool(conn)

    result = await linker.unlink_account(pool, discord_id=1, coc_tag="#aaa")
    assert result is True
    # tag normalized and discord_id passed for scoping
    args = conn.execute.await_args.args
    assert args[1] == 1 and args[2] == "#AAA"


@pytest.mark.asyncio
async def test_get_all_accounts_only_returns_the_given_members() -> None:
    """Links are global, but /dumpaccounts passes one server's member ids so an
    admin only ever sees their own server's members."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await linker.get_all_accounts(_fake_pool(conn), [11, 22])

    sql, *args = conn.fetch.await_args.args
    assert "discord_id = ANY($1::bigint[])" in sql
    assert args == [[11, 22]]


@pytest.mark.asyncio
async def test_get_all_accounts_with_no_members_skips_the_query() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock()

    assert await linker.get_all_accounts(_fake_pool(conn), []) == []
    conn.fetch.assert_not_awaited()


def _admin_conn(holder=None, insert_result="INSERT 0 1") -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=holder)
    conn.execute = AsyncMock(return_value=insert_result)
    return conn


@pytest.mark.asyncio
async def test_admin_link_stores_an_unverified_link(monkeypatch) -> None:
    conn = _admin_conn()
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=("ok", "Chief")))

    result = await linker.admin_link_account(_fake_pool(conn), 42, "2pp0jccl", admin_id=9)

    assert result == {
        "success": True, "coc_tag": "#2PP0JCCL", "coc_name": "Chief",
        "already": False, "verified": False,
    }
    sql, *args = conn.execute.await_args.args
    assert "FALSE" in sql and "ON CONFLICT (coc_tag) DO NOTHING" in sql
    assert args == [42, "#2PP0JCCL", "Chief"]


@pytest.mark.asyncio
async def test_admin_link_never_takes_a_tag_from_another_user(monkeypatch) -> None:
    """Links are global, so an admin in one server must not reassign someone's tag."""
    conn = _admin_conn(holder={"discord_id": 77, "verified": True})
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=("ok", "Chief")))

    result = await linker.admin_link_account(_fake_pool(conn), 42, "#2PP0JCCL", admin_id=9)

    assert result["success"] is False
    assert result["holder_id"] == 77
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_link_keeps_an_existing_verified_link_verified(monkeypatch) -> None:
    conn = _admin_conn(holder={"discord_id": 42, "verified": True})
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=("ok", "Chief")))

    result = await linker.admin_link_account(_fake_pool(conn), 42, "#2PP0JCCL", admin_id=9)

    assert result["success"] is True
    assert result["already"] is True and result["verified"] is True
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"), [("notfound", "No player found"), ("error", "unavailable")]
)
async def test_admin_link_checks_the_tag_before_touching_the_db(monkeypatch, status, message) -> None:
    conn = _admin_conn()
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=(status, None)))

    result = await linker.admin_link_account(_fake_pool(conn), 42, "#ZZZZZ", admin_id=9)

    assert result["success"] is False and message in result["error"]
    assert result["holder_id"] is None
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_link_reports_losing_a_race_for_the_tag(monkeypatch) -> None:
    conn = _admin_conn(insert_result="INSERT 0 0")
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=("ok", "Chief")))

    result = await linker.admin_link_account(_fake_pool(conn), 42, "#2PP0JCCL", admin_id=9)

    assert result["success"] is False
    assert "just linked" in result["error"]


@pytest.mark.asyncio
async def test_fetch_player_name_uses_the_lookup(monkeypatch) -> None:
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=("ok", "Chief")))
    assert await linker._fetch_player_name("#2PP0JCCL") == "Chief"
    monkeypatch.setattr(linker, "_lookup_player", AsyncMock(return_value=("error", None)))
    assert await linker._fetch_player_name("#2PP0JCCL") is None
