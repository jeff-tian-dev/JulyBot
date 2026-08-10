"""Unit tests for /postbase: rendering/validation and storage."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.announce import base_poster, base_storage
from modules.announce.poster import PostError


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


# --- link validation --------------------------------------------------------


def test_validate_link_accepts_clash_layout_link() -> None:
    link = "https://link.clashofclans.com/en?action=OpenLayout&id=TH16%3AHV%3AAAA"
    assert base_poster.validate_link(link) == link


def test_validate_link_strips_whitespace() -> None:
    assert base_poster.validate_link("  https://example.com/a  ") == "https://example.com/a"


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not a link", "javascript:alert(1)", "ftp://example.com", "https://"],
)
def test_validate_link_rejects_junk(bad: str) -> None:
    with pytest.raises(PostError):
        base_poster.validate_link(bad)


def test_validate_link_rejects_overlong() -> None:
    with pytest.raises(PostError):
        base_poster.validate_link("https://e.com/" + "a" * base_poster.MAX_LINK_LENGTH)


# --- text normalisation -----------------------------------------------------


def test_normalize_text_converts_literal_newlines() -> None:
    assert base_poster.normalize_text("a\\nb") == "a\nb"


def test_normalize_text_blank_becomes_none() -> None:
    assert base_poster.normalize_text("   ") is None
    assert base_poster.normalize_text(None) is None


def test_validate_base_input_rejects_overlong_description() -> None:
    with pytest.raises(PostError):
        base_poster.validate_base_input(
            link="https://example.com",
            description="x" * (base_poster.MAX_DESCRIPTION_LENGTH + 1),
        )


# --- embed body -------------------------------------------------------------


def test_build_base_body_matches_layout() -> None:
    body = base_poster.build_base_body(cc="X2 HH x2 W x1 FRN", description="Invis Rage Cake Base!")
    # The title is absent by design — it lives in the embed's native title field.
    assert body == f"{base_poster.DIVIDER}\n\n**CC:**\nX2 HH x2 W x1 FRN\n\nInvis Rage Cake Base!"


def test_divider_is_not_bare_markdown_rule() -> None:
    # Markdown "---" does NOT become a horizontal rule inside an embed
    # description; Discord renders it as three bare dashes, which looks broken.
    assert base_poster.DIVIDER != "---"
    assert set(base_poster.DIVIDER) == {"-"} and len(base_poster.DIVIDER) > 3


def test_build_base_body_renders_description_bare() -> None:
    # No "Notes:" heading is injected — the poster types their own label if
    # they want one, so whatever they wrote is reproduced verbatim.
    body = base_poster.build_base_body(cc=None, description="Notes:\nmine")
    assert body == f"{base_poster.DIVIDER}\n\nNotes:\nmine"


def test_build_base_body_omits_blank_sections() -> None:
    body = base_poster.build_base_body(cc=None, description="Just notes.")
    assert body == f"{base_poster.DIVIDER}\n\nJust notes."
    assert "CC:" not in body


def test_build_base_body_rejects_combined_overflow() -> None:
    # CC and description are each individually valid yet overflow the 4096
    # embed limit once concatenated (reachable via the edit modal, which takes
    # raw text). The title is exempt — it has its own field and its own limit.
    with pytest.raises(PostError):
        base_poster.build_base_body(
            cc="c" * base_poster.MAX_CC_LENGTH,
            description="d" * base_poster.MAX_EMBED_DESCRIPTION_LENGTH,
        )


def test_build_base_embed_puts_title_in_title_field() -> None:
    embed = base_poster.build_base_embed(title="Tap 6.0", cc=None, description="notes")
    assert embed.title == "Tap 6.0"
    # ...and not duplicated into the body.
    assert "Tap 6.0" not in embed.description


def test_build_base_embed_omits_blank_title() -> None:
    embed = base_poster.build_base_embed(title=None, cc=None, description="notes")
    assert not embed.title


def test_build_base_embed_uses_attachment_uri() -> None:
    embed = base_poster.build_base_embed(
        title="Tap", cc=None, description="notes", image_filename="my base.png"
    )
    # The filename is sanitised so attachment:// resolves.
    assert embed.image.url == "attachment://my_base.png"
    assert embed.colour.value == base_poster.BASE_EMBED_COLOUR


def test_build_base_embed_prefers_filename_over_url() -> None:
    embed = base_poster.build_base_embed(
        title=None,
        cc=None,
        description="x",
        image_filename="a.png",
        image_url="https://cdn.example/b.png",
    )
    assert embed.image.url == "attachment://a.png"


def test_embed_from_record_uses_stored_url() -> None:
    record = {
        "title": "Tap 6.0",
        "cc": "x2 HH",
        "description": "notes",
        "image_url": "https://cdn.example/base.png",
    }
    embed = base_poster.embed_from_record(record)
    assert embed.image.url == "https://cdn.example/base.png"
    assert "**CC:**" in embed.description


# --- image validation -------------------------------------------------------


def test_validate_image_rejects_non_image() -> None:
    attachment = MagicMock()
    attachment.content_type = "application/pdf"
    attachment.filename = "base.pdf"
    with pytest.raises(PostError):
        base_poster.validate_image(attachment)


def test_validate_image_accepts_png() -> None:
    attachment = MagicMock()
    attachment.content_type = "image/png"
    attachment.filename = "base.png"
    base_poster.validate_image(attachment)  # does not raise


# --- stats visibility gate --------------------------------------------------


def _user(user_id: int, *, admin: bool = False, manage_messages: bool = False):
    user = MagicMock()
    user.id = user_id
    user.guild_permissions.administrator = admin
    user.guild_permissions.manage_messages = manage_messages
    return user


def _record(author_id: int = 1, *, stats_admin_only: bool = False):
    return {"author_id": author_id, "stats_admin_only": stats_admin_only}


def test_stats_public_by_default() -> None:
    from discord_bot.commands import base_post_commands as bp

    assert bp._can_view_stats(_user(99), _record()) is True


def test_stats_hidden_from_plain_member_when_flagged() -> None:
    from discord_bot.commands import base_post_commands as bp

    assert bp._can_view_stats(_user(99), _record(stats_admin_only=True)) is False


def test_stats_hidden_even_from_the_author() -> None:
    from discord_bot.commands import base_post_commands as bp

    # The author isn't exempt — the flag exists to keep the tally private, and
    # /postbase is open to everyone so the author may be a plain member.
    author = _user(1)
    assert bp._can_view_stats(author, _record(author_id=1, stats_admin_only=True)) is False


@pytest.mark.parametrize("perm", ["admin", "manage_messages"])
def test_stats_visible_to_moderators(perm: str) -> None:
    from discord_bot.commands import base_post_commands as bp

    mod = _user(99, **{perm if perm == "admin" else "manage_messages": True})
    assert bp._can_view_stats(mod, _record(stats_admin_only=True)) is True


# --- storage ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_download_is_idempotent_per_user() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 0")
    conn.fetchval = AsyncMock(return_value=3)
    pool = _fake_pool(conn)

    count = await base_storage.record_download(pool, base_post_id=7, user_id=42)

    assert count == 3
    # The insert must swallow duplicates so the tally stays unique-per-user.
    sql = conn.execute.await_args.args[0]
    assert "ON CONFLICT DO NOTHING" in sql


@pytest.mark.asyncio
async def test_update_base_post_only_sets_provided_fields() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    pool = _fake_pool(conn)

    await base_storage.update_base_post(pool, 1, description="new notes")

    sql, *params = conn.fetchrow.await_args.args
    assert "description = $2" in sql
    assert "title" not in sql
    assert params == [1, "new notes"]


@pytest.mark.asyncio
async def test_update_base_post_clears_field_with_empty_string() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    pool = _fake_pool(conn)

    await base_storage.update_base_post(pool, 1, title="")

    sql, *params = conn.fetchrow.await_args.args
    assert "title = $2" in sql
    # Empty string is stored as NULL, which is how the modal clears a field.
    assert params == [1, None]


@pytest.mark.asyncio
async def test_update_base_post_with_no_fields_is_a_read() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    pool = _fake_pool(conn)

    await base_storage.update_base_post(pool, 1)

    sql = conn.fetchrow.await_args.args[0]
    assert sql.strip().upper().startswith("SELECT")


@pytest.mark.asyncio
async def test_create_base_post_persists_stats_flag() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    pool = _fake_pool(conn)

    await base_storage.create_base_post(
        pool,
        guild_id=1,
        channel_id=2,
        author_id=3,
        link="https://example.com",
        stats_admin_only=True,
    )

    params = conn.fetchrow.await_args.args[1:]
    assert params[-1] is True


@pytest.mark.asyncio
async def test_list_views_to_restore_is_a_single_query() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(
        return_value=[{"id": 1, "stats_admin_only": True, "download_count": 4}]
    )
    pool = _fake_pool(conn)

    rows = await base_storage.list_views_to_restore(pool)

    # One aggregate query, not a count per post.
    conn.fetch.assert_awaited_once()
    sql = conn.fetch.await_args.args[0]
    assert "LEFT JOIN base_post_downloads" in sql
    assert "message_id IS NOT NULL" in sql
    assert rows[0]["download_count"] == 4


@pytest.mark.asyncio
async def test_list_base_post_ids_skips_unpublished() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 4}])
    pool = _fake_pool(conn)

    ids = await base_storage.list_base_post_ids(pool)

    assert ids == [1, 4]
    assert "message_id IS NOT NULL" in conn.fetch.await_args.args[0]
