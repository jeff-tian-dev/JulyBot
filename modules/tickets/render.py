"""Build and validate everything a ticket shows: the panel, the message at the
top of a ticket, the channel name, the transcript and the log entry.

Pure — no pool and no Discord calls — so it is unit-testable on its own.

**Layout is components v2**, the same as /postbase: one accent-coloured
container holding a heading, a separator, the body, the banner as a full-width
media gallery, and the buttons. An optional thumbnail puts the heading and body
in a section with the small picture top-right instead. disnake sets the IS_COMPONENTS_V2 flag itself
and refuses `content` or `embeds` alongside these, so a v2 message never
carries either. Text displays render Discord markdown (bold, italics, bullet
lists, `#` headings), which is all the formatting the admin-written text needs.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import disnake

TICKET_COLOUR = 0xEC4899  # the same pink as /post and /postbase
LOG_COLOUR = 0x5865F2

CUSTOM_ID_PREFIX = "ticket"
ACTION_OPEN = "open"
ACTION_CLOSE = "close"
ACTION_CONFIRM = "confirm"

# The type name is the button label, which Discord caps at 80 characters.
MAX_NAME_LENGTH = 80
MAX_TITLE_LENGTH = 256
# Discord caps the combined text of every text display in one message at 4000.
# A ticket's top message is heading + body + the mention line (up to
# MAX_STAFF_ROLES role mentions of ~23 characters each), so the body gets 3000.
MAX_BODY_LENGTH = 3000
MAX_COMPONENTS_TEXT_LENGTH = 4000
MAX_STAFF_ROLES = 20
MAX_EMOJI_LENGTH = 64
# Discord allows 25 buttons on a message, five per row.
MAX_TICKET_TYPES = 25
BUTTONS_PER_ROW = 5
# Discord's channel name limit is 100.
MAX_CHANNEL_NAME_LENGTH = 90
# Discord's embed description limit is 4096; the transcript preview stays short.
TRANSCRIPT_TIME_FORMAT = "%Y-%m-%d %H:%M:%S UTC"

_CUSTOM_EMOJI = re.compile(r"^<a?:\w{2,32}:\d{15,22}>$")
_ASCII_LETTER = re.compile(r"[A-Za-z]")
# Ways to name a custom emoji by id: <a:name:id>, name:id, a bare id, or its CDN link.
_EMOJI_ID_FORMS = re.compile(
    r"^(?:<a?:\w{2,32}:(?P<id>\d{15,22})>"
    r"|:?\w{2,32}:(?P<id2>\d{15,22})"
    r"|(?P<id3>\d{15,22})"
    r"|https://(?:cdn|media)\.discordapp\.(?:com|net)/emojis/(?P<id4>\d{15,22})\.\w+(?:\?\S*)?)$"
)
_EMOJI_NAME = re.compile(r"^:?(?P<name>\w{2,32}):?$")
_NOT_SLUG = re.compile(r"[^a-z0-9]+")


class TicketError(Exception):
    """A ticket action failed in a way the person can be told about."""


# --- validation ---------------------------------------------------------------

def validate_name(name: str | None) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise TicketError("The ticket type's name can't be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise TicketError(f"That name is too long (limit {MAX_NAME_LENGTH} characters).")
    return cleaned


def validate_emoji(emoji: str | None) -> str | None:
    """A button emoji: a standard emoji or a custom one (`<:name:id>`), or None.

    Discord rejects the whole message if a button's emoji is invalid, which
    would break the panel for every type — so plain words are refused here.
    """
    cleaned = (emoji or "").strip()
    if not cleaned:
        return None
    if _CUSTOM_EMOJI.match(cleaned):
        return cleaned
    if len(cleaned) > MAX_EMOJI_LENGTH or _ASCII_LETTER.search(cleaned) or " " in cleaned:
        raise TicketError(
            "That doesn't look like an emoji. Use one standard emoji (like 🎫) "
            "or a custom emoji from this server."
        )
    return cleaned


def resolve_emoji(emoji: str | None, available) -> str | None:
    """Turn what an admin typed into a button emoji, looking custom ones up by name.

    `available` is every custom emoji the bot can use, this server's first.
    Needed because Discord only converts `:name:` into `<:name:id>` for emojis
    the *typing user* can use: without Nitro, an animated emoji or one from
    another server arrives as the plain text `:name:`. The bot can use those
    regardless, so it resolves the name itself. Also accepts `<a:name:id>`,
    `name:id`, a bare id, and an emoji's image link (right-click → Copy Link).

    A custom emoji the bot can't use is refused here: Discord would reject the
    whole panel over it.
    """
    cleaned = (emoji or "").strip()
    if not cleaned:
        return None
    usable = [e for e in available if getattr(e, "available", True)]

    match = _EMOJI_ID_FORMS.match(cleaned)
    if match:
        emoji_id = int(next(g for g in match.groups() if g))
        found = next((e for e in usable if e.id == emoji_id), None)
        if found is None:
            raise TicketError(
                "I can't use that emoji — I can only use custom emojis from servers I'm in."
            )
        return _emoji_markup(found)

    match = _EMOJI_NAME.match(cleaned)
    if match:
        name = match.group("name")
        found = [e for e in usable if e.name == name] or [
            e for e in usable if e.name.lower() == name.lower()
        ]
        if not found:
            if cleaned.startswith(":") or not _ASCII_LETTER.search(cleaned):
                raise TicketError(
                    f"I can't find a custom emoji called `{name}` in any server I'm in."
                )
            return validate_emoji(cleaned)  # a plain word: refused with the usual message
        # This server's emojis come first, so a local one wins a name clash.
        return _emoji_markup(found[0])

    return validate_emoji(cleaned)


def _emoji_markup(emoji) -> str:
    return f"<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>"


def validate_content(title: str | None, body: str | None) -> tuple[str | None, str | None]:
    """Clean a message's title and body. At least one must have text."""
    clean_title = " ".join((title or "").split()) or None
    clean_body = (body or "").strip() or None
    if not clean_title and not clean_body:
        raise TicketError("Write a title or a message — both can't be empty.")
    if clean_title and len(clean_title) > MAX_TITLE_LENGTH:
        raise TicketError(f"The title is limited to {MAX_TITLE_LENGTH} characters.")
    if clean_body and len(clean_body) > MAX_BODY_LENGTH:
        raise TicketError(
            f"The message is {len(clean_body)} characters — the limit is {MAX_BODY_LENGTH}."
        )
    return clean_title, clean_body


# --- custom ids ---------------------------------------------------------------

def custom_id(action: str, target_id: int) -> str:
    return f"{CUSTOM_ID_PREFIX}:{action}:{target_id}"


def parse_custom_id(value: str | None) -> tuple[str, int] | None:
    """(action, id) for one of the ticket buttons, or None if it isn't ours."""
    parts = (value or "").split(":")
    if len(parts) != 3 or parts[0] != CUSTOM_ID_PREFIX:
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


# --- components ---------------------------------------------------------------

def _content_children(
    title: str | None,
    body: str | None,
    image_filename: str | None,
    thumbnail_filename: str | None = None,
) -> list:
    """Heading, body and banner. With a thumbnail, the heading and body sit in a
    section with the small picture top-right (a section can't hold a separator,
    so that layout has none). A section needs text beside it, so a thumbnail on
    a message with no text is left out."""
    children: list = []
    texts: list = []
    if title:
        texts.append(disnake.ui.TextDisplay(f"## {title}"))
    if body:
        texts.append(disnake.ui.TextDisplay(body))
    if thumbnail_filename and texts:
        children.append(
            disnake.ui.Section(
                *texts, accessory=disnake.ui.Thumbnail(f"attachment://{thumbnail_filename}")
            )
        )
    else:
        if title:
            children.append(texts[0])
            if body:
                children.append(disnake.ui.Separator())
        if body:
            children.append(texts[-1])
    if image_filename:
        children.append(
            disnake.ui.MediaGallery(disnake.MediaGalleryItem(f"attachment://{image_filename}"))
        )
    return children


def _container(children: list) -> disnake.ui.Container:
    return disnake.ui.Container(*children, accent_colour=disnake.Colour(TICKET_COLOUR))


def type_button(ticket_type: dict) -> disnake.ui.Button:
    return disnake.ui.Button(
        label=ticket_type["name"],
        emoji=ticket_type.get("emoji") or None,
        style=disnake.ButtonStyle.secondary,
        custom_id=custom_id(ACTION_OPEN, ticket_type["id"]),
    )


def build_panel_components(
    config: dict,
    types: list[dict],
    *,
    image_filename: str | None,
    thumbnail_filename: str | None = None,
) -> list[disnake.ui.Container]:
    """The panel: the admin's message, then one button per ticket type."""
    children = _content_children(
        config.get("panel_title"), config.get("panel_body"), image_filename, thumbnail_filename
    )
    buttons = [type_button(t) for t in types[:MAX_TICKET_TYPES]]
    for start in range(0, len(buttons), BUTTONS_PER_ROW):
        children.append(disnake.ui.ActionRow(*buttons[start : start + BUTTONS_PER_ROW]))
    return [_container(children)]


def mention_line(opener_id: int, staff_role_ids: list[int]) -> str:
    """The line under a new ticket that notifies the opener and the staff."""
    line = f"<@{opener_id}> opened this ticket."
    if staff_role_ids:
        line += " " + " ".join(f"<@&{rid}>" for rid in staff_role_ids[:MAX_STAFF_ROLES])
    return line


def build_ticket_components(
    ticket_type: dict,
    *,
    ticket_id: int,
    opener_id: int | None,
    staff_role_ids: list[int],
    image_filename: str | None,
    thumbnail_filename: str | None = None,
    preview: bool = False,
) -> list:
    """The message at the top of a ticket: the type's content and a Close button.

    The mention line is its own text display under the container; it is what
    notifies the opener and the staff roles (the sender must allow those
    mentions). `preview=True` drops it and disables the button, for showing an
    admin what the type will look like.
    """
    children = _content_children(
        ticket_type.get("title"), ticket_type.get("body"), image_filename, thumbnail_filename
    )
    children.append(
        disnake.ui.ActionRow(
            disnake.ui.Button(
                label="Close ticket",
                emoji="🔒",
                style=disnake.ButtonStyle.danger,
                custom_id=custom_id(ACTION_CLOSE, ticket_id),
                disabled=preview,
            )
        )
    )
    components: list = [_container(children)]
    if not preview and opener_id is not None:
        components.append(disnake.ui.TextDisplay(mention_line(opener_id, staff_role_ids)))
    return components


def confirm_close_row(ticket_id: int) -> disnake.ui.ActionRow:
    return disnake.ui.ActionRow(
        disnake.ui.Button(
            label="Close and delete",
            style=disnake.ButtonStyle.danger,
            custom_id=custom_id(ACTION_CONFIRM, ticket_id),
        )
    )


# --- channel name -------------------------------------------------------------

def _slug(text: str) -> str:
    return _NOT_SLUG.sub("-", (text or "").lower()).strip("-")


def channel_name(type_name: str, username: str) -> str:
    """`<type>-<username>`, in the lowercase-and-dashes form Discord allows."""
    parts = [p for p in (_slug(type_name), _slug(username)) if p]
    name = "-".join(parts) or "ticket"
    return name[:MAX_CHANNEL_NAME_LENGTH].strip("-") or "ticket"


# --- transcript + log ---------------------------------------------------------

def _utc(dt: datetime | None) -> datetime | None:
    """Timestamps from Postgres are naive UTC (Supabase runs UTC)."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _stamp(dt: datetime | None) -> str:
    value = _utc(dt)
    return value.strftime(TRANSCRIPT_TIME_FORMAT) if value else "unknown"


def format_transcript(ticket: dict, messages: list, *, closed_by: str, truncated: bool = False) -> str:
    """A plain-text record of a ticket, oldest message first.

    Text only: files posted in the ticket are deleted with the channel, so the
    transcript names them rather than linking URLs that are about to die.
    """
    lines = [
        f"Ticket #{ticket['id']} — {ticket['type_name']}",
        f"Opened by: {ticket['opener_id']}",
        f"Opened at: {_stamp(ticket.get('opened_at'))}",
        f"Closed by: {closed_by}",
        f"Messages: {len(messages)}" + (" (oldest only — the ticket was longer than the limit)" if truncated else ""),
        "",
    ]
    for message in messages:
        author = message.author
        name = getattr(author, "display_name", None) or getattr(author, "name", "unknown")
        prefix = f"[{_stamp(message.created_at)}] {name} ({author.id})"
        content = (message.content or "").strip()
        attachments = [a.filename for a in (message.attachments or [])]
        embeds = len(message.embeds or [])
        if not content and not attachments and not embeds:
            # The bot's own components-v2 messages carry no content.
            if getattr(author, "bot", False):
                content = "[bot message]"
            else:
                continue
        lines.append(f"{prefix}: {content}" if content else f"{prefix}:")
        for filename in attachments:
            lines.append(f"    [attachment: {filename}]")
        if embeds:
            lines.append(f"    [{embeds} embed(s)]")
    return "\n".join(lines) + "\n"


def log_embed(ticket: dict, *, closed_by_id: int, message_count: int) -> disnake.Embed:
    """The summary posted with the transcript in the log channel."""
    embed = disnake.Embed(
        title=f"Ticket closed — {ticket['type_name']}",
        colour=LOG_COLOUR,
    )
    embed.add_field(name="Opened by", value=f"<@{ticket['opener_id']}> (`{ticket['opener_id']}`)")
    embed.add_field(name="Closed by", value=f"<@{closed_by_id}>")
    embed.add_field(name="Messages", value=str(message_count))
    opened = _utc(ticket.get("opened_at"))
    if opened:
        embed.add_field(name="Opened", value=f"<t:{int(opened.timestamp())}:f>")
    embed.set_footer(text=f"Ticket #{ticket['id']}")
    return embed
