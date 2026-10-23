"""Unit tests for /postbase's components-v2 layout and its stateless button listener."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest
from disnake.ui.action_row import normalize_components_to_dict

from discord_bot.commands import base_post_commands as bp
from modules.announce import base_poster
from modules.announce.poster import PostError


def _record(**overrides):
    record = {
        "id": 5,
        "author_id": 1,
        "title": "Tap 6.0",
        "cc": "x2 HH",
        "description": "notes",
        "link": "https://link.clashofclans.com/en?action=OpenLayout&id=x",
        "image_filename": "stored.png",
        "image_url": "https://cdn.discordapp.com/attachments/1/2/stored.png",
        "stats_admin_only": False,
    }
    record.update(overrides)
    return record


def _message(*, v2: bool, filename: str | None = "live.png"):
    message = MagicMock()
    message.flags = disnake.MessageFlags(is_components_v2=v2)
    attachment = MagicMock()
    attachment.filename = filename
    message.attachments = [attachment] if filename else []
    message.edit = AsyncMock()
    return message


def _walk(components):
    """Every component in a tree, depth-first."""
    for c in components:
        yield c
        yield from _walk(getattr(c, "children", None) or getattr(c, "items", None) or [])


# --- text sections --------------------------------------------------------------


def test_sections_put_the_title_in_a_markdown_heading() -> None:
    heading, body = base_poster.build_post_sections(title="Tap 6.0", cc="x2 HH", description="notes")
    assert heading == "## Tap 6.0"
    assert body == "**CC:**\nx2 HH\n\nnotes"


def test_sections_skip_blank_parts() -> None:
    assert base_poster.build_post_sections(title=None, cc=None, description="notes") == (None, "notes")
    assert base_poster.build_post_sections(title=None, cc=None, description=None) == (None, None)


def test_sections_reject_overflowing_the_components_text_limit() -> None:
    with pytest.raises(PostError):
        base_poster.build_post_sections(
            title="t", cc=None, description="d" * base_poster.MAX_COMPONENTS_TEXT_LENGTH
        )


# --- the post layout --------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_is_one_pink_container_with_a_full_width_gallery() -> None:
    components = bp.build_post_components(_record(), download_count=3, image_filename="base.png")

    assert len(components) == 1
    container = components[0]
    assert isinstance(container, disnake.ui.Container)
    assert container.accent_colour.value == base_poster.BASE_POST_COLOUR == 0xEC4899

    kinds = [type(c).__name__ for c in container.children]
    assert kinds == ["TextDisplay", "Separator", "TextDisplay", "MediaGallery", "ActionRow"]
    gallery = container.children[3]
    assert gallery.items[0].media.url == "attachment://base.png"


@pytest.mark.asyncio
async def test_post_serialises_as_components_v2() -> None:
    """disnake must accept the tree and flag it v2 — the flag is what lets the
    container render, and a bad nesting would only fail live."""
    payload, is_v2 = normalize_components_to_dict(
        bp.build_post_components(_record(), download_count=0, image_filename="base.png")
    )
    assert is_v2 is True
    assert payload[0]["accent_color"] == 0xEC4899


@pytest.mark.asyncio
async def test_untitled_post_has_no_heading_or_separator() -> None:
    container = bp.build_post_components(
        _record(title=None), download_count=0, image_filename="base.png"
    )[0]
    kinds = [type(c).__name__ for c in container.children]
    assert kinds == ["TextDisplay", "MediaGallery", "ActionRow"]


@pytest.mark.asyncio
async def test_buttons_carry_routable_custom_ids_and_the_count() -> None:
    container = bp.build_post_components(_record(), download_count=7, image_filename="x.png")[0]
    buttons = list(container.children[-1].children)
    assert [b.custom_id for b in buttons] == ["basepost:fetch:5", "basepost:edit:5", "basepost:downloads:5"]
    assert buttons[2].label == "7 Downloads"


@pytest.mark.asyncio
async def test_admin_only_stats_never_show_the_count() -> None:
    container = bp.build_post_components(
        _record(stats_admin_only=True), download_count=7, image_filename="x.png"
    )[0]
    label = container.children[-1].children[2].label
    assert label == "Downloads"
    assert "7" not in label


# --- routing --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("custom_id", "expected"),
    [
        ("basepost:fetch:57", ("fetch", 57)),
        ("basepost:downloads:3", ("downloads", 3)),
        ("purchase:confirm:7", None),  # another feature's button
        ("basepost:fetch:abc", None),
        ("garbage", None),
        (None, None),
    ],
)
def test_parse_custom_id(custom_id, expected) -> None:
    assert bp.parse_custom_id(custom_id) == expected


@pytest.mark.asyncio
async def test_listener_routes_by_custom_id_to_the_clicked_post() -> None:
    """The post id comes from the clicked button — the rule every persistent
    button in this repo follows."""
    cog = bp.BasePostCommands(MagicMock())
    inter = MagicMock()
    inter.data.custom_id = "basepost:fetch:57"
    handler = AsyncMock()

    with patch.dict(bp.BUTTON_HANDLERS, {"fetch": handler}):
        await cog.on_button_click(inter)

    handler.assert_awaited_once_with(inter, 57)


@pytest.mark.asyncio
async def test_listener_ignores_other_features_buttons() -> None:
    cog = bp.BasePostCommands(MagicMock())
    inter = MagicMock()
    inter.data.custom_id = "purchase:confirm:7"
    handlers = {k: AsyncMock() for k in bp.BUTTON_HANDLERS}

    with patch.dict(bp.BUTTON_HANDLERS, handlers):
        await cog.on_button_click(inter)

    for h in handlers.values():
        h.assert_not_awaited()


# --- fetch refreshes the count without restyling old posts -------------------------


def _fetch_inter(message):
    inter = MagicMock()
    inter.bot.pool = MagicMock()
    inter.author.id = 42
    inter.message = message
    inter.response.send_modal = AsyncMock()
    inter.response.send_message = AsyncMock()
    return inter


async def _fetch(inter, record):
    with patch.object(bp.base_storage, "get_base_post", new=AsyncMock(return_value=record)), \
         patch.object(bp.base_storage, "record_download", new=AsyncMock(return_value=4)):
        await bp.on_fetch(inter, record["id"])


@pytest.mark.asyncio
async def test_fetch_on_an_old_post_only_swaps_its_buttons() -> None:
    message = _message(v2=False)
    inter = _fetch_inter(message)

    await _fetch(inter, _record())

    inter.response.send_modal.assert_awaited_once()
    kwargs = message.edit.await_args.kwargs
    assert set(kwargs) == {"components"}  # no content/embed: its look is untouched
    assert kwargs["components"][0].children[2].label == "4 Downloads"


@pytest.mark.asyncio
async def test_fetch_on_a_new_post_rebuilds_the_container() -> None:
    message = _message(v2=True)
    inter = _fetch_inter(message)

    await _fetch(inter, _record())

    container = message.edit.await_args.kwargs["components"][0]
    assert isinstance(container, disnake.ui.Container)
    assert container.children[-1].children[2].label == "4 Downloads"


@pytest.mark.asyncio
async def test_fetch_with_admin_only_stats_does_not_edit() -> None:
    message = _message(v2=True)
    inter = _fetch_inter(message)

    await _fetch(inter, _record(stats_admin_only=True))

    message.edit.assert_not_awaited()


# --- editing ---------------------------------------------------------------------


def _modal_inter(message):
    inter = MagicMock()
    inter.text_values = {"title": "Tap 6.1", "cc": "x2 HH", "description": "new notes", "link": ""}
    inter.bot.pool = MagicMock()
    inter.message = message
    inter.response.edit_message = AsyncMock()
    inter.response.send_message = AsyncMock()
    return inter


async def _submit_edit(inter, updated):
    with patch.object(bp.base_storage, "update_base_post", new=AsyncMock(return_value=updated)), \
         patch.object(bp.base_storage, "count_downloads", new=AsyncMock(return_value=3)):
        await bp.BaseEditModal(_record()).callback(inter)


@pytest.mark.asyncio
async def test_editing_a_new_post_rebuilds_it_against_the_live_attachment() -> None:
    inter = _modal_inter(_message(v2=True, filename="live.png"))

    await _submit_edit(inter, _record(title="Tap 6.1", description="new notes"))

    kwargs = inter.response.edit_message.await_args.kwargs
    assert set(kwargs) == {"components"}  # v2 forbids content/embeds
    container = kwargs["components"][0]
    assert container.children[0].content == "## Tap 6.1"
    gallery = next(c for c in container.children if isinstance(c, disnake.ui.MediaGallery))
    # The upload is referenced, never its expiring CDN URL.
    assert gallery.items[0].media.url == "attachment://live.png"
    assert "attachments" not in kwargs and "file" not in kwargs


@pytest.mark.asyncio
async def test_editing_an_old_post_keeps_the_embed_layout() -> None:
    inter = _modal_inter(_message(v2=False, filename="live.png"))

    await _submit_edit(inter, _record(title="Tap 6.1"))

    kwargs = inter.response.edit_message.await_args.kwargs
    assert kwargs["content"] is None  # clears an original plain-message post's text
    assert kwargs["embed"].title == "Tap 6.1"
    assert kwargs["embed"].image.url == "attachment://live.png"
    assert isinstance(kwargs["components"][0], disnake.ui.ActionRow)


@pytest.mark.asyncio
async def test_edit_falls_back_to_the_stored_filename() -> None:
    inter = _modal_inter(_message(v2=True, filename=None))

    await _submit_edit(inter, _record())

    container = inter.response.edit_message.await_args.kwargs["components"][0]
    gallery = next(c for c in container.children if isinstance(c, disnake.ui.MediaGallery))
    assert gallery.items[0].media.url == "attachment://stored.png"
