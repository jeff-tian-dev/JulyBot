"""Unit tests for modules.guild_config.storage (mocked pool)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from database import models
from modules.guild_config import storage


class _FakeCtx:
    def __init__(self, value=None) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakeCtx(conn))
    conn.transaction = MagicMock(return_value=_FakeCtx())
    return pool


def _conn(*, already_imported: bool) -> MagicMock:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"legacy_config_imported": already_imported})
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()
    return conn


@pytest.mark.asyncio
async def test_get_guild_config_defaults_to_unset_for_unknown_server() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    config = await storage.get_guild_config(_fake_pool(conn), 42)

    assert config["mod_log_channel_id"] is None
    assert config["clan_watch_channel_id"] is None
    assert conn.fetchrow.await_args.args[1] == 42


@pytest.mark.asyncio
async def test_import_legacy_config_copies_values_once() -> None:
    conn = _conn(already_imported=False)

    ran = await storage.import_legacy_config(
        _fake_pool(conn),
        7,
        mod_log_channel_id=11,
        clan_watch_channel_id=22,
        twitter_ping_role_id=0,
        youtube_ping_role_id=44,
        clan_tags=["2pp", "#2PP", "", "q0"],
    )

    assert ran is True
    sql, *args = conn.execute.await_args.args
    # Never overwrites a value already set, and marks the import done.
    assert "COALESCE(mod_log_channel_id, $2)" in sql
    assert "legacy_config_imported = TRUE" in sql
    # 0 (unset in .env) is stored as NULL, not as channel id 0.
    assert args == [7, 11, 22, None, 44]
    # Tags normalised and de-duplicated; the empty one is skipped.
    rows = conn.executemany.await_args.args[1]
    assert rows == [(7, "#2PP"), (7, "#Q0")]


@pytest.mark.asyncio
async def test_import_legacy_config_never_runs_twice() -> None:
    """Once imported, a restart must not restore a value an admin cleared."""
    conn = _conn(already_imported=True)

    ran = await storage.import_legacy_config(
        _fake_pool(conn), 7, mod_log_channel_id=11, clan_tags=["#2PP"]
    )

    assert ran is False
    conn.execute.assert_not_awaited()
    conn.executemany.assert_not_awaited()


@pytest.mark.asyncio
async def test_import_legacy_config_needs_a_home_server() -> None:
    conn = _conn(already_imported=False)
    assert await storage.import_legacy_config(_fake_pool(conn), 0, mod_log_channel_id=11) is False
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_channel_only_accepts_known_columns() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = _fake_pool(conn)

    await storage.set_channel(pool, 7, "mod_log_channel_id", 99)
    sql, *args = conn.execute.await_args.args
    assert "mod_log_channel_id" in sql and args == [7, 99]

    # The column name is interpolated into SQL, so anything else is refused.
    with pytest.raises(ValueError):
        await storage.set_channel(pool, 7, "guild_id; DROP TABLE users", 1)
    with pytest.raises(ValueError):
        await storage.set_ping_role(pool, 7, "tiktok", 1)


@pytest.mark.asyncio
async def test_set_ping_role_clears_with_none() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()

    await storage.set_ping_role(_fake_pool(conn), 7, "youtube", None)

    sql, *args = conn.execute.await_args.args
    assert "youtube_ping_role_id" in sql and args == [7, None]


@pytest.mark.asyncio
async def test_clans_by_guild_groups_per_server() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[
            {"guild_id": 1, "clan_tag": "#A"},
            {"guild_id": 2, "clan_tag": "#B"},
            {"guild_id": 1, "clan_tag": "#C"},
        ]
    )

    assert await storage.clans_by_guild(_fake_pool(conn)) == {1: ["#A", "#C"], 2: ["#B"]}


@pytest.mark.asyncio
async def test_add_clan_normalises_and_reports_duplicates() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=["INSERT 0 1", "INSERT 0 0"])
    pool = _fake_pool(conn)

    assert await storage.add_clan(pool, 7, "2pp", "July", 5) is True
    assert conn.execute.await_args.args[1:3] == (7, "#2PP")
    assert await storage.add_clan(pool, 7, "#2PP", "July", 5) is False


def test_per_server_keys_are_migrated() -> None:
    """There's no live DB in tests, so pin the migration SQL itself."""
    assert "PRIMARY KEY (guild_id, coc_tag)" in models.CREATE_CLAN_MEMBERSHIP
    assert "ADD PRIMARY KEY (guild_id, coc_tag)" in models.MIGRATE_CLAN_MEMBERSHIP_GUILD_KEY
    assert "SET guild_id = $1 WHERE guild_id IS NULL" in models.BACKFILL_CLAN_MEMBERSHIP_GUILD
    assert "PRIMARY KEY (guild_id, tweet_id)" in models.CREATE_SEEN_TWEETS
    assert "ADD PRIMARY KEY (guild_id, tweet_id)" in models.MIGRATE_SEEN_TWEETS_PER_GUILD
    assert "legacy_config_imported" in models.ALTER_GUILD_SETTINGS_MULTI_SERVER
