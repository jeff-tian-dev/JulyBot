"""Unit tests for /ticket (discord_bot.commands.ticket_commands) and the
Tickets field of /setup status."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import setup_commands
from discord_bot.commands import ticket_commands as tc
from modules.tickets import images, render, service, storage
from modules.tickets.render import TicketError

GUILD_ID = 7


def _interaction(custom_id: str | None = None) -> MagicMock:
    inter = MagicMock()
    inter.id = 123456
    inter.guild.id = GUILD_ID
    inter.author.id = 555
    inter.data.custom_id = custom_id
    inter.response.send_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.response.is_done = MagicMock(return_value=False)
    inter.edit_original_response = AsyncMock()
    inter.followup.send = AsyncMock()
    return inter


def _cog() -> tc.TicketCommands:
    cog = tc.TicketCommands(MagicMock())
    cog.bot.pool = "pool"
    return cog


def _ticket(**overrides) -> dict:
    row = {"id": 11, "guild_id": GUILD_ID, "closed_at": None, "channel_id": 5000, "opener_id": 1}
    row.update(overrides)
    return row


def _type(**overrides) -> dict:
    row = {
        "id": 3, "guild_id": GUILD_ID, "name": "Support", "emoji": "🎫",
        "title": "Hi", "body": "Tell us", "image": None, "removed_at": None,
    }
    row.update(overrides)
    return row


def _attachment(filename="banner.png", *, content_type="image/png", size=1000) -> MagicMock:
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.size = size
    attachment.read = AsyncMock(return_value=b"png")
    return attachment


def _sent_text(inter) -> str:
    call = inter.response.send_message.await_args
    return call.args[0] if call.args else call.kwargs.get("content", "")


def _edited_text(inter) -> str:
    return inter.edit_original_response.await_args.kwargs["content"]


# --- button routing -----------------------------------------------------------


@pytest.mark.asyncio
async def test_listener_ignores_other_features_buttons() -> None:
    inter = _interaction("basepost:fetch:5")
    handler = AsyncMock()
    with patch.dict(tc.BUTTON_HANDLERS, {"fetch": handler, "open": handler}):
        await _cog().on_button_click(inter)
    handler.assert_not_awaited()
    inter.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_listener_routes_by_the_clicked_buttons_id() -> None:
    inter = _interaction("ticket:close:11")
    handler = AsyncMock()
    with patch.dict(tc.BUTTON_HANDLERS, {"close": handler}):
        await _cog().on_button_click(inter)
    handler.assert_awaited_once_with(inter, 11)


@pytest.mark.asyncio
async def test_a_crashing_button_still_answers_the_click() -> None:
    inter = _interaction("ticket:open:3")
    with patch.dict(tc.BUTTON_HANDLERS, {"open": AsyncMock(side_effect=RuntimeError("boom"))}):
        await _cog().on_button_click(inter)
    assert "went wrong" in _sent_text(inter)


# --- opening ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_open_defers_then_links_the_new_ticket() -> None:
    inter = _interaction()
    inter.bot.pool = "pool"
    channel = MagicMock(mention="<#5000>")
    result = service.OpenResult(channel=channel, created=True)
    with patch.object(service, "open_ticket", AsyncMock(return_value=result)) as open_ticket:
        await tc.on_open(inter, 3)

    # Private "thinking" reply first: creating a channel can take over 3s.
    inter.response.defer.assert_awaited_once_with(with_message=True, ephemeral=True)
    open_ticket.assert_awaited_once_with("pool", inter.guild, inter.author, 3)
    assert _edited_text(inter) == "Your ticket is open: <#5000>"


@pytest.mark.asyncio
async def test_open_points_at_the_ticket_they_already_have() -> None:
    inter = _interaction()
    result = service.OpenResult(channel=MagicMock(mention="<#5000>"), created=False)
    with patch.object(service, "open_ticket", AsyncMock(return_value=result)):
        await tc.on_open(inter, 3)
    assert "already have this ticket open: <#5000>" in _edited_text(inter)


@pytest.mark.asyncio
async def test_open_shows_the_reason_it_could_not() -> None:
    inter = _interaction()
    with patch.object(service, "open_ticket", AsyncMock(side_effect=TicketError("Tickets are full"))):
        await tc.on_open(inter, 3)
    assert _edited_text(inter) == "⚠️ Tickets are full"


# --- closing ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_close_is_refused_for_the_opener_and_other_members() -> None:
    inter = _interaction()
    with patch.object(storage, "get_ticket", AsyncMock(return_value=_ticket())), patch.object(
        service, "can_close", AsyncMock(return_value=False)
    ):
        await tc.on_close(inter, 11)
    assert "Only staff" in _sent_text(inter)
    assert "components" not in inter.response.send_message.await_args.kwargs


@pytest.mark.asyncio
async def test_close_asks_staff_to_confirm_privately() -> None:
    inter = _interaction()
    with patch.object(storage, "get_ticket", AsyncMock(return_value=_ticket())), patch.object(
        service, "can_close", AsyncMock(return_value=True)
    ), patch.object(service, "close_ticket", AsyncMock()) as close_ticket:
        await tc.on_close(inter, 11)

    close_ticket.assert_not_awaited()  # nothing is deleted on the first click
    kwargs = inter.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["components"][0].children[0].custom_id == "ticket:confirm:11"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ticket",
    [None, _ticket(guild_id=999), _ticket(closed_at="2026-11-16")],
    ids=["unknown", "another server's", "closed"],
)
async def test_close_on_a_ticket_that_is_not_open_here(ticket) -> None:
    inter = _interaction()
    with patch.object(storage, "get_ticket", AsyncMock(return_value=ticket)), patch.object(
        service, "can_close", AsyncMock(return_value=True)
    ) as can_close:
        await tc.on_close(inter, 11)
    assert "already closed" in _sent_text(inter)
    can_close.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_rechecks_staff_before_closing() -> None:
    inter = _interaction()
    with patch.object(service, "can_close", AsyncMock(return_value=False)), patch.object(
        service, "close_ticket", AsyncMock()
    ) as close_ticket:
        await tc.on_confirm(inter, 11)
    close_ticket.assert_not_awaited()
    assert "Only staff" in _sent_text(inter)


@pytest.mark.asyncio
async def test_confirm_closes_and_reports_a_failure() -> None:
    inter = _interaction()
    inter.bot.pool = "pool"
    with patch.object(service, "can_close", AsyncMock(return_value=True)), patch.object(
        service, "close_ticket", AsyncMock()
    ) as close_ticket:
        await tc.on_confirm(inter, 11)
    close_ticket.assert_awaited_once_with("pool", inter.guild, 11, inter.author)
    # The confirm button is removed so it can't be pressed twice.
    assert inter.response.edit_message.await_args.kwargs["components"] == []
    inter.edit_original_response.assert_not_awaited()

    inter = _interaction()
    with patch.object(service, "can_close", AsyncMock(return_value=True)), patch.object(
        service, "close_ticket", AsyncMock(side_effect=TicketError("still open"))
    ):
        await tc.on_confirm(inter, 11)
    assert _edited_text(inter) == "⚠️ still open"


@pytest.mark.asyncio
async def test_a_ticket_channel_deleted_by_hand_closes_its_ticket() -> None:
    channel = MagicMock()
    channel.id = 5000
    with patch.object(storage, "close_by_channel", AsyncMock(return_value=_ticket())) as close:
        await _cog().on_guild_channel_delete(channel)
    close.assert_awaited_once_with("pool", 5000)


# --- /ticket setup ------------------------------------------------------------


def _places(*, log_public=False):
    channel = MagicMock(id=10, mention="<#10>")
    category = MagicMock(id=20)
    category.name = "Tickets"
    log = MagicMock(id=30, mention="<#30>")
    log.permissions_for = MagicMock(return_value=SimpleNamespace(view_channel=log_public))
    return channel, category, log


@pytest.mark.asyncio
async def test_setup_refuses_and_lists_what_the_bot_is_missing() -> None:
    inter = _interaction()
    channel, category, log = _places()
    cog = _cog()
    with patch.object(service, "setup_problems", MagicMock(return_value=["My role needs Manage Channels."])), patch.object(
        storage, "set_locations", AsyncMock()
    ) as save:
        await cog.setup_cmd.callback(cog, inter, channel=channel, category=category, log_channel=log)
    save.assert_not_awaited()
    assert "- My role needs Manage Channels." in _sent_text(inter)


@pytest.mark.asyncio
async def test_setup_saves_the_three_places_for_this_server() -> None:
    inter = _interaction()
    channel, category, log = _places()
    cog = _cog()
    with patch.object(service, "setup_problems", MagicMock(return_value=[])), patch.object(
        storage, "get_config", AsyncMock(return_value=None)
    ), patch.object(storage, "set_locations", AsyncMock()) as save:
        await cog.setup_cmd.callback(cog, inter, channel=channel, category=category, log_channel=log)

    assert save.await_args.args == ("pool", GUILD_ID)
    assert save.await_args.kwargs == {"panel_channel_id": 10, "category_id": 20, "log_channel_id": 30}
    text = _sent_text(inter)
    assert "/ticket type add" in text and "Everyone can see" not in text


@pytest.mark.asyncio
async def test_setup_warns_when_transcripts_would_be_public() -> None:
    inter = _interaction()
    channel, category, log = _places(log_public=True)
    cog = _cog()
    with patch.object(service, "setup_problems", MagicMock(return_value=[])), patch.object(
        storage, "get_config", AsyncMock(return_value=None)
    ), patch.object(storage, "set_locations", AsyncMock()):
        await cog.setup_cmd.callback(cog, inter, channel=channel, category=category, log_channel=log)
    assert "Everyone can see <#30>" in _sent_text(inter)


@pytest.mark.asyncio
async def test_setup_says_the_old_panel_is_left_behind_when_the_channel_moves() -> None:
    inter = _interaction()
    channel, category, log = _places()
    cog = _cog()
    before = {"panel_message_id": 555, "panel_channel_id": 99}
    with patch.object(service, "setup_problems", MagicMock(return_value=[])), patch.object(
        storage, "get_config", AsyncMock(return_value=before)
    ), patch.object(storage, "set_locations", AsyncMock()):
        await cog.setup_cmd.callback(cog, inter, channel=channel, category=category, log_channel=log)
    assert "old panel in <#99>" in _sent_text(inter)


# --- images -------------------------------------------------------------------


def test_check_image_rejects_non_images_and_oversized_files() -> None:
    tc.check_image(None)
    tc.check_image(_attachment())
    with pytest.raises(TicketError, match="supported image"):
        tc.check_image(_attachment("notes.pdf", content_type="application/pdf"))
    with pytest.raises(TicketError, match="supported image"):
        tc.check_image(_attachment("photo.heic", content_type="image/heic"))
    with pytest.raises(TicketError, match="too big"):
        tc.check_image(_attachment(size=images.MAX_IMAGE_BYTES + 1))


@pytest.mark.asyncio
async def test_apply_image_saves_replaces_removes_or_keeps() -> None:
    with patch.object(images, "save", AsyncMock(return_value="type-3.png")) as save, patch.object(
        images, "delete", AsyncMock()
    ) as delete:
        # A new upload with a different extension replaces the old file.
        assert await tc.apply_image(GUILD_ID, "type-3", "type-3.jpg", _attachment(), False) == "type-3.png"
        save.assert_awaited_once_with(GUILD_ID, "type-3", ".png", b"png")
        delete.assert_awaited_once_with(GUILD_ID, "type-3.jpg")

        delete.reset_mock()
        assert await tc.apply_image(GUILD_ID, "type-3", "type-3.png", None, True) is None
        delete.assert_awaited_once_with(GUILD_ID, "type-3.png")

        delete.reset_mock()
        assert await tc.apply_image(GUILD_ID, "type-3", "type-3.png", None, False) == "type-3.png"
        delete.assert_not_awaited()


# --- /ticket panel ------------------------------------------------------------


@pytest.mark.asyncio
async def test_panel_needs_setup_first() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "get_config", AsyncMock(return_value=None)):
        await cog.panel.callback(cog, inter, image=None, remove_image=False)
    inter.response.send_modal.assert_not_awaited()
    assert "/ticket setup" in _sent_text(inter)


@pytest.mark.asyncio
async def test_panel_opens_a_pre_filled_form_then_saves_and_publishes() -> None:
    inter = _interaction()
    cog = _cog()
    config = {"panel_title": "Old title", "panel_body": "Old body", "panel_image": None}
    with patch.object(storage, "get_config", AsyncMock(return_value=config)):
        await cog.panel.callback(cog, inter, image=None, remove_image=False)

    # The form is the first response; nothing slow may come before it.
    inter.response.defer.assert_not_awaited()
    modal = inter.response.send_modal.await_args.args[0]
    title_input, body_input = modal.components[0].children[0], modal.components[1].children[0]
    assert (title_input.value, body_input.value) == ("Old title", "Old body")
    assert body_input.style is disnake.TextInputStyle.paragraph  # real line breaks

    submit = _interaction()
    submit.text_values = {"title": "Open a ticket", "body": "- one\n- two"}
    published = MagicMock(jump_url="https://discord.com/x")
    with patch.object(storage, "set_panel_content", AsyncMock()) as save, patch.object(
        service, "publish_panel", AsyncMock(return_value=published)
    ) as publish:
        await modal.callback(submit)

    submit.response.defer.assert_awaited_once_with(ephemeral=True)
    assert save.await_args.kwargs == {"title": "Open a ticket", "body": "- one\n- two", "image": None}
    publish.assert_awaited_once_with("pool", submit.guild)
    assert "https://discord.com/x" in _edited_text(submit)


@pytest.mark.asyncio
async def test_panel_form_reports_bad_input_instead_of_timing_out() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "get_config", AsyncMock(return_value={})):
        await cog.panel.callback(cog, inter, image=None, remove_image=False)
    modal = inter.response.send_modal.await_args.args[0]

    submit = _interaction()
    submit.text_values = {"title": "", "body": "  "}
    with patch.object(storage, "set_panel_content", AsyncMock()) as save:
        await modal.callback(submit)
    save.assert_not_awaited()
    assert _edited_text(submit).startswith("⚠️")


@pytest.mark.asyncio
async def test_panel_rejects_a_bad_image_before_the_admin_writes_anything() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "get_config", AsyncMock(return_value={})):
        await cog.panel.callback(
            cog, inter, image=_attachment("a.pdf", content_type="application/pdf"), remove_image=False
        )
    inter.response.send_modal.assert_not_awaited()
    assert "supported image" in _sent_text(inter)


# --- /ticket type -------------------------------------------------------------


@pytest.mark.asyncio
async def test_type_add_refuses_a_duplicate_name_before_opening_the_form() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "list_types", AsyncMock(return_value=[_type()])):
        await cog.type_add.callback(cog, inter, name="support", emoji=None, image=None)
    inter.response.send_modal.assert_not_awaited()
    assert "already a ticket type" in _sent_text(inter)


@pytest.mark.asyncio
async def test_type_add_refuses_past_the_button_limit() -> None:
    inter = _interaction()
    cog = _cog()
    full = [_type(id=i, name=f"T{i}") for i in range(render.MAX_TICKET_TYPES)]
    with patch.object(storage, "list_types", AsyncMock(return_value=full)):
        await cog.type_add.callback(cog, inter, name="One more", emoji=None, image=None)
    inter.response.send_modal.assert_not_awaited()
    assert str(render.MAX_TICKET_TYPES) in _sent_text(inter)


@pytest.mark.asyncio
async def test_type_add_refuses_a_word_as_the_emoji() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "list_types", AsyncMock(return_value=[])):
        await cog.type_add.callback(cog, inter, name="Support", emoji="ticket", image=None)
    inter.response.send_modal.assert_not_awaited()
    assert "emoji" in _sent_text(inter)


@pytest.mark.asyncio
async def test_type_add_creates_the_type_updates_the_panel_and_previews_it() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "list_types", AsyncMock(return_value=[])):
        await cog.type_add.callback(cog, inter, name=" Support ", emoji="🎫", image=_attachment())
    modal = inter.response.send_modal.await_args.args[0]

    submit = _interaction()
    submit.text_values = {"title": "Hi", "body": "Tell us"}
    created = _type()
    with patch.object(storage, "create_type", AsyncMock(return_value=created)) as create, patch.object(
        images, "save", AsyncMock(return_value="type-3.png")
    ) as save, patch.object(images, "delete", AsyncMock()), patch.object(
        storage, "update_type", AsyncMock(return_value=_type(image="type-3.png"))
    ) as update, patch.object(service, "refresh_panel", AsyncMock(return_value=None)) as refresh, patch.object(
        images, "load", AsyncMock(return_value=b"png")
    ):
        await modal.callback(submit)

    assert create.await_args.kwargs == {
        "name": "Support", "emoji": "🎫", "title": "Hi", "body": "Tell us", "created_by": 555,
    }
    # The banner file is named after the new type's id, so it is saved second.
    save.assert_awaited_once_with(GUILD_ID, "type-3", ".png", b"png")
    assert update.await_args.kwargs["image"] == "type-3.png"
    refresh.assert_awaited_once_with("pool", submit.guild)
    assert "Added **Support**" in _edited_text(submit)
    preview = submit.followup.send.await_args.kwargs
    assert preview["ephemeral"] is True
    assert preview["file"].filename == "type-3.png"
    assert preview["components"][0].children[-1].children[0].disabled is True


@pytest.mark.asyncio
async def test_type_add_passes_on_why_the_panel_was_not_updated() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "list_types", AsyncMock(return_value=[])):
        await cog.type_add.callback(cog, inter, name="Support", emoji=None, image=None)
    modal = inter.response.send_modal.await_args.args[0]

    submit = _interaction()
    submit.text_values = {"title": "Hi", "body": ""}
    with patch.object(storage, "create_type", AsyncMock(return_value=_type())), patch.object(
        service, "refresh_panel", AsyncMock(return_value="There's no panel yet — post one with `/ticket panel`.")
    ), patch.object(images, "load", AsyncMock(return_value=None)):
        await modal.callback(submit)
    assert "/ticket panel" in _edited_text(submit)


@pytest.mark.asyncio
async def test_type_edit_keeps_what_was_not_changed() -> None:
    inter = _interaction()
    cog = _cog()
    record = _type(image="type-3.png")
    with patch.object(storage, "get_type_by_name", AsyncMock(return_value=record)):
        await cog.type_edit.callback(
            cog, inter, name="Support", new_name=None, emoji=None,
            remove_emoji=False, image=None, remove_image=False,
        )
    modal = inter.response.send_modal.await_args.args[0]
    assert modal.components[0].children[0].value == "Hi"

    submit = _interaction()
    submit.text_values = {"title": "Hello", "body": "New text"}
    with patch.object(storage, "update_type", AsyncMock(return_value=record)) as update, patch.object(
        service, "refresh_panel", AsyncMock(return_value=None)
    ), patch.object(images, "load", AsyncMock(return_value=None)), patch.object(
        images, "delete", AsyncMock()
    ) as delete:
        await modal.callback(submit)

    assert update.await_args.args == ("pool", 3)
    assert update.await_args.kwargs == {
        "name": "Support", "emoji": "🎫", "title": "Hello", "body": "New text", "image": "type-3.png",
    }
    delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_type_edit_can_rename_and_strip_the_emoji_and_banner() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "get_type_by_name", AsyncMock(return_value=_type(image="type-3.png"))):
        await cog.type_edit.callback(
            cog, inter, name="Support", new_name="Help", emoji=None,
            remove_emoji=True, image=None, remove_image=True,
        )
    modal = inter.response.send_modal.await_args.args[0]

    submit = _interaction()
    submit.text_values = {"title": "Hi", "body": "Tell us"}
    with patch.object(storage, "update_type", AsyncMock(return_value=_type(name="Help"))) as update, patch.object(
        service, "refresh_panel", AsyncMock(return_value=None)
    ), patch.object(images, "load", AsyncMock(return_value=None)), patch.object(
        images, "delete", AsyncMock()
    ) as delete:
        await modal.callback(submit)

    assert update.await_args.kwargs["name"] == "Help"
    assert update.await_args.kwargs["emoji"] is None
    assert update.await_args.kwargs["image"] is None
    delete.assert_awaited_once_with(GUILD_ID, "type-3.png")


@pytest.mark.asyncio
async def test_type_edit_unknown_name() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "get_type_by_name", AsyncMock(return_value=None)):
        await cog.type_edit.callback(
            cog, inter, name="Nope", new_name=None, emoji=None,
            remove_emoji=False, image=None, remove_image=False,
        )
    inter.response.send_modal.assert_not_awaited()
    assert "no ticket type" in _sent_text(inter)


@pytest.mark.asyncio
async def test_type_remove_takes_the_button_off_and_deletes_the_banner() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "get_type_by_name", AsyncMock(return_value=_type(image="type-3.png"))), patch.object(
        storage, "remove_type", AsyncMock(return_value=True)
    ) as remove, patch.object(images, "delete", AsyncMock()) as delete, patch.object(
        service, "refresh_panel", AsyncMock(return_value=None)
    ) as refresh:
        await cog.type_remove.callback(cog, inter, name="Support")

    remove.assert_awaited_once_with("pool", GUILD_ID, 3)
    delete.assert_awaited_once_with(GUILD_ID, "type-3.png")
    refresh.assert_awaited_once()
    assert "already open stay" in _edited_text(inter)


@pytest.mark.asyncio
async def test_type_list() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "list_types", AsyncMock(return_value=[_type(image="type-3.png")])):
        await cog.type_list.callback(cog, inter)
    assert _sent_text(inter) == "- 🎫 **Support** — Hi, has a banner"

    inter = _interaction()
    with patch.object(storage, "list_types", AsyncMock(return_value=[])):
        await cog.type_list.callback(cog, inter)
    assert "/ticket type add" in _sent_text(inter)


@pytest.mark.asyncio
async def test_type_autocomplete_only_offers_this_servers_types() -> None:
    inter = _interaction()
    inter.bot.pool = "pool"
    with patch.object(
        storage, "list_types", AsyncMock(return_value=[_type(), _type(id=4, name="Purchase")])
    ) as list_types:
        assert await tc._type_autocomplete(inter, "pur") == ["Purchase"]
    assert list_types.await_args.args == ("pool", GUILD_ID)


# --- /ticket role -------------------------------------------------------------


def _role(role_id=900, *, default=False, mentionable=True) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    role.mentionable = mentionable
    role.is_default.return_value = default
    return role


@pytest.mark.asyncio
async def test_role_add_refuses_everyone() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "add_staff_role", AsyncMock()) as add:
        await cog.role_add.callback(cog, inter, role=_role(default=True))
    add.assert_not_awaited()
    assert "private" in _sent_text(inter)


@pytest.mark.asyncio
async def test_role_add_saves_and_says_open_tickets_are_not_changed() -> None:
    inter = _interaction()
    inter.guild.me.guild_permissions.mention_everyone = False
    cog = _cog()
    with patch.object(storage, "list_staff_roles", AsyncMock(return_value=[])), patch.object(
        storage, "add_staff_role", AsyncMock(return_value=True)
    ) as add:
        await cog.role_add.callback(cog, inter, role=_role(mentionable=False))

    add.assert_awaited_once_with("pool", GUILD_ID, 900, 555)
    text = _sent_text(inter)
    assert "already open aren't changed" in text
    assert "isn't mentionable" in text  # the ping wouldn't notify anyone
    assert inter.response.send_message.await_args.kwargs["allowed_mentions"].roles is False


@pytest.mark.asyncio
async def test_role_add_is_capped() -> None:
    inter = _interaction()
    cog = _cog()
    full = list(range(1, render.MAX_STAFF_ROLES + 1))
    with patch.object(storage, "list_staff_roles", AsyncMock(return_value=full)), patch.object(
        storage, "add_staff_role", AsyncMock()
    ) as add:
        await cog.role_add.callback(cog, inter, role=_role())
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_remove_and_list() -> None:
    inter = _interaction()
    cog = _cog()
    with patch.object(storage, "remove_staff_role", AsyncMock(return_value=True)) as remove:
        await cog.role_remove.callback(cog, inter, role=_role())
    remove.assert_awaited_once_with("pool", GUILD_ID, 900)
    assert "no longer ticket staff" in _sent_text(inter)

    inter = _interaction()
    with patch.object(storage, "list_staff_roles", AsyncMock(return_value=[900, 901])):
        await cog.role_list.callback(cog, inter)
    assert _sent_text(inter) == "Ticket staff: <@&900>, <@&901>"

    inter = _interaction()
    with patch.object(storage, "list_staff_roles", AsyncMock(return_value=[])):
        await cog.role_list.callback(cog, inter)
    assert "only admins" in _sent_text(inter)


# --- /setup status ------------------------------------------------------------


def _status_guild(channels=()) -> MagicMock:
    guild = MagicMock()
    by_id = {c.id: c for c in channels}
    guild.get_channel = MagicMock(side_effect=lambda cid: by_id.get(cid))
    return guild


def test_status_points_a_fresh_server_at_ticket_setup() -> None:
    assert "/ticket setup" in setup_commands.tickets_line(_status_guild(), None)
    assert "/ticket setup" in setup_commands.tickets_line(_status_guild(), {"config": None})


def test_status_shows_a_working_ticket_setup() -> None:
    channel, category, log = _places()
    tickets = {
        "config": {"panel_channel_id": 10, "category_id": 20, "log_channel_id": 30, "panel_message_id": 5},
        "staff_role_ids": [900],
        "type_count": 2,
        "open_count": 12,
    }
    with patch.object(service, "setup_problems", MagicMock(return_value=[])):
        line = setup_commands.tickets_line(_status_guild([channel, category, log]), tickets)
    assert "✅ Buttons in <#10>" in line and "**Tickets**" in line and "<#30>" in line
    assert "2 ticket types" in line
    assert "Staff: <@&900>" in line
    assert "Open tickets: 12" in line
    assert "⚠️" not in line


def test_status_flags_everything_unfinished() -> None:
    channel, category, log = _places()
    tickets = {
        "config": {"panel_channel_id": 10, "category_id": 20, "log_channel_id": 30, "panel_message_id": None},
        "staff_role_ids": [],
        "type_count": 0,
        "open_count": 0,
    }
    with patch.object(service, "setup_problems", MagicMock(return_value=["My role needs Manage Channels."])):
        line = setup_commands.tickets_line(_status_guild([channel, category, log]), tickets)
    assert "⚠️ My role needs Manage Channels." in line
    assert "/ticket panel" in line and "/ticket type add" in line and "/ticket role add" in line


def test_status_summarises_a_long_staff_list_to_fit_an_embed_field() -> None:
    channel, category, log = _places()
    tickets = {
        "config": {"panel_channel_id": 10, "category_id": 20, "log_channel_id": 30, "panel_message_id": 5},
        "staff_role_ids": [10**18 + i for i in range(render.MAX_STAFF_ROLES)],
        "type_count": 1,
        "open_count": 0,
    }
    problems = ["My role needs these server-wide (Server Settings → Roles): " + ", ".join(["Manage Channels"] * 6) + "."]
    with patch.object(service, "setup_problems", MagicMock(return_value=problems)):
        line = setup_commands.tickets_line(_status_guild([channel, category, log]), tickets)
    assert f"and {render.MAX_STAFF_ROLES - setup_commands.MAX_STATUS_STAFF_ROLES} more" in line
    assert len(line) <= 1024


def test_status_notices_a_deleted_ticket_channel() -> None:
    channel, category, _log = _places()
    tickets = {
        "config": {"panel_channel_id": 10, "category_id": 20, "log_channel_id": 30, "panel_message_id": 5},
        "staff_role_ids": [], "type_count": 1, "open_count": 0,
    }
    line = setup_commands.tickets_line(_status_guild([channel, category]), tickets)
    assert "was deleted" in line
