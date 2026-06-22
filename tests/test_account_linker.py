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
