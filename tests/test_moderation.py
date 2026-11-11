"""Unit tests for modules.moderation."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from datetime import timedelta

import disnake.utils

from modules.moderation import logging, messages, purge
from modules.moderation.validation import ModerationError, parse_user_id, validate_member_target


def _role(position: int) -> SimpleNamespace:
    return SimpleNamespace(position=position)


def _member(
    member_id: int,
    *,
    top_role_position: int = 1,
    display_name: str = "TestUser",
) -> MagicMock:
    member = MagicMock(spec=disnake.Member)
    member.id = member_id
    member.top_role = _role(top_role_position)
    member.__str__ = MagicMock(return_value=f"{display_name}#0001")
    return member


def _guild(*, owner_id: int = 100, bot_role_position: int = 10) -> MagicMock:
    guild = MagicMock(spec=disnake.Guild)
    guild.owner_id = owner_id
    bot_member = _member(999, top_role_position=bot_role_position)
    guild.me = bot_member
    return guild


def test_pick_ban_quip_returns_known_string() -> None:
    assert messages.pick_ban_quip() in messages.BAN_QUIPS


def test_pick_kick_quip_returns_known_string() -> None:
    assert messages.pick_kick_quip() in messages.KICK_QUIPS


def test_format_public_message_includes_user_and_quip() -> None:
    user = MagicMock()
    user.__str__ = MagicMock(return_value="BadActor#1234")
    result = messages.format_public_message(user, "go to your room.")
    assert result == "BadActor#1234 go to your room."


def test_parse_user_id_accepts_valid_snowflake() -> None:
    assert parse_user_id("1514111681222148219") == 1514111681222148219


def test_parse_user_id_rejects_invalid() -> None:
    with pytest.raises(ModerationError, match="Invalid user ID"):
        parse_user_id("not-a-number")


def test_validate_member_target_rejects_self() -> None:
    guild = _guild()
    moderator = _member(42, top_role_position=5)
    target = _member(42, top_role_position=1)
    with pytest.raises(ModerationError, match="yourself"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_rejects_owner() -> None:
    guild = _guild(owner_id=7)
    moderator = _member(42, top_role_position=20)
    target = _member(7, top_role_position=1)
    with pytest.raises(ModerationError, match="server owner"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_rejects_higher_role_than_bot() -> None:
    guild = _guild(bot_role_position=5)
    moderator = _member(42, top_role_position=20)
    target = _member(55, top_role_position=5)
    with pytest.raises(ModerationError, match="equal or higher role than me"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_rejects_higher_role_than_moderator() -> None:
    guild = _guild(bot_role_position=20)
    moderator = _member(42, top_role_position=3)
    target = _member(55, top_role_position=8)
    with pytest.raises(ModerationError, match="equal or higher role than you"):
        validate_member_target(guild, target, moderator)


def test_validate_member_target_allows_valid_target() -> None:
    guild = _guild(bot_role_position=10)
    moderator = _member(42, top_role_position=8)
    target = _member(55, top_role_position=2)
    validate_member_target(guild, target, moderator)


def _log_guild(channel, *, guild_id: int = 77) -> MagicMock:
    """A guild whose get_channel returns `channel`, for send_mod_log."""
    guild = MagicMock()
    guild.id = guild_id
    guild.get_channel.return_value = channel
    return guild


def _patch_config(mod_log_channel_id):
    return patch.object(
        logging.guild_config,
        "get_guild_config",
        AsyncMock(return_value={"mod_log_channel_id": mod_log_channel_id}),
    )


@pytest.mark.asyncio
async def test_send_mod_log_posts_embed_with_reason() -> None:
    channel = AsyncMock()
    guild = _log_guild(channel)
    moderator = _member(42)
    moderator.__str__ = MagicMock(return_value="ModUser#0001")

    with _patch_config(1514111681222148219) as get_config:
        await logging.send_mod_log(
            MagicMock(),
            guild,
            action="ban",
            target_label="BadActor#1234",
            target_id=1234,
            moderator=moderator,
            reason="spam",
        )

    get_config.assert_awaited_once()
    assert get_config.await_args.args[1] == 77
    guild.get_channel.assert_called_once_with(1514111681222148219)
    channel.send.assert_awaited_once()
    embed = channel.send.await_args.kwargs["embed"]
    assert embed.title == "Member Banned"
    assert embed.colour.value == 0xE74C3C
    fields = {field.name: field.value for field in embed.fields}
    assert "BadActor#1234" in fields["Target"]
    assert "1234" in fields["Target"]
    assert fields["Reason"] == "spam"


@pytest.mark.asyncio
async def test_send_mod_log_uses_dash_when_no_reason() -> None:
    channel = AsyncMock()
    moderator = _member(42)

    with _patch_config(1514111681222148219):
        await logging.send_mod_log(
            MagicMock(),
            _log_guild(channel),
            action="kick",
            target_label="User#0001",
            target_id=99,
            moderator=moderator,
            reason=None,
        )

    embed = channel.send.await_args.kwargs["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Reason"] == "—"


@pytest.mark.asyncio
async def test_send_mod_log_skips_server_with_no_channel_set() -> None:
    channel = AsyncMock()
    guild = _log_guild(channel)

    with _patch_config(None):
        await logging.send_mod_log(
            MagicMock(), guild, action="kick", target_label="U", target_id=1,
            moderator=_member(42), reason=None,
        )

    guild.get_channel.assert_not_called()
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_mod_log_never_posts_outside_its_own_server() -> None:
    # The channel id resolves only through the acting server; a channel that
    # isn't in it (e.g. another server's log channel) is never posted to.
    guild = _log_guild(None)

    with _patch_config(1514111681222148219):
        await logging.send_mod_log(
            MagicMock(), guild, action="kick", target_label="U", target_id=1,
            moderator=_member(42), reason=None,
        )

    guild.get_channel.assert_called_once_with(1514111681222148219)


# ---------------------------------------------------------------------------
# purge
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fast_purge(monkeypatch):
    """No request pacing in tests, and no purge left registered between tests.

    Message search is unavailable unless a test installs one, so tests that
    name a member exercise the full-scan fallback by default.
    """
    monkeypatch.setattr(purge, "MAX_REQUESTS_PER_SECOND", 0)
    monkeypatch.setattr(
        purge, "_search_page",
        AsyncMock(side_effect=disnake.HTTPException(MagicMock(status=403), "no search")),
    )
    purge._running.clear()
    yield
    purge._running.clear()


_next_id = iter(range(1, 10**6))


def _message(*, author_id: int, content: str, age_days: float) -> MagicMock:
    msg = MagicMock()
    msg.id = 0  # assigned by _purge_channel so history can page by id
    msg.author = SimpleNamespace(id=author_id)
    msg.content = content
    msg.created_at = disnake.utils.utcnow() - timedelta(days=age_days)
    msg.delete = AsyncMock()
    return msg


def _perms(*, history=True, manage=True, threads=True) -> SimpleNamespace:
    return SimpleNamespace(
        read_message_history=history, manage_messages=manage, manage_threads=threads
    )


def _purge_channel(messages_list: list[MagicMock], *, archived_threads=()) -> MagicMock:
    """A channel/thread mock whose history() pages newest-first like Discord.

    The list is newest-first: earlier messages get higher ids.
    """
    channel = MagicMock()
    channel.id = next(_next_id)
    for i, m in enumerate(messages_list):
        m.id = 10**9 - i
    channel.pages_read = 0

    channel.recent_reads = 0

    def history(*, limit=100, before=None, after=None):
        if after is not None:
            # Oldest-first, like disnake when ``after`` is given.
            channel.recent_reads += 1
            page = [m for m in messages_list if m.created_at > after][::-1][:limit]
        else:
            channel.pages_read += 1
            cursor = before.id if before is not None else float("inf")
            page = [m for m in messages_list if m.id < cursor][:limit]

        async def _gen():
            for m in page:
                yield m

        return _gen()

    def archived(**kwargs):
        async def _gen():
            for t in archived_threads:
                yield t

        return _gen()

    def partial(message_id):
        ref = MagicMock()
        ref.id = message_id
        ref.created_at = disnake.utils.snowflake_time(message_id)
        ref.delete = AsyncMock()
        channel.refs[message_id] = ref
        return ref

    channel.refs = {}
    channel.get_partial_message = partial
    channel.history = history
    channel.archived_threads = archived
    channel.delete_messages = AsyncMock()
    channel.edit = AsyncMock()
    channel.permissions_for = MagicMock(return_value=_perms())
    return channel


def _archived_thread(messages_list: list[MagicMock]) -> MagicMock:
    thread = _purge_channel(messages_list)
    thread.__class__ = disnake.Thread  # so the isinstance check sees a thread
    thread.archived = True
    return thread


def _purge_guild(channels: list[MagicMock], *, threads=(), voice=()) -> MagicMock:
    """Guild whose channels are all fully manageable."""
    guild = MagicMock(spec=disnake.Guild)
    guild.id = next(_next_id)
    guild.me = SimpleNamespace(id=999)
    guild.text_channels = list(channels)
    guild.threads = list(threads)
    guild.voice_channels = list(voice)
    guild.stage_channels = []
    guild.forum_channels = []
    guild.media_channels = []
    return guild


@pytest.mark.asyncio
async def test_purge_rejects_empty_word() -> None:
    guild = _purge_guild([])
    with pytest.raises(ModerationError, match="non-empty word"):
        await purge.purge_messages(guild, "   ", SimpleNamespace(id=42), target=SimpleNamespace(id=55))


@pytest.mark.asyncio
async def test_purge_allows_self_target() -> None:
    """A moderator may purge their own messages (no self-target guard)."""
    mod = SimpleNamespace(id=42)
    channel = _purge_channel([_message(author_id=42, content="dog", age_days=1)])
    result = await purge.purge_messages(_purge_guild([channel]), "dog", mod, target=mod)
    assert result.deleted == 1


@pytest.mark.asyncio
async def test_purge_matches_case_insensitive_substring_and_author() -> None:
    target_id = 55
    msgs = [
        _message(author_id=target_id, content="This is a REPORT", age_days=1),   # match
        _message(author_id=target_id, content="nothing here", age_days=1),        # no word
        _message(author_id=999, content="report from someone else", age_days=1),  # wrong author
    ]
    channel = _purge_channel(msgs)

    result = await purge.purge_messages(
        _purge_guild([channel]), "report", SimpleNamespace(id=42), target=SimpleNamespace(id=target_id)
    )

    assert result.deleted == 1
    assert result.channels_scanned == 1
    assert result.messages_scanned == 3
    assert result.finished is True
    # A single recent match is deleted individually, not via bulk.
    channel.delete_messages.assert_not_awaited()
    msgs[0].delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_bulk_deletes_recent_and_individually_deletes_old() -> None:
    recent = [_message(author_id=55, content=f"spam {i}", age_days=1) for i in range(3)]
    old = _message(author_id=55, content="spam old", age_days=30)
    channel = _purge_channel(recent + [old])

    result = await purge.purge_messages(_purge_guild([channel]), "spam", SimpleNamespace(id=42))

    assert result.deleted == 4
    channel.delete_messages.assert_awaited_once()
    assert len(channel.delete_messages.await_args.args[0]) == 3
    old.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_reads_every_page_with_no_deletion_cap() -> None:
    """History past the first page is reached, and there's no 500-per-run stop."""
    msgs = [_message(author_id=1, content="dog", age_days=30) for _ in range(650)]
    channel = _purge_channel(msgs)

    result = await purge.purge_messages(_purge_guild([channel]), "dog", SimpleNamespace(id=42))

    assert result.messages_scanned == 650
    assert result.deleted == 650
    assert channel.pages_read == 7  # 6 full pages + the short last one
    assert all(m.delete.await_count == 1 for m in msgs)


@pytest.mark.asyncio
async def test_purge_bulk_batches_are_at_most_100() -> None:
    msgs = [_message(author_id=1, content="dog", age_days=1) for _ in range(250)]
    channel = _purge_channel(msgs)

    result = await purge.purge_messages(_purge_guild([channel]), "dog", SimpleNamespace(id=42))

    sizes = [len(c.args[0]) for c in channel.delete_messages.await_args_list]
    assert sizes == [100, 100, 50]
    assert result.deleted == 250


@pytest.mark.asyncio
async def test_purge_failed_bulk_delete_retries_one_by_one() -> None:
    """One bad message must not cost the rest of its batch."""
    msgs = [_message(author_id=1, content="dog", age_days=1) for _ in range(3)]
    channel = _purge_channel(msgs)
    channel.delete_messages = AsyncMock(
        side_effect=disnake.HTTPException(MagicMock(status=400), "too old")
    )

    result = await purge.purge_messages(_purge_guild([channel]), "dog", SimpleNamespace(id=42))

    assert result.deleted == 3
    assert result.failed == 0
    assert all(m.delete.await_count == 1 for m in msgs)


@pytest.mark.asyncio
async def test_purge_message_near_the_14_day_line_is_deleted_singly() -> None:
    """A message inside the safety margin never joins a bulk call (Discord would reject it)."""
    msgs = [_message(author_id=1, content="dog", age_days=1) for _ in range(2)]
    edge = _message(author_id=1, content="dog", age_days=14)
    edge.created_at = (
        disnake.utils.utcnow() - timedelta(days=14) + purge.BULK_DELETE_SAFETY_MARGIN / 2
    )
    channel = _purge_channel(msgs + [edge])

    await purge.purge_messages(_purge_guild([channel]), "dog", SimpleNamespace(id=42))

    assert edge not in channel.delete_messages.await_args.args[0]
    edge.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_purge_already_deleted_message_is_not_a_failure() -> None:
    msg = _message(author_id=1, content="dog", age_days=30)
    msg.delete = AsyncMock(side_effect=disnake.NotFound(MagicMock(status=404), "gone"))
    channel = _purge_channel([msg])

    result = await purge.purge_messages(_purge_guild([channel]), "dog", SimpleNamespace(id=42))

    assert result.failed == 0


@pytest.mark.asyncio
async def test_purge_counts_unmanageable_channels_as_skipped() -> None:
    ok_channel = _purge_channel([_message(author_id=55, content="spam", age_days=1)])
    blocked = _purge_channel([])
    blocked.permissions_for = MagicMock(return_value=_perms(history=False, manage=False))

    result = await purge.purge_messages(
        _purge_guild([ok_channel, blocked]), "spam", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.deleted == 1
    assert result.channels_scanned == 1
    assert result.channels_skipped == 1


@pytest.mark.asyncio
async def test_purge_scans_all_channels_threads_and_voice_chats() -> None:
    text = [_purge_channel([_message(author_id=1, content="dog", age_days=1)]) for _ in range(3)]
    thread = _purge_channel([_message(author_id=1, content="dog", age_days=1)])
    voice = _purge_channel([_message(author_id=1, content="dog", age_days=1)])

    result = await purge.purge_messages(
        _purge_guild(text, threads=[thread], voice=[voice]), "dog", SimpleNamespace(id=42)
    )

    assert result.deleted == 5
    assert result.channels_scanned == 5
    assert result.channels_found == 5


@pytest.mark.asyncio
async def test_purge_reaches_archived_threads_and_rearchives_them() -> None:
    hit = _archived_thread([_message(author_id=1, content="dog", age_days=60)])
    miss = _archived_thread([_message(author_id=1, content="cat", age_days=60)])
    parent = _purge_channel([], archived_threads=[hit, miss])

    result = await purge.purge_messages(_purge_guild([parent]), "dog", SimpleNamespace(id=42))

    assert result.deleted == 1
    assert result.channels_scanned == 3
    # Unarchived only to delete, then put back.
    assert [c.kwargs for c in hit.edit.await_args_list] == [{"archived": False}, {"archived": True}]
    miss.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_reports_archived_threads_it_cannot_reach() -> None:
    parent = _purge_channel([])
    parent.permissions_for = MagicMock(return_value=_perms(threads=False))
    parent.archived_threads = MagicMock(side_effect=AssertionError("must not list"))

    result = await purge.purge_messages(_purge_guild([parent]), "dog", SimpleNamespace(id=42))

    assert result.archived_unreachable == 1
    assert "Manage Threads" in purge.format_summary(result, "dog")


@pytest.mark.asyncio
async def test_purge_without_target_deletes_any_authors_matches() -> None:
    msgs = [
        _message(author_id=1, content="buy cheap gems", age_days=1),
        _message(author_id=2, content="GEMS here", age_days=30),
        _message(author_id=3, content="hello", age_days=1),
    ]
    channel = _purge_channel(msgs)

    result = await purge.purge_messages(_purge_guild([channel]), "gems", SimpleNamespace(id=42))

    assert result.deleted == 2
    msgs[0].delete.assert_awaited_once()
    msgs[1].delete.assert_awaited_once()
    msgs[2].delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_channel_restricts_scan_to_that_channel() -> None:
    inside = _purge_channel([_message(author_id=1, content="spam", age_days=1)])
    outside = _purge_channel([_message(author_id=1, content="spam", age_days=1)])
    outside.history = MagicMock(side_effect=AssertionError("must not scan other channels"))

    result = await purge.purge_messages(
        _purge_guild([inside, outside]), "spam", SimpleNamespace(id=42), channel=inside
    )

    assert result.deleted == 1
    assert result.channels_scanned == 1
    assert result.channels_skipped == 0


@pytest.mark.asyncio
async def test_purge_channel_combines_with_target() -> None:
    msgs = [
        _message(author_id=55, content="spam", age_days=1),
        _message(author_id=66, content="spam", age_days=1),
    ]
    channel = _purge_channel(msgs)

    result = await purge.purge_messages(
        _purge_guild([channel]), "spam", SimpleNamespace(id=42),
        target=SimpleNamespace(id=55), channel=channel,
    )

    assert result.deleted == 1
    msgs[0].delete.assert_awaited_once()
    msgs[1].delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_purge_unmanageable_named_channel_raises() -> None:
    channel = _purge_channel([_message(author_id=1, content="spam", age_days=1)])
    channel.permissions_for = MagicMock(return_value=_perms(manage=False))
    channel.mention = "<#1>"

    with pytest.raises(ModerationError, match="Manage Messages"):
        await purge.purge_messages(_purge_guild([channel]), "spam", SimpleNamespace(id=42), channel=channel)


@pytest.mark.asyncio
async def test_purge_cancel_stops_reading_and_deleting() -> None:
    msgs = [_message(author_id=1, content="dog", age_days=30) for _ in range(500)]
    channel = _purge_channel(msgs)

    job = purge.start_purge(_purge_guild([channel]), "dog", SimpleNamespace(id=42))
    job.cancel()
    result = await job.task

    assert result.cancelled is True
    assert result.deleted == 0
    assert channel.pages_read == 0
    assert "cancelled" in purge.format_summary(result, "dog")


@pytest.mark.asyncio
async def test_purge_only_one_at_a_time_per_server() -> None:
    guild = _purge_guild([_purge_channel([])])
    job = purge.start_purge(guild, "dog", SimpleNamespace(id=42))

    with pytest.raises(ModerationError, match="already running"):
        purge.start_purge(guild, "cat", SimpleNamespace(id=42))

    await job.task
    # A finished purge frees the server for the next one.
    await purge.start_purge(guild, "cat", SimpleNamespace(id=42)).task


@pytest.mark.asyncio
async def test_rate_limiter_spaces_requests() -> None:
    import time as _time

    limiter = purge._RateLimiter(50)
    start = _time.monotonic()
    for _ in range(6):
        await limiter.wait()
    # 6 calls at 50/s: the first is immediate, the next 5 are 20ms apart.
    assert _time.monotonic() - start >= 0.09


def test_format_progress_and_summary() -> None:
    result = purge.PurgeResult(
        deleted=1234, messages_scanned=56789, channels_found=10, channels_scanned=4
    )
    progress = purge.format_progress(result, "dog")
    assert "56,789" in progress and "4/10" in progress and "1,234" in progress

    summary = purge.format_summary(result, "dog", member_label="bob", channel_mention="<#1>")
    assert "bob" in summary and "<#1>" in summary and "Purge finished" in summary


# ---------------------------------------------------------------------------
# purge with a member named: Discord message search
# ---------------------------------------------------------------------------

_hit_seq = iter(range(1, 10**6))


def _hit(*, author_id: int, content: str, channel: MagicMock, age_days: float) -> dict:
    """A search result payload, with a real snowflake id so its age is derivable."""
    created = disnake.utils.utcnow() - timedelta(days=age_days)
    return {
        "id": str(disnake.utils.time_snowflake(created) + next(_hit_seq)),
        "channel_id": str(channel.id),
        "author": {"id": str(author_id)},
        "content": content,
    }


class _FakeSearch:
    """Serves search pages in order; then empty pages. Records every query."""

    def __init__(self, *pages: list[dict], extra: dict | None = None, nested: bool = True) -> None:
        self.pages = list(pages)
        self.extra = extra or {}
        self.nested = nested
        self.calls: list[dict] = []

    async def __call__(self, guild, params):
        self.calls.append(dict(params))
        page = self.pages.pop(0) if self.pages else []
        messages = [[m] for m in page] if self.nested else page
        return {"total_results": len(page), "messages": messages, **self.extra}


def _search_guild(channels: list[MagicMock], **kwargs) -> MagicMock:
    guild = _purge_guild(channels, **kwargs)
    by_id = {c.id: c for c in channels}
    guild.get_channel_or_thread = MagicMock(side_effect=lambda cid: by_id.get(cid))
    guild.fetch_channel = AsyncMock(side_effect=disnake.NotFound(MagicMock(status=404), "gone"))
    return guild


@pytest.mark.asyncio
async def test_member_purge_uses_search_and_never_reads_full_history(monkeypatch) -> None:
    channel = _purge_channel([])
    hits = [
        _hit(author_id=55, content="bad DOGS", channel=channel, age_days=1),
        _hit(author_id=55, content="cat", channel=channel, age_days=1),
    ]
    search = _FakeSearch(hits)
    monkeypatch.setattr(purge, "_search_page", search)

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.used_search is True
    assert result.deleted == 1
    assert result.messages_scanned == 2
    # Substring match is checked locally: "dog" deletes "DOGS".
    channel.refs[int(hits[0]["id"])].delete.assert_awaited_once()
    assert int(hits[1]["id"]) not in channel.refs
    # Searches by author only -- a content search would miss substring matches.
    assert search.calls[0]["author_id"] == "55"
    assert "content" not in search.calls[0]
    assert search.calls[0]["include_nsfw"] == "true"
    # Only the recent window was read directly, never the full history.
    assert channel.pages_read == 0
    assert channel.recent_reads == 1


@pytest.mark.asyncio
async def test_member_search_pages_by_max_id_until_empty(monkeypatch) -> None:
    channel = _purge_channel([])
    page1 = [_hit(author_id=55, content="dog", channel=channel, age_days=30) for _ in range(3)]
    page2 = [_hit(author_id=55, content="dog", channel=channel, age_days=60) for _ in range(2)]
    search = _FakeSearch(page1, page2)
    monkeypatch.setattr(purge, "_search_page", search)

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.deleted == 5
    assert len(search.calls) == 3  # two pages, then the empty one that ends it
    assert "max_id" not in search.calls[0]
    assert search.calls[1]["max_id"] == str(min(int(h["id"]) for h in page1))
    assert "offset" not in search.calls[1]


@pytest.mark.asyncio
async def test_member_search_accepts_flat_message_list(monkeypatch) -> None:
    channel = _purge_channel([])
    monkeypatch.setattr(
        purge, "_search_page",
        _FakeSearch([_hit(author_id=55, content="dog", channel=channel, age_days=1)], nested=False),
    )

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.deleted == 1


@pytest.mark.asyncio
async def test_member_search_bulk_deletes_recent_per_channel(monkeypatch) -> None:
    a, b = _purge_channel([]), _purge_channel([])
    hits = [_hit(author_id=55, content="dog", channel=a, age_days=1) for _ in range(3)]
    hits += [_hit(author_id=55, content="dog", channel=b, age_days=1) for _ in range(2)]
    monkeypatch.setattr(purge, "_search_page", _FakeSearch(hits))

    result = await purge.purge_messages(
        _search_guild([a, b]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.deleted == 5
    assert result.channels_scanned == 2
    assert len(a.delete_messages.await_args.args[0]) == 3
    assert len(b.delete_messages.await_args.args[0]) == 2


@pytest.mark.asyncio
async def test_member_search_narrowed_to_a_channel(monkeypatch) -> None:
    channel = _purge_channel([])
    search = _FakeSearch([_hit(author_id=55, content="dog", channel=channel, age_days=1)])
    monkeypatch.setattr(purge, "_search_page", search)

    await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42),
        target=SimpleNamespace(id=55), channel=channel,
    )

    assert search.calls[0]["channel_id"] == str(channel.id)


@pytest.mark.asyncio
async def test_member_search_skips_channels_it_cannot_manage(monkeypatch) -> None:
    blocked = _purge_channel([])
    blocked.permissions_for = MagicMock(return_value=_perms(manage=False))
    hits = [_hit(author_id=55, content="dog", channel=blocked, age_days=1) for _ in range(2)]
    monkeypatch.setattr(purge, "_search_page", _FakeSearch(hits))

    result = await purge.purge_messages(
        _search_guild([blocked]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.deleted == 0
    assert result.channels_skipped == 1  # counted once, not per message
    assert blocked.refs == {}


@pytest.mark.asyncio
async def test_member_search_fetches_and_unarchives_an_uncached_thread(monkeypatch) -> None:
    thread = _archived_thread([])
    guild = _search_guild([])
    guild.fetch_channel = AsyncMock(return_value=thread)
    monkeypatch.setattr(
        purge, "_search_page",
        _FakeSearch([_hit(author_id=55, content="dog", channel=thread, age_days=90)]),
    )

    result = await purge.purge_messages(guild, "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55))

    assert result.deleted == 1
    guild.fetch_channel.assert_awaited_once_with(thread.id)
    assert [c.kwargs for c in thread.edit.await_args_list] == [{"archived": False}, {"archived": True}]


@pytest.mark.asyncio
async def test_member_search_waits_out_not_indexed_then_continues(monkeypatch) -> None:
    channel = _purge_channel([])
    hit = _hit(author_id=55, content="dog", channel=channel, age_days=1)
    replies = [
        {"message": "Index not yet available. Try again later", "code": 110000, "retry_after": 0},
        {"messages": [[hit]], "total_results": 1},
        {"messages": [], "total_results": 0},
    ]
    monkeypatch.setattr(purge, "_search_page", AsyncMock(side_effect=replies))
    monkeypatch.setattr(purge, "SEARCH_INDEX_DEFAULT_WAIT_SECONDS", 0)

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.used_search is True
    assert result.deleted == 1


@pytest.mark.asyncio
async def test_member_purge_falls_back_to_full_scan_when_never_indexed(monkeypatch) -> None:
    channel = _purge_channel([_message(author_id=55, content="dog", age_days=1)])
    not_indexed = {"code": 110000, "retry_after": 0}
    monkeypatch.setattr(purge, "_search_page", AsyncMock(return_value=not_indexed))
    monkeypatch.setattr(purge, "SEARCH_INDEX_DEFAULT_WAIT_SECONDS", 0)

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.used_search is False
    assert result.deleted == 1
    assert channel.pages_read == 1
    assert purge._search_page.await_count == purge.SEARCH_INDEX_RETRIES


@pytest.mark.asyncio
async def test_member_purge_falls_back_when_search_errors() -> None:
    """The default fixture makes search 403; the full scan still does the job."""
    channel = _purge_channel([_message(author_id=55, content="dog", age_days=1)])

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.used_search is False
    assert result.deleted == 1
    assert result.channels_scanned == 1


@pytest.mark.asyncio
async def test_member_search_warns_while_discord_is_still_indexing(monkeypatch) -> None:
    channel = _purge_channel([])
    monkeypatch.setattr(
        purge, "_search_page",
        _FakeSearch(
            [_hit(author_id=55, content="dog", channel=channel, age_days=1)],
            extra={"doing_deep_historical_index": True},
        ),
    )

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.search_incomplete is True
    assert "still indexing" in purge.format_summary(result, "dog", member_label="bob")


@pytest.mark.asyncio
async def test_member_purge_catches_a_message_too_new_to_be_searchable(monkeypatch) -> None:
    """Search lags new messages; the recent-history read deletes them anyway."""
    just_posted = _message(author_id=55, content="chicken", age_days=0.0001)
    someone_else = _message(author_id=66, content="chicken", age_days=0.0001)
    channel = _purge_channel([just_posted, someone_else])
    monkeypatch.setattr(purge, "_search_page", _FakeSearch())  # index has nothing yet

    result = await purge.purge_messages(
        _search_guild([channel]), "chicken", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.used_search is True
    assert result.deleted == 1
    just_posted.delete.assert_awaited_once()
    someone_else.delete.assert_not_awaited()
    assert channel.pages_read == 0


@pytest.mark.asyncio
async def test_message_found_by_search_and_recent_read_is_deleted_once(monkeypatch) -> None:
    recent = _message(author_id=55, content="dog", age_days=0.0001)
    channel = _purge_channel([recent])
    payload = {
        "id": str(recent.id),
        "channel_id": str(channel.id),
        "author": {"id": "55"},
        "content": "dog",
    }
    monkeypatch.setattr(purge, "_search_page", _FakeSearch([payload]))

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.deleted == 1
    assert recent.delete.await_count + len(channel.refs) == 1


@pytest.mark.asyncio
async def test_member_purge_falls_back_when_search_hides_message_text(monkeypatch) -> None:
    """Blank content on every hit would mean nothing ever matches -- don't trust it."""
    channel = _purge_channel([_message(author_id=55, content="dog", age_days=30)])
    blank = [_hit(author_id=55, content="", channel=channel, age_days=30) for _ in range(10)]
    monkeypatch.setattr(purge, "_search_page", _FakeSearch(blank))

    result = await purge.purge_messages(
        _search_guild([channel]), "dog", SimpleNamespace(id=42), target=SimpleNamespace(id=55)
    )

    assert result.used_search is False
    assert result.deleted == 1
    assert channel.pages_read == 1


def test_search_progress_says_it_is_checking_their_messages() -> None:
    result = purge.PurgeResult(used_search=True, messages_scanned=1500, deleted=12)
    progress = purge.format_progress(result, "dog")
    assert "1,500" in progress and "their message" in progress


@pytest.mark.asyncio
async def test_mod_log_without_target_id_omits_id() -> None:
    channel = MagicMock()
    channel.send = AsyncMock()
    moderator = SimpleNamespace(id=42)

    with _patch_config(123):
        await logging.send_mod_log(
            MagicMock(), _log_guild(channel), action="purge", target_label="Everyone",
            target_id=None, moderator=moderator, reason="r",
        )

    embed = channel.send.await_args.kwargs["embed"]
    fields = {field.name: field.value for field in embed.fields}
    assert fields["Target"] == "Everyone"
