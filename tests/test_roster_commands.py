"""Unit tests for the /roster view Cog layer: public posting and owner-only column buttons."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import roster_commands
from modules.roster.render import VIEW_COLUMNS, VIEW_HIDDEN

OWNER = 555


def _view() -> roster_commands.RosterView:
    return roster_commands.RosterView("Main", [], VIEW_COLUMNS, VIEW_HIDDEN, owner_id=OWNER)


def _click(author_id: int):
    inter = MagicMock()
    inter.author.id = author_id
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    return inter


@pytest.mark.asyncio
async def test_owner_can_toggle_a_column() -> None:
    view = _view()
    button = view.children[0]
    key = VIEW_COLUMNS[0]
    before = view.show[key]
    inter = _click(OWNER)

    with patch.object(roster_commands, "render_embed", return_value=disnake.Embed()):
        await button.callback(inter)

    assert view.show[key] is not before
    inter.response.edit_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_other_viewers_cannot_toggle_the_public_roster() -> None:
    """The message is public, so a stranger's click would change it for everyone."""
    view = _view()
    key = VIEW_COLUMNS[0]
    before = view.show[key]
    inter = _click(999)

    await view.children[0].callback(inter)

    assert view.show[key] is before
    inter.response.edit_message.assert_not_awaited()
    assert inter.response.send_message.call_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_timeout_strips_the_buttons_from_the_message() -> None:
    view = _view()
    view.message = MagicMock()
    view.message.edit = AsyncMock()

    await view.on_timeout()

    view.message.edit.assert_awaited_once_with(view=None)


@pytest.mark.asyncio
async def test_timeout_tolerates_a_deleted_message() -> None:
    view = _view()
    view.message = MagicMock()
    view.message.edit = AsyncMock(
        side_effect=disnake.NotFound(MagicMock(status=404), "gone")
    )

    await view.on_timeout()  # must not raise


def _command_inter():
    inter = MagicMock()
    inter.guild.id = 1
    inter.author.id = OWNER
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock(return_value=MagicMock())
    inter.delete_original_response = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


@pytest.mark.asyncio
async def test_view_is_posted_publicly() -> None:
    cog = roster_commands.RosterCommands(MagicMock())
    inter = _command_inter()
    built = {"name": "Main", "watched": False, "members": []}

    with patch.object(roster_commands.storage, "build_roster_view", new=AsyncMock(return_value=built)):
        await cog.view.callback(cog, inter, name="Main")

    assert inter.response.defer.await_args.kwargs.get("ephemeral", False) is False
    inter.edit_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_tracked_roster_view_has_an_inout_button() -> None:
    cog = roster_commands.RosterCommands(MagicMock())
    inter = _command_inter()
    inter.guild.get_member.return_value.display_name = "Jeff"
    built = {
        "name": "Main",
        "watched": True,
        "members": [
            {
                "discord_id": 1,
                "coc_tag": "#ABC",
                "coc_name": "Bob",
                "coc_api_ok": True,
                "trophies": 5000,
                "in_main_clan": False,
                "daily_seconds": 7200,
            }
        ],
    }

    with patch.object(roster_commands.storage, "build_roster_view", new=AsyncMock(return_value=built)):
        await cog.view.callback(cog, inter, name="Main")

    posted = inter.edit_original_response.await_args.kwargs["view"]
    assert [child.label for child in posted.children] == ["Trophies", "In/Out", "CoC Tag", "IGN", "Discord"]
    assert "❌" in posted.embed().description
    inout = next(child for child in posted.children if child.label == "In/Out")
    assert inout.style == disnake.ButtonStyle.success


@pytest.mark.asyncio
async def test_unknown_roster_error_is_shown_only_to_the_invoker() -> None:
    cog = roster_commands.RosterCommands(MagicMock())
    inter = _command_inter()

    with patch.object(
        roster_commands.storage, "build_roster_view",
        new=AsyncMock(side_effect=ValueError("No roster named **Nope**.")),
    ):
        await cog.view.callback(cog, inter, name="Nope")

    inter.delete_original_response.assert_awaited_once()
    assert inter.followup.send.await_args.kwargs["ephemeral"] is True
    inter.edit_original_response.assert_not_awaited()


def _long_view() -> roster_commands.RosterView:
    rows = [
        {"trophies": "5000", "coctag": f"#TAG{i:05d}", "ign": f"Player{i:05d}", "discord": f"user{i:05d}"}
        for i in range(150)
    ]
    return roster_commands.RosterView("Main", rows, VIEW_COLUMNS, VIEW_HIDDEN, owner_id=OWNER)


@pytest.mark.asyncio
async def test_short_roster_has_no_page_buttons() -> None:
    labels = [child.label for child in _view().children]
    assert "Next ▶" not in labels and "◀ Prev" not in labels


@pytest.mark.asyncio
async def test_long_roster_pages_forward_and_back() -> None:
    view = _long_view()
    assert view.page_count() > 1
    assert view.prev_button in view.children and view.prev_button.disabled
    first = view.embed().description

    inter = _click(OWNER)
    await view.next_button.callback(inter)
    assert view.page == 1
    assert view.embed().description != first
    assert not view.prev_button.disabled
    inter.response.edit_message.assert_awaited_once()

    await view.prev_button.callback(_click(OWNER))
    assert view.page == 0


@pytest.mark.asyncio
async def test_other_viewers_cannot_page_the_public_roster() -> None:
    view = _long_view()
    inter = _click(999)
    await view.next_button.callback(inter)
    assert view.page == 0
    inter.response.edit_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_hiding_columns_can_drop_the_page_buttons() -> None:
    """Fewer columns -> shorter rows -> fewer pages; the page index is clamped."""
    rows = [
        {"trophies": "5000", "coctag": "#TAG00000", "ign": f"Player{i:05d}", "discord": "someuser12345"}
        for i in range(80)
    ]
    view = roster_commands.RosterView("Main", rows, VIEW_COLUMNS, (), owner_id=OWNER)
    assert view.page_count() > 1
    view.page = 1
    # Hide every column but trophies, one click each.
    for button in [c for c in view.children if c.custom_id in ("col_coctag", "col_ign", "col_discord")]:
        await button.callback(_click(OWNER))
    assert view.page_count() == 1
    assert view.page == 0
    assert view.next_button not in view.children
