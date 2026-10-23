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
