"""Unit tests for the ticket modules: rendering, opening and closing, the panel.

Discord and the database are both mocked. The SQL itself was checked against a
real Postgres when the feature was written (see the 2026-11-16 CLAUDE.md
entry); these tests pin the behaviour around it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest
from disnake.ui.action_row import normalize_components_to_dict

from database import models
from modules.tickets import images, render, service, storage
from modules.tickets.render import TicketError

GUILD_ID = 7
OPENER_ID = 4242
STAFF_ROLE_ID = 900


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


def _type(**overrides) -> dict:
    row = {
        "id": 3,
        "guild_id": GUILD_ID,
        "name": "Support",
        "emoji": "🎫",
        "title": "How can we help?",
        "body": "**Tell us**\n- what happened\n- when",
        "image": None,
        "removed_at": None,
    }
    row.update(overrides)
    return row


def _ticket(**overrides) -> dict:
    row = {
        "id": 11,
        "guild_id": GUILD_ID,
        "type_id": 3,
        "type_name": "Support",
        "channel_id": 5000,
        "opener_id": OPENER_ID,
        "opened_at": datetime(2026, 11, 16, 12, 0, 0),
        "closed_at": None,
        "closed_by": None,
    }
    row.update(overrides)
    return row


def _config(**overrides) -> dict:
    row = {
        "guild_id": GUILD_ID,
        "panel_channel_id": 10,
        "category_id": 20,
        "log_channel_id": 30,
        "panel_message_id": None,
        "panel_title": "Open a ticket",
        "panel_body": "Pick one below.",
        "panel_image": None,
    }
    row.update(overrides)
    return row


def _walk(components):
    for c in components:
        yield c
        yield from _walk(getattr(c, "children", None) or [])
        if getattr(c, "accessory", None) is not None:  # a section's thumbnail
            yield c.accessory


# --- validation ---------------------------------------------------------------


def test_content_needs_a_title_or_a_message() -> None:
    with pytest.raises(TicketError):
        render.validate_content("  ", "\n")
    assert render.validate_content("  Hello   there ", None) == ("Hello there", None)
    # Line breaks in the body are the admin's formatting and are kept.
    assert render.validate_content(None, " - a\n- b ") == (None, "- a\n- b")


def test_content_rejects_an_overlong_message() -> None:
    with pytest.raises(TicketError):
        render.validate_content("t", "x" * (render.MAX_BODY_LENGTH + 1))


@pytest.mark.parametrize("emoji", ["🎫", "1️⃣", "<:blossom:123456789012345678>", "<a:spin:123456789012345678>"])
def test_emoji_accepts_standard_and_custom(emoji) -> None:
    assert render.validate_emoji(f" {emoji} ") == emoji


@pytest.mark.parametrize("emoji", ["ticket", ":ticket:", "🎫 🎫"])
def test_emoji_rejects_plain_words(emoji) -> None:
    """Discord rejects the whole panel over one bad button emoji."""
    with pytest.raises(TicketError):
        render.validate_emoji(emoji)


def test_emoji_blank_is_none() -> None:
    assert render.validate_emoji(None) is None
    assert render.validate_emoji("  ") is None


LOCAL_SPIN = SimpleNamespace(name="spin", id=111111111111111111, animated=True, available=True)
OTHER_SPIN = SimpleNamespace(name="spin", id=222222222222222222, animated=True, available=True)
OTHER_STAR = SimpleNamespace(name="Star", id=333333333333333333, animated=False, available=True)
LOST = SimpleNamespace(name="lost", id=444444444444444444, animated=False, available=False)
EMOJIS = [LOCAL_SPIN, OTHER_SPIN, OTHER_STAR, LOST]


@pytest.mark.parametrize("typed", [":spin:", "spin", " :spin: "])
def test_resolve_finds_an_emoji_by_name_this_server_first(typed) -> None:
    """Without Nitro, Discord sends an animated/external emoji as plain `:name:`."""
    assert render.resolve_emoji(typed, EMOJIS) == "<a:spin:111111111111111111>"


@pytest.mark.parametrize(
    "typed",
    [
        "<a:spin:222222222222222222>",
        "<:spin:222222222222222222>",  # wrong animated flag is corrected
        "spin:222222222222222222",
        "222222222222222222",
        "https://cdn.discordapp.com/emojis/222222222222222222.gif?size=48",
    ],
)
def test_resolve_accepts_every_way_of_naming_an_id(typed) -> None:
    assert render.resolve_emoji(typed, EMOJIS) == "<a:spin:222222222222222222>"


def test_resolve_name_match_falls_back_to_any_case() -> None:
    assert render.resolve_emoji(":star:", EMOJIS) == "<:Star:333333333333333333>"


@pytest.mark.parametrize("typed", [":nope:", "<:x:999999999999999999>", ":lost:"])
def test_resolve_refuses_an_emoji_the_bot_cant_use(typed) -> None:
    """Discord would reject the whole panel over it."""
    with pytest.raises(TicketError):
        render.resolve_emoji(typed, EMOJIS)


@pytest.mark.parametrize("typed", ["🎫", "1️⃣"])
def test_resolve_passes_standard_emojis_through(typed) -> None:
    assert render.resolve_emoji(typed, EMOJIS) == typed


def test_resolve_still_refuses_plain_words() -> None:
    with pytest.raises(TicketError, match="doesn't look like an emoji"):
        render.resolve_emoji("ticket", EMOJIS)
    assert render.resolve_emoji("  ", EMOJIS) is None


def test_name_is_capped_at_the_button_label_limit() -> None:
    assert render.validate_name("  Buy   a sub ") == "Buy a sub"
    with pytest.raises(TicketError):
        render.validate_name("x" * (render.MAX_NAME_LENGTH + 1))
    with pytest.raises(TicketError):
        render.validate_name(" ")


@pytest.mark.parametrize(
    ("custom_id", "expected"),
    [
        ("ticket:open:3", ("open", 3)),
        ("ticket:close:11", ("close", 11)),
        ("basepost:fetch:3", None),  # another feature's button
        ("ticket:open:abc", None),
        ("ticket:open", None),
        (None, None),
    ],
)
def test_parse_custom_id(custom_id, expected) -> None:
    assert render.parse_custom_id(custom_id) == expected


# --- panel --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_panel_is_one_container_with_a_button_per_type() -> None:
    types = [_type(id=i, name=f"Type {i}", emoji=None) for i in range(1, 8)]
    components = render.build_panel_components(_config(), types, image_filename="panel.png")

    assert len(components) == 1
    container = components[0]
    kinds = [type(c).__name__ for c in container.children]
    # Seven buttons need two rows: Discord allows five per row.
    assert kinds == ["TextDisplay", "Separator", "TextDisplay", "MediaGallery", "ActionRow", "ActionRow"]
    assert container.children[0].content == "## Open a ticket"
    assert container.children[3].items[0].media.url == "attachment://panel.png"
    buttons = [b for row in container.children[4:] for b in row.children]
    assert [b.custom_id for b in buttons] == [f"ticket:open:{i}" for i in range(1, 8)]
    assert [b.label for b in buttons] == [f"Type {i}" for i in range(1, 8)]


@pytest.mark.asyncio
async def test_panel_serialises_as_components_v2() -> None:
    payload, is_v2 = normalize_components_to_dict(
        render.build_panel_components(_config(), [_type()], image_filename=None)
    )
    assert is_v2 is True
    assert payload[0]["accent_color"] == render.TICKET_COLOUR


@pytest.mark.asyncio
async def test_panel_without_types_or_image_is_just_the_text() -> None:
    container = render.build_panel_components(
        _config(panel_title=None), [], image_filename=None
    )[0]
    assert [type(c).__name__ for c in container.children] == ["TextDisplay"]


# --- the message at the top of a ticket ---------------------------------------


@pytest.mark.asyncio
async def test_ticket_message_has_the_type_content_a_close_button_and_the_pings() -> None:
    components = render.build_ticket_components(
        _type(),
        ticket_id=11,
        opener_id=OPENER_ID,
        staff_role_ids=[STAFF_ROLE_ID, 901],
        image_filename="type-3.png",
    )
    container, mentions = components
    kinds = [type(c).__name__ for c in container.children]
    assert kinds == ["TextDisplay", "Separator", "TextDisplay", "MediaGallery", "ActionRow"]
    close = container.children[-1].children[0]
    assert close.custom_id == "ticket:close:11"
    assert close.disabled is False
    assert mentions.content == f"<@{OPENER_ID}> opened this ticket. <@&{STAFF_ROLE_ID}> <@&901>"
    assert normalize_components_to_dict(components)[1] is True


@pytest.mark.asyncio
async def test_preview_pings_nobody_and_cannot_close_anything() -> None:
    components = render.build_ticket_components(
        _type(), ticket_id=0, opener_id=None, staff_role_ids=[], image_filename=None, preview=True
    )
    assert len(components) == 1
    assert components[0].children[-1].children[0].disabled is True


def test_a_full_ticket_message_fits_discords_text_limit() -> None:
    """Heading + body + mention line share one 4000-character budget."""
    heading = "## " + "t" * render.MAX_TITLE_LENGTH
    mentions = render.mention_line(10**19, [10**19] * render.MAX_STAFF_ROLES)
    total = len(heading) + render.MAX_BODY_LENGTH + len(mentions)
    assert total <= render.MAX_COMPONENTS_TEXT_LENGTH


@pytest.mark.parametrize(
    ("type_name", "username", "expected"),
    [
        ("Support", "Jeff.T 23", "support-jeff-t-23"),
        ("Buy a Sub!", "july", "buy-a-sub-july"),
        ("Support", "عبدالله", "support"),  # nothing Discord-safe left of the name
        ("🎫", "🎫", "ticket"),
    ],
)
def test_channel_name(type_name, username, expected) -> None:
    assert render.channel_name(type_name, username) == expected


def test_channel_name_stays_under_discords_limit() -> None:
    name = render.channel_name("x" * 80, "y" * 80)
    assert len(name) <= render.MAX_CHANNEL_NAME_LENGTH


# --- transcript ---------------------------------------------------------------


def _message(content="", *, name="jeff", author_id=1, bot=False, attachments=(), embeds=()):
    message = MagicMock()
    message.content = content
    message.author = SimpleNamespace(display_name=name, name=name, id=author_id, bot=bot)
    message.created_at = datetime(2026, 11, 16, 12, 30, 5)
    message.attachments = [SimpleNamespace(filename=f) for f in attachments]
    message.embeds = list(embeds)
    return message


def test_transcript_records_who_said_what_and_names_attachments() -> None:
    text = render.format_transcript(
        _ticket(),
        [
            _message(bot=True, name="Blossom", author_id=99),  # the v2 opening message
            _message("my payment failed", attachments=("receipt.png",)),
            _message(""),  # nothing to record
            _message("looking into it", name="mod", author_id=2),
        ],
        closed_by="mod (2)",
    )
    assert "Ticket #11 — Support" in text
    assert f"Opened by: {OPENER_ID}" in text
    assert "Opened at: 2026-11-16 12:00:00 UTC" in text
    assert "Closed by: mod (2)" in text
    assert "[2026-11-16 12:30:05 UTC] Blossom (99): [bot message]" in text
    assert "[2026-11-16 12:30:05 UTC] jeff (1): my payment failed" in text
    assert "    [attachment: receipt.png]" in text
    assert "[2026-11-16 12:30:05 UTC] mod (2): looking into it" in text
    assert text.count("jeff (1)") == 1  # the empty message was skipped


def test_transcript_says_when_it_was_cut_short() -> None:
    text = render.format_transcript(_ticket(), [_message("hi")], closed_by="mod", truncated=True)
    assert "oldest only" in text


def test_log_embed_names_the_opener_and_closer() -> None:
    embed = render.log_embed(_ticket(), closed_by_id=2, message_count=14)
    fields = {f.name: f.value for f in embed.fields}
    assert str(OPENER_ID) in fields["Opened by"]
    assert fields["Closed by"] == "<@2>"
    assert fields["Messages"] == "14"
    assert embed.footer.text == "Ticket #11"


# --- permissions --------------------------------------------------------------


def _perms(**missing) -> SimpleNamespace:
    names = set(service.GUILD_PERMS) | set(service.PANEL_CHANNEL_PERMS) | set(service.LOG_CHANNEL_PERMS)
    values = {name: True for name in names}
    values.update(missing)
    return SimpleNamespace(**values)


def _place(name: str, perms: SimpleNamespace) -> MagicMock:
    place = MagicMock()
    place.name = name
    place.mention = f"#{name}"
    place.permissions_for = MagicMock(return_value=perms)
    return place


def test_setup_has_no_problems_when_the_bot_can_do_everything() -> None:
    guild = MagicMock()
    guild.me.guild_permissions = _perms()
    ok = _perms()
    assert service.setup_problems(guild, _place("open", ok), _place("Tickets", ok), _place("log", ok)) == []


def test_setup_names_each_missing_permission_and_where() -> None:
    guild = MagicMock()
    guild.me.guild_permissions = _perms(manage_channels=False)
    problems = service.setup_problems(
        guild,
        _place("open", _perms(attach_files=False)),
        _place("Tickets", _perms()),
        _place("log", _perms(send_messages=False)),
    )
    text = "\n".join(problems)
    assert "server-wide" in text and "Manage Channels" in text
    assert "#open" in text and "Attach Files" in text
    assert "#log" in text and "Send Messages" in text


def _member(*, admin=False, role_ids=()) -> MagicMock:
    member = MagicMock()
    member.id = 555
    member.guild.id = GUILD_ID
    member.guild_permissions.administrator = admin
    member.roles = [SimpleNamespace(id=rid) for rid in role_ids]
    return member


def test_staff_are_admins_and_holders_of_a_staff_role() -> None:
    assert service.is_staff(_member(admin=True), []) is True
    assert service.is_staff(_member(role_ids=[1, STAFF_ROLE_ID]), [STAFF_ROLE_ID]) is True
    assert service.is_staff(_member(role_ids=[1]), [STAFF_ROLE_ID]) is False
    assert service.is_staff(_member(), []) is False


@pytest.mark.asyncio
async def test_can_close_reads_the_staff_roles_of_the_members_own_server() -> None:
    with patch.object(storage, "list_staff_roles", AsyncMock(return_value=[STAFF_ROLE_ID])) as roles:
        assert await service.can_close("pool", _member(role_ids=[STAFF_ROLE_ID])) is True
        assert await service.can_close("pool", _member(role_ids=[5])) is False
    assert roles.await_args.args == ("pool", GUILD_ID)


# --- opening a ticket ---------------------------------------------------------


class _Open:
    """A guild + patched storage for driving service.open_ticket."""

    def __init__(self, *, category_channels=0, existing=None, created=True) -> None:
        self.calls: list[str] = []
        self.role = MagicMock()
        self.role.id = STAFF_ROLE_ID
        self.category = MagicMock()
        self.category.channels = [object()] * category_channels
        self.channel = MagicMock()
        self.channel.id = 5000
        self.channel.send = AsyncMock(side_effect=lambda **kw: self.calls.append("send"))
        self.channel.delete = AsyncMock(side_effect=lambda **kw: self.calls.append("delete_channel"))
        self.existing_channel = MagicMock()

        self.guild = MagicMock()
        self.guild.id = GUILD_ID
        self.channels = {20: self.category}
        self.guild.get_channel = MagicMock(side_effect=lambda cid: self.channels.get(cid))
        self.guild.get_role = MagicMock(side_effect=lambda rid: self.role if rid == STAFF_ROLE_ID else None)
        self.guild.create_text_channel = AsyncMock(side_effect=self._create_channel)

        self.member = MagicMock()
        self.member.id = OPENER_ID
        self.member.name = "jeff"

        created_rows = [_ticket(channel_id=None)] if created else [None, _ticket(channel_id=None)]
        self.storage = {
            "get_type": AsyncMock(return_value=_type()),
            "get_config": AsyncMock(return_value=_config()),
            "create_ticket": AsyncMock(side_effect=self._record("create_ticket", created_rows)),
            "get_open_ticket": AsyncMock(return_value=existing),
            "list_staff_roles": AsyncMock(return_value=[STAFF_ROLE_ID, 12345]),  # 12345 was deleted
            "attach_channel": AsyncMock(),
            "delete_ticket": AsyncMock(side_effect=lambda *a: self.calls.append("delete_row")),
            "claim_close": AsyncMock(return_value=_ticket()),
        }

    def _record(self, name, results):
        results = list(results)

        async def call(*args, **kwargs):
            self.calls.append(name)
            return results.pop(0) if results else None

        return call

    async def _create_channel(self, *args, **kwargs):
        self.calls.append("create_channel")
        return self.channel

    async def run(self, type_id=3):
        patches = [patch.object(storage, name, mock) for name, mock in self.storage.items()]
        patches.append(patch.object(images, "load", AsyncMock(return_value=None)))
        for p in patches:
            p.start()
        try:
            return await service.open_ticket("pool", self.guild, self.member, type_id)
        finally:
            for p in patches:
                p.stop()


@pytest.mark.asyncio
async def test_open_claims_the_row_then_makes_a_private_channel() -> None:
    env = _Open()
    result = await env.run()

    assert result.created is True and result.channel is env.channel
    # The row exists before the channel: the Close button's id comes from it.
    assert env.calls == ["create_ticket", "create_channel", "send"]
    args, kwargs = env.guild.create_text_channel.await_args
    assert args == ("support-jeff",)
    assert kwargs["category"] is env.category
    overwrites = kwargs["overwrites"]
    assert overwrites[env.guild.default_role].view_channel is False
    # Without its own entry the bot couldn't see the channel it just created.
    assert overwrites[env.guild.me].view_channel is True
    assert overwrites[env.guild.me].manage_channels is True
    assert overwrites[env.member].view_channel is True
    assert overwrites[env.member].send_messages is True
    assert overwrites[env.role].view_channel is True
    assert len(overwrites) == 4  # the deleted staff role is skipped
    env.storage["attach_channel"].assert_awaited_once_with("pool", 11, 5000)


@pytest.mark.asyncio
async def test_open_pings_the_opener_and_the_staff_roles_only() -> None:
    env = _Open()
    await env.run()

    kwargs = env.channel.send.await_args.kwargs
    container, mentions = kwargs["components"]
    assert mentions.content == f"<@{OPENER_ID}> opened this ticket. <@&{STAFF_ROLE_ID}>"
    assert container.children[-1].children[0].custom_id == "ticket:close:11"
    allowed = kwargs["allowed_mentions"]
    assert allowed.everyone is False
    assert allowed.users == [env.member]
    assert allowed.roles == [env.role]
    assert "content" not in kwargs and "embed" not in kwargs  # v2 forbids both


@pytest.mark.asyncio
async def test_open_attaches_the_types_banner() -> None:
    env = _Open()
    env.storage["get_type"] = AsyncMock(return_value=_type(image="type-3.png"))
    patches = [patch.object(storage, name, mock) for name, mock in env.storage.items()]
    patches.append(patch.object(images, "load", AsyncMock(return_value=b"png-bytes")))
    for p in patches:
        p.start()
    try:
        await service.open_ticket("pool", env.guild, env.member, 3)
    finally:
        for p in patches:
            p.stop()

    kwargs = env.channel.send.await_args.kwargs
    assert [f.filename for f in kwargs["files"]] == ["type-3.png"]
    gallery = [c for c in _walk(kwargs["components"]) if isinstance(c, disnake.ui.MediaGallery)][0]
    assert gallery.items[0].media.url == "attachment://type-3.png"


@pytest.mark.asyncio
async def test_second_press_returns_the_ticket_they_already_have() -> None:
    env = _Open(existing=_ticket(channel_id=5001), created=False)
    env.channels[5001] = env.existing_channel
    result = await env.run()

    assert result.created is False and result.channel is env.existing_channel
    env.guild.create_text_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_ticket_still_being_created_is_not_duplicated() -> None:
    """The losing half of a double-click must not make a second channel."""
    env = _Open(existing=_ticket(channel_id=None, opened_at=datetime.utcnow()), created=False)
    with pytest.raises(TicketError, match="being created"):
        await env.run()
    env.guild.create_text_channel.assert_not_awaited()
    env.storage["claim_close"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_ticket_whose_channel_is_gone_is_cleared_and_reopened() -> None:
    """E.g. the channel was deleted while the bot was offline, or an open died
    half-way: the member must not be locked out of that type forever."""
    stale = _ticket(id=9, channel_id=None, opened_at=datetime.utcnow() - timedelta(minutes=10))
    env = _Open(existing=stale, created=False)
    result = await env.run()

    assert result.created is True
    env.storage["claim_close"].assert_awaited_once_with("pool", 9, None)
    env.guild.create_text_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_full_category_refuses_and_leaves_no_row() -> None:
    env = _Open(category_channels=service.CATEGORY_CHANNEL_LIMIT)
    with pytest.raises(TicketError, match="full"):
        await env.run()
    assert env.calls == ["create_ticket", "delete_row"]
    env.guild.create_text_channel.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_channel_creation_rolls_the_row_back() -> None:
    env = _Open()
    response = MagicMock(status=403, reason="Forbidden")
    env.guild.create_text_channel = AsyncMock(side_effect=disnake.Forbidden(response, "Missing Permissions"))
    with pytest.raises(TicketError, match="not allowed"):
        await env.run()
    assert env.calls == ["create_ticket", "delete_row"]


@pytest.mark.asyncio
async def test_a_failed_first_message_removes_the_row_then_the_channel() -> None:
    """Row first: deleting the channel fires the channel-delete listener, which
    would otherwise record a ticket that never opened as closed."""
    env = _Open()
    env.channel.send = AsyncMock(side_effect=RuntimeError("boom"))
    with pytest.raises(TicketError):
        await env.run()
    assert env.calls == ["create_ticket", "create_channel", "delete_row", "delete_channel"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ticket_type",
    [None, _type(guild_id=999), _type(removed_at=datetime(2026, 11, 1))],
    ids=["unknown", "another server's", "removed"],
)
async def test_open_refuses_a_type_that_is_not_this_servers_live_one(ticket_type) -> None:
    env = _Open()
    env.storage["get_type"] = AsyncMock(return_value=ticket_type)
    with pytest.raises(TicketError, match="no longer exists"):
        await env.run()
    assert env.calls == []


@pytest.mark.asyncio
async def test_open_refuses_when_the_category_is_gone() -> None:
    env = _Open()
    env.channels.clear()
    with pytest.raises(TicketError, match="set up"):
        await env.run()
    assert env.calls == []


# --- closing a ticket ---------------------------------------------------------


class _Close:
    def __init__(self, *, ticket=None, claimed=True, log=True) -> None:
        self.calls: list[str] = []
        self.ticket = ticket if ticket is not None else _ticket()

        self.channel = MagicMock()
        history = [_message("hello"), _message("bye", name="mod", author_id=2)]

        def _history(**kwargs):
            async def gen():
                for m in history:
                    yield m

            return gen()

        self.channel.history = MagicMock(side_effect=_history)
        self.channel.delete = AsyncMock(side_effect=lambda **kw: self.calls.append("delete_channel"))
        self.log = MagicMock()
        self.log.mention = "#ticket-log"
        self.log.send = AsyncMock(side_effect=lambda **kw: self.calls.append("log"))

        self.guild = MagicMock()
        self.guild.id = GUILD_ID
        channels = {5000: self.channel}
        if log:
            channels[30] = self.log
        self.guild.get_channel = MagicMock(side_effect=lambda cid: channels.get(cid))

        self.closer = MagicMock()
        self.closer.id = 2
        self.closer.__str__ = lambda s: "mod"

        self.storage = {
            "get_ticket": AsyncMock(return_value=self.ticket),
            "get_config": AsyncMock(return_value=_config()),
            "claim_close": AsyncMock(
                side_effect=lambda *a: (self.calls.append("claim"), self.ticket if claimed else None)[1]
            ),
            "reopen": AsyncMock(side_effect=lambda *a: self.calls.append("reopen")),
        }

    async def run(self, ticket_id=11):
        patches = [patch.object(storage, name, mock) for name, mock in self.storage.items()]
        for p in patches:
            p.start()
        try:
            await service.close_ticket("pool", self.guild, ticket_id, self.closer)
        finally:
            for p in patches:
                p.stop()


@pytest.mark.asyncio
async def test_close_saves_the_transcript_before_deleting_the_channel() -> None:
    env = _Close()
    await env.run()

    assert env.calls == ["claim", "log", "delete_channel"]
    assert env.channel.history.call_args.kwargs["oldest_first"] is True
    kwargs = env.log.send.await_args.kwargs
    assert kwargs["file"].filename == "ticket-11.txt"
    transcript = kwargs["file"].fp.read().decode()
    assert "jeff (1): hello" in transcript and "mod (2): bye" in transcript
    assert kwargs["embed"].title == "Ticket closed — Support"
    # The opener's mention in the log must not notify them.
    assert kwargs["allowed_mentions"].users is False


@pytest.mark.asyncio
async def test_a_failed_transcript_leaves_the_ticket_open_and_the_channel_alone() -> None:
    env = _Close()
    env.log.send = AsyncMock(side_effect=RuntimeError("no permission"))
    with pytest.raises(TicketError, match="still open"):
        await env.run()

    assert env.calls == ["claim", "reopen"]
    env.channel.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_close_without_a_log_channel_does_nothing() -> None:
    env = _Close(log=False)
    with pytest.raises(TicketError, match="log channel"):
        await env.run()
    assert env.calls == []


@pytest.mark.asyncio
async def test_only_one_of_two_simultaneous_closes_runs() -> None:
    env = _Close(claimed=False)
    with pytest.raises(TicketError, match="already being closed"):
        await env.run()
    assert env.calls == ["claim"]
    env.log.send.assert_not_awaited()
    env.channel.delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ticket", "message"),
    [
        (_ticket(guild_id=999), "no longer exists"),  # another server's ticket id
        (_ticket(closed_at=datetime(2026, 11, 16)), "already closed"),
    ],
)
async def test_close_refuses_other_servers_and_closed_tickets(ticket, message) -> None:
    env = _Close(ticket=ticket)
    with pytest.raises(TicketError, match=message):
        await env.run()
    assert env.calls == []


@pytest.mark.asyncio
async def test_close_tolerates_a_channel_that_is_already_gone() -> None:
    env = _Close(ticket=_ticket(channel_id=7777))  # not in the guild any more
    await env.run()
    assert env.calls == ["claim", "log"]


@pytest.mark.asyncio
async def test_close_reports_a_channel_it_could_not_delete_but_stays_closed() -> None:
    env = _Close()
    response = MagicMock(status=403, reason="Forbidden")
    env.channel.delete = AsyncMock(side_effect=disnake.Forbidden(response, "Missing Permissions"))
    with pytest.raises(TicketError, match="delete it by hand"):
        await env.run()
    assert "reopen" not in env.calls


# --- publishing the panel -----------------------------------------------------


def _panel_env(config: dict, *, image: bytes | None = None):
    channel = MagicMock()
    sent = MagicMock()
    sent.id = 777
    channel.send = AsyncMock(return_value=sent)
    partial = MagicMock()
    partial.edit = AsyncMock(return_value="edited")
    channel.get_partial_message = MagicMock(return_value=partial)
    guild = MagicMock()
    guild.id = GUILD_ID
    guild.get_channel = MagicMock(side_effect=lambda cid: channel if cid == 10 else None)
    patches = [
        patch.object(storage, "get_config", AsyncMock(return_value=config)),
        patch.object(storage, "list_types", AsyncMock(return_value=[_type()])),
        patch.object(storage, "set_panel_message", AsyncMock()),
        patch.object(images, "load", AsyncMock(return_value=image)),
    ]
    return guild, channel, partial, patches


@pytest.mark.asyncio
async def test_first_publish_posts_the_panel_and_remembers_the_message() -> None:
    guild, channel, partial, patches = _panel_env(_config())
    with patches[0], patches[1], patches[2] as remember, patches[3]:
        await service.publish_panel("pool", guild)

    partial.edit.assert_not_awaited()
    container = channel.send.await_args.kwargs["components"][0]
    assert container.children[-1].children[0].custom_id == "ticket:open:3"
    remember.assert_awaited_once_with("pool", GUILD_ID, 777)


@pytest.mark.asyncio
async def test_republish_edits_in_place_and_re_uploads_the_banner() -> None:
    """`attachments=[]` drops the old upload, so the new file is the only one."""
    guild, channel, partial, patches = _panel_env(
        _config(panel_message_id=555, panel_image="panel.png"), image=b"png"
    )
    with patches[0], patches[1], patches[2] as remember, patches[3]:
        assert await service.publish_panel("pool", guild) == "edited"

    channel.get_partial_message.assert_called_once_with(555)
    kwargs = partial.edit.await_args.kwargs
    assert kwargs["attachments"] == []
    assert [f.filename for f in kwargs["files"]] == ["panel.png"]
    channel.send.assert_not_awaited()
    remember.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_deleted_panel_is_posted_again() -> None:
    guild, channel, partial, patches = _panel_env(_config(panel_message_id=555))
    partial.edit = AsyncMock(side_effect=disnake.NotFound(MagicMock(status=404, reason="x"), "gone"))
    with patches[0], patches[1], patches[2] as remember, patches[3]:
        await service.publish_panel("pool", guild)

    channel.send.assert_awaited_once()
    remember.assert_awaited_once_with("pool", GUILD_ID, 777)


@pytest.mark.asyncio
async def test_a_missing_banner_file_publishes_without_it() -> None:
    guild, channel, _partial, patches = _panel_env(_config(panel_image="panel.png"), image=None)
    with patches[0], patches[1], patches[2], patches[3]:
        await service.publish_panel("pool", guild)

    kwargs = channel.send.await_args.kwargs
    assert "file" not in kwargs
    assert not [c for c in _walk(kwargs["components"]) if isinstance(c, disnake.ui.MediaGallery)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "message"),
    [
        (None, "/ticket setup"),
        (_config(panel_title=None, panel_body=None), "/ticket panel"),
        (_config(panel_channel_id=404), "deleted"),
    ],
)
async def test_publish_explains_what_is_missing(config, message) -> None:
    guild, channel, _partial, patches = _panel_env(config)
    with patches[0], patches[1], patches[2], patches[3]:
        with pytest.raises(TicketError, match=message):
            await service.publish_panel("pool", guild)
    channel.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_never_raises_and_skips_a_server_with_no_panel() -> None:
    guild = MagicMock()
    guild.id = GUILD_ID
    with patch.object(storage, "get_config", AsyncMock(return_value=_config(panel_title=None, panel_body=None))):
        note = await service.refresh_panel("pool", guild)
    assert "/ticket panel" in note

    with patch.object(storage, "get_config", AsyncMock(return_value=_config())), patch.object(
        service, "publish_panel", AsyncMock(side_effect=TicketError("channel deleted"))
    ):
        assert await service.refresh_panel("pool", guild) == "channel deleted"


# --- storage + schema ---------------------------------------------------------


@pytest.mark.asyncio
async def test_create_ticket_lets_the_database_decide_who_wins() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)  # the insert lost the conflict
    result = await storage.create_ticket(
        _fake_pool(conn), GUILD_ID, type_id=3, type_name="Support", opener_id=OPENER_ID
    )
    assert result is None
    sql = conn.fetchrow.await_args.args[0]
    assert "ON CONFLICT (guild_id, opener_id, type_id) WHERE closed_at IS NULL DO NOTHING" in sql


@pytest.mark.asyncio
async def test_claim_close_only_matches_an_open_ticket() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    assert await storage.claim_close(_fake_pool(conn), 11, 2) is None
    assert "closed_at IS NULL" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_remove_type_is_a_soft_delete_scoped_to_the_server() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    assert await storage.remove_type(_fake_pool(conn), GUILD_ID, 3) is True
    sql = conn.execute.await_args.args[0]
    assert "DELETE" not in sql and "removed_at = NOW()" in sql and "guild_id = $2" in sql


def test_schema_enforces_one_open_ticket_per_member_per_type() -> None:
    assert "ON tickets (guild_id, opener_id, type_id) WHERE closed_at IS NULL" in models.CREATE_TICKETS
    assert "WHERE removed_at IS NULL" in models.CREATE_TICKET_TYPES


def test_ticket_tables_never_alter_guild_settings() -> None:
    """A startup ALTER on a table every poller reads is what hung on 2026-10-29."""
    for ddl in (
        models.CREATE_TICKET_CONFIG,
        models.CREATE_TICKET_TYPES,
        models.CREATE_TICKET_STAFF_ROLES,
        models.CREATE_TICKETS,
    ):
        assert "ALTER TABLE" not in ddl
        assert "guild_settings" not in ddl


def test_tickets_are_dropped_before_the_types_they_reference() -> None:
    tables = models.ALL_TABLES
    assert tables.index("tickets") < tables.index("ticket_types")


# --- images -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_images_round_trip_per_server(tmp_path) -> None:
    with patch.object(images, "settings", SimpleNamespace(TICKET_IMAGE_DIR=str(tmp_path))):
        name = await images.save(GUILD_ID, images.type_stem(3), ".png", b"abc")
        assert name == "type-3.png"
        assert await images.load(GUILD_ID, name) == b"abc"
        assert await images.load(GUILD_ID + 1, name) is None  # another server's folder
        await images.delete(GUILD_ID, name)
        assert await images.load(GUILD_ID, name) is None
        await images.delete(GUILD_ID, name)  # already gone: no error
        assert await images.load(GUILD_ID, None) is None


@pytest.mark.asyncio
async def test_images_refuse_a_path_outside_the_folder(tmp_path) -> None:
    with patch.object(images, "settings", SimpleNamespace(TICKET_IMAGE_DIR=str(tmp_path))):
        assert await images.load(GUILD_ID, "../../.env") is None


def test_only_formats_discord_shows_inline_are_allowed() -> None:
    assert images.extension_of("Banner.PNG") == ".png"
    assert images.extension_of("photo.heic") is None
    assert images.extension_of(None) is None


# --- thumbnails ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_thumbnail_sits_beside_the_text_in_a_section() -> None:
    components = render.build_ticket_components(
        _type(),
        ticket_id=11,
        opener_id=OPENER_ID,
        staff_role_ids=[],
        image_filename="type-3.png",
        thumbnail_filename="type-3-thumb.png",
    )
    container = components[0]
    # A section can't hold a separator, so the heading and body sit together.
    assert [type(c).__name__ for c in container.children] == ["Section", "MediaGallery", "ActionRow"]
    section = container.children[0]
    assert [c.content for c in section.children] == [
        "## How can we help?", "**Tell us**\n- what happened\n- when",
    ]
    assert isinstance(section.accessory, disnake.ui.Thumbnail)
    assert section.accessory.media.url == "attachment://type-3-thumb.png"
    assert normalize_components_to_dict(components)[1] is True


@pytest.mark.asyncio
async def test_the_panel_takes_a_thumbnail_too() -> None:
    container = render.build_panel_components(
        _config(), [_type()], image_filename=None, thumbnail_filename="panel-thumb.png"
    )[0]
    assert [type(c).__name__ for c in container.children] == ["Section", "ActionRow"]
    assert container.children[0].accessory.media.url == "attachment://panel-thumb.png"


@pytest.mark.asyncio
async def test_a_thumbnail_with_no_text_beside_it_is_left_out() -> None:
    """A section needs text; Discord would reject one without it."""
    components = render.build_ticket_components(
        _type(title=None, body=None),
        ticket_id=11,
        opener_id=None,
        staff_role_ids=[],
        image_filename=None,
        thumbnail_filename="type-3-thumb.png",
        preview=True,
    )
    assert [type(c).__name__ for c in components[0].children] == ["ActionRow"]


@pytest.mark.asyncio
async def test_open_attaches_the_banner_and_the_thumbnail() -> None:
    env = _Open()
    env.storage["get_type"] = AsyncMock(
        return_value=_type(image="type-3.png", thumbnail="type-3-thumb.png")
    )
    patches = [patch.object(storage, name, mock) for name, mock in env.storage.items()]
    patches.append(patch.object(images, "load", AsyncMock(return_value=b"png-bytes")))
    for p in patches:
        p.start()
    try:
        await service.open_ticket("pool", env.guild, env.member, 3)
    finally:
        for p in patches:
            p.stop()

    kwargs = env.channel.send.await_args.kwargs
    assert [f.filename for f in kwargs["files"]] == ["type-3.png", "type-3-thumb.png"]
    thumb = [c for c in _walk(kwargs["components"]) if isinstance(c, disnake.ui.Thumbnail)][0]
    assert thumb.media.url == "attachment://type-3-thumb.png"


@pytest.mark.asyncio
async def test_a_missing_thumbnail_file_renders_without_it() -> None:
    env = _Open()
    env.storage["get_type"] = AsyncMock(return_value=_type(thumbnail="type-3-thumb.png"))
    patches = [patch.object(storage, name, mock) for name, mock in env.storage.items()]
    patches.append(patch.object(images, "load", AsyncMock(return_value=None)))
    for p in patches:
        p.start()
    try:
        await service.open_ticket("pool", env.guild, env.member, 3)
    finally:
        for p in patches:
            p.stop()

    kwargs = env.channel.send.await_args.kwargs
    assert "files" not in kwargs
    assert not [c for c in _walk(kwargs["components"]) if isinstance(c, disnake.ui.Thumbnail)]


@pytest.mark.asyncio
async def test_republish_re_uploads_the_panel_thumbnail() -> None:
    guild, channel, partial, patches = _panel_env(
        _config(panel_message_id=555, panel_image="panel.png", panel_thumbnail="panel-thumb.png"),
        image=b"png",
    )
    with patches[0], patches[1], patches[2], patches[3]:
        await service.publish_panel("pool", guild)
    files = partial.edit.await_args.kwargs["files"]
    assert [f.filename for f in files] == ["panel.png", "panel-thumb.png"]


def test_thumbnail_migration_is_guarded_and_stays_off_guild_settings() -> None:
    """Every ALTER sits behind an information_schema check, so once the column
    exists startup never takes the table lock again (the 2026-10-29 hang)."""
    ddl = models.MIGRATE_TICKET_THUMBNAILS
    assert "guild_settings" not in ddl
    assert ddl.count("ALTER TABLE") == ddl.count("IF NOT EXISTS (") == 2
    assert "panel_thumbnail VARCHAR(100)" in models.CREATE_TICKET_CONFIG
    assert "thumbnail VARCHAR(100)" in models.CREATE_TICKET_TYPES


def test_thumbnail_files_never_collide_with_banners() -> None:
    assert images.type_thumbnail_stem(3) != images.type_stem(3)
    assert images.PANEL_THUMBNAIL_STEM != images.PANEL_STEM
