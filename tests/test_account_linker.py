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
async def test_link_account_invalid_tag() -> None:
    pool = _fake_pool(MagicMock())
    with pytest.raises(ValueError):
        await linker.link_account(pool, discord_id=1, coc_tag="ABC123", token="t")


@pytest.mark.asyncio
async def test_get_linked_account_not_found() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    pool = _fake_pool(conn)

    result = await linker.get_linked_account(pool, discord_id=99999)
    assert result is None
    conn.fetchrow.assert_awaited_once()


@pytest.mark.asyncio
async def test_unlink_nonexistent() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 0")
    pool = _fake_pool(conn)

    result = await linker.unlink_account(pool, discord_id=99999)
    assert result is False
