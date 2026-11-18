"""The Discord-side ticket operations: check setup, publish the panel, open a
ticket, close a ticket.

Same shape as modules/moderation/purge.py: functions take disnake objects and a
pool, never a bot, so tests drive them with mocked guilds and a mocked pool.
Anything the person can be told about is raised as TicketError.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg
import disnake

from modules.tickets import images, render, storage
from modules.tickets.render import TicketError

logger = logging.getLogger(__name__)

NO_PINGS = disnake.AllowedMentions.none()
# Discord's cap on channels in one category.
CATEGORY_CHANNEL_LIMIT = 50
# The newest messages past this many are left out of a transcript.
TRANSCRIPT_MAX_MESSAGES = 5000
# A ticket row without a channel is "still being created" for this long. Past
# it the creation is assumed to have died (bot restart mid-open) and the row is
# cleared so the member isn't locked out of that ticket type for good.
PENDING_OPEN_GRACE_SECONDS = 60

# Discord only lets a bot allow or deny, in a new channel's overwrites, the
# permissions it holds server-wide — and the channel is created with its own
# overwrites, so nothing is inherited from the category. Hence these are checked
# on the bot's server-wide permissions, not on the category.
GUILD_PERMS = (
    "manage_channels",
    "view_channel",
    "send_messages",
    "embed_links",
    "attach_files",
    "read_message_history",
)
PANEL_CHANNEL_PERMS = ("view_channel", "send_messages", "attach_files")
LOG_CHANNEL_PERMS = ("view_channel", "send_messages", "embed_links", "attach_files")

# What the opener and the staff roles get in a ticket channel.
MEMBER_OVERWRITE = dict(
    view_channel=True,
    send_messages=True,
    read_message_history=True,
    attach_files=True,
    embed_links=True,
)


@dataclass(frozen=True)
class OpenResult:
    channel: disnake.abc.GuildChannel
    created: bool  # False = the member already had this ticket open


def _label(permission: str) -> str:
    return permission.replace("_", " ").title()


def _missing(perms, names) -> list[str]:
    return [_label(name) for name in names if not getattr(perms, name, False)]


def setup_problems(guild: disnake.Guild, panel_channel, category, log_channel) -> list[str]:
    """Everything stopping tickets from working with these three places."""
    problems: list[str] = []
    me = guild.me
    missing = _missing(me.guild_permissions, GUILD_PERMS)
    if missing:
        problems.append(
            f"My role needs these server-wide (Server Settings → Roles): {', '.join(missing)}."
        )
    missing = _missing(panel_channel.permissions_for(me), PANEL_CHANNEL_PERMS)
    if missing:
        problems.append(f"In {panel_channel.mention} I'm missing: {', '.join(missing)}.")
    missing = _missing(category.permissions_for(me), ("view_channel", "manage_channels"))
    if missing:
        problems.append(f"In the **{category.name}** category I'm missing: {', '.join(missing)}.")
    missing = _missing(log_channel.permissions_for(me), LOG_CHANNEL_PERMS)
    if missing:
        problems.append(f"In {log_channel.mention} I'm missing: {', '.join(missing)}.")
    return problems


def is_staff(member, staff_role_ids: list[int]) -> bool:
    """Admins, and anyone holding one of the server's ticket staff roles."""
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and perms.administrator:
        return True
    held = {role.id for role in getattr(member, "roles", [])}
    return bool(held.intersection(staff_role_ids))


async def can_close(pool: asyncpg.Pool, member) -> bool:
    """Read from the DB on every click, so a role change applies immediately."""
    perms = getattr(member, "guild_permissions", None)
    if perms is not None and perms.administrator:
        return True
    return is_staff(member, await storage.list_staff_roles(pool, member.guild.id))


def _file(data: bytes | None, filename: str | None) -> disnake.File | None:
    if not data or not filename:
        return None
    return disnake.File(io.BytesIO(data), filename=filename)


async def load_media(guild_id: int, stored: str | None) -> tuple[str | None, bytes | None]:
    """(filename to reference, bytes) — both None when there's no image or its file is gone."""
    data = await images.load(guild_id, stored)
    return (stored if data else None), data


def media_files(*media: tuple[str | None, bytes | None]) -> list[disnake.File]:
    """Fresh File objects for each loaded image (a File can only be sent once)."""
    files = [_file(data, filename) for filename, data in media]
    return [f for f in files if f is not None]


# --- panel --------------------------------------------------------------------

async def publish_panel(pool: asyncpg.Pool, guild: disnake.Guild) -> disnake.Message:
    """Post the panel, or edit the one already posted, to match the database.

    The banner is re-uploaded from disk every time (`attachments=[]` drops the
    old upload), so an edit never depends on an old attachment still resolving.
    If the stored message is gone, a new one is posted.
    """
    config = await storage.get_config(pool, guild.id)
    if config is None or not config.get("panel_channel_id"):
        raise TicketError("Tickets aren't set up yet — run `/ticket setup` first.")
    if not config.get("panel_title") and not config.get("panel_body"):
        raise TicketError("The panel has no message yet — write one with `/ticket panel`.")
    channel = guild.get_channel(config["panel_channel_id"])
    if channel is None:
        raise TicketError("The open-ticket channel was deleted — run `/ticket setup` again.")

    types = await storage.list_types(pool, guild.id)
    banner = await load_media(guild.id, config.get("panel_image"))
    thumbnail = await load_media(guild.id, config.get("panel_thumbnail"))
    components = render.build_panel_components(
        config, types, image_filename=banner[0], thumbnail_filename=thumbnail[0]
    )

    message_id = config.get("panel_message_id")
    if message_id:
        try:
            return await channel.get_partial_message(message_id).edit(
                components=components,
                files=media_files(banner, thumbnail),
                attachments=[],
                allowed_mentions=NO_PINGS,
            )
        except disnake.NotFound:
            logger.info("Ticket panel message %s is gone in guild=%s; reposting", message_id, guild.id)

    files = media_files(banner, thumbnail)
    kwargs = {"files": files} if files else {}
    message = await channel.send(components=components, allowed_mentions=NO_PINGS, **kwargs)
    await storage.set_panel_message(pool, guild.id, message.id)
    return message


async def refresh_panel(pool: asyncpg.Pool, guild: disnake.Guild) -> str | None:
    """Re-render the panel after a type changed. Returns a note if it couldn't.

    A server that hasn't written its panel yet simply has nothing to refresh.
    Never raises: the type change itself already succeeded.
    """
    config = await storage.get_config(pool, guild.id)
    if config is None or not (config.get("panel_title") or config.get("panel_body")):
        return "There's no panel yet — post one with `/ticket panel`."
    try:
        await publish_panel(pool, guild)
    except TicketError as exc:
        return str(exc)
    except disnake.HTTPException as exc:
        logger.exception("Couldn't refresh the ticket panel in guild=%s", guild.id)
        return f"I couldn't update the panel: {exc.text or exc}"
    return None


# --- open ---------------------------------------------------------------------

def _is_stale_pending(ticket: dict, now: datetime) -> bool:
    opened = ticket.get("opened_at")
    if opened is None:
        return True
    if opened.tzinfo is not None:
        opened = opened.replace(tzinfo=None)
    return now - opened > timedelta(seconds=PENDING_OPEN_GRACE_SECONDS)


async def _claim_ticket(pool, guild, member, ticket_type) -> tuple[dict | None, object | None]:
    """(new ticket row, None) or (None, the channel of the ticket they already have)."""
    for _ in range(2):
        row = await storage.create_ticket(
            pool,
            guild.id,
            type_id=ticket_type["id"],
            type_name=ticket_type["name"],
            opener_id=member.id,
        )
        if row is not None:
            return row, None
        existing = await storage.get_open_ticket(pool, guild.id, member.id, ticket_type["id"])
        if existing is None:
            continue  # it closed between the two queries; try again
        channel = guild.get_channel(existing["channel_id"]) if existing["channel_id"] else None
        if channel is not None:
            return None, channel
        if existing["channel_id"] is None and not _is_stale_pending(existing, datetime.utcnow()):
            raise TicketError("Your ticket is being created — give it a moment.")
        # Its channel is gone (or was never made): clear the row and retry.
        await storage.claim_close(pool, existing["id"], None)
    raise TicketError("I couldn't open the ticket — please try again.")


async def open_ticket(
    pool: asyncpg.Pool, guild: disnake.Guild, member: disnake.Member, type_id: int
) -> OpenResult:
    """Open a ticket of this type for the member, or return the one they have."""
    ticket_type = await storage.get_type(pool, type_id)
    if ticket_type is None or ticket_type["guild_id"] != guild.id or ticket_type["removed_at"]:
        raise TicketError("That ticket type no longer exists.")
    config = await storage.get_config(pool, guild.id)
    category = guild.get_channel(config["category_id"]) if config and config.get("category_id") else None
    if category is None:
        raise TicketError("Tickets aren't fully set up on this server — please tell an admin.")

    ticket, existing_channel = await _claim_ticket(pool, guild, member, ticket_type)
    if ticket is None:
        return OpenResult(channel=existing_channel, created=False)

    if len(category.channels) >= CATEGORY_CHANNEL_LIMIT:
        await storage.delete_ticket(pool, ticket["id"])
        logger.warning("Ticket category is full in guild=%s", guild.id)
        raise TicketError("Tickets are full right now — please let a staff member know.")

    staff_role_ids = await storage.list_staff_roles(pool, guild.id)
    roles = [role for role in (guild.get_role(rid) for rid in staff_role_ids) if role is not None]
    overwrites = {
        guild.default_role: disnake.PermissionOverwrite(view_channel=False),
        # Without its own entry the bot is hidden from the channel it just made.
        guild.me: disnake.PermissionOverwrite(manage_channels=True, **MEMBER_OVERWRITE),
        member: disnake.PermissionOverwrite(**MEMBER_OVERWRITE),
    }
    for role in roles:
        overwrites[role] = disnake.PermissionOverwrite(**MEMBER_OVERWRITE)

    try:
        channel = await guild.create_text_channel(
            render.channel_name(ticket_type["name"], render.ticket_number(ticket)),
            category=category,
            overwrites=overwrites,
            topic=f"{ticket_type['name']} ticket #{render.ticket_number(ticket)} — opened by {member} ({member.id})",
            reason=f"Ticket #{render.ticket_number(ticket)} opened by {member} ({member.id})",
        )
    except disnake.HTTPException as exc:
        await storage.delete_ticket(pool, ticket["id"])
        logger.exception("Couldn't create a ticket channel in guild=%s", guild.id)
        if isinstance(exc, disnake.Forbidden):
            raise TicketError(
                "I'm not allowed to create the ticket channel — please tell an admin "
                "(`/setup status` shows what I'm missing)."
            ) from exc
        raise TicketError("Discord wouldn't create the ticket channel — please try again.") from exc

    try:
        await storage.attach_channel(pool, ticket["id"], channel.id)
        banner = await load_media(guild.id, ticket_type.get("image"))
        thumbnail = await load_media(guild.id, ticket_type.get("thumbnail"))
        files = media_files(banner, thumbnail)
        kwargs = {"files": files} if files else {}
        await channel.send(
            components=render.build_ticket_components(
                ticket_type,
                ticket_id=ticket["id"],
                opener_id=member.id,
                staff_role_ids=[role.id for role in roles],
                image_filename=banner[0],
                thumbnail_filename=thumbnail[0],
            ),
            allowed_mentions=disnake.AllowedMentions(
                everyone=False, users=[member], roles=roles[: render.MAX_STAFF_ROLES]
            ),
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 — never leave a half-made ticket behind
        logger.exception("Couldn't finish opening ticket id=%s in guild=%s", ticket["id"], guild.id)
        # Delete the row first: deleting the channel fires the channel-delete
        # listener, which would otherwise mark a failed ticket as closed.
        await storage.delete_ticket(pool, ticket["id"])
        try:
            await channel.delete(reason="Ticket could not be opened")
        except disnake.HTTPException:
            logger.warning("Couldn't remove half-made ticket channel %s", channel.id)
        raise TicketError("I couldn't open the ticket — please try again.") from exc

    logger.info(
        "Opened ticket id=%s type=%r for user=%s in guild=%s",
        ticket["id"], ticket_type["name"], member.id, guild.id,
    )
    return OpenResult(channel=channel, created=True)


# --- close --------------------------------------------------------------------

async def close_ticket(
    pool: asyncpg.Pool, guild: disnake.Guild, ticket_id: int, closed_by: disnake.Member
) -> None:
    """Save the transcript to the log channel, then delete the ticket channel.

    The channel is only deleted after the transcript is posted. If that fails
    the ticket is put back to open and nothing is lost.
    """
    ticket = await storage.get_ticket(pool, ticket_id)
    if ticket is None or ticket["guild_id"] != guild.id:
        raise TicketError("That ticket no longer exists.")
    if ticket["closed_at"] is not None:
        raise TicketError("This ticket is already closed.")
    config = await storage.get_config(pool, guild.id)
    log_channel = guild.get_channel(config["log_channel_id"]) if config and config.get("log_channel_id") else None
    if log_channel is None:
        raise TicketError(
            "There's no ticket log channel to save the transcript to, so I haven't closed "
            "this. An admin needs to run `/ticket setup`."
        )

    claimed = await storage.claim_close(pool, ticket_id, closed_by.id)
    if claimed is None:
        raise TicketError("This ticket is already being closed.")

    channel = guild.get_channel(ticket["channel_id"]) if ticket["channel_id"] else None
    try:
        messages = []
        if channel is not None:
            # One over the cap, to know whether anything was left out.
            async for message in channel.history(limit=TRANSCRIPT_MAX_MESSAGES + 1, oldest_first=True):
                messages.append(message)
        truncated = len(messages) > TRANSCRIPT_MAX_MESSAGES
        messages = messages[:TRANSCRIPT_MAX_MESSAGES]
        text = render.format_transcript(
            claimed, messages, closed_by=f"{closed_by} ({closed_by.id})", truncated=truncated
        )
        await log_channel.send(
            embed=render.log_embed(claimed, closed_by_id=closed_by.id, message_count=len(messages)),
            file=disnake.File(io.BytesIO(text.encode("utf-8")), filename=f"ticket-{render.ticket_number(claimed)}.txt"),
            allowed_mentions=NO_PINGS,
        )
    except Exception as exc:  # noqa: BLE001 — any failure here must leave the ticket open
        await storage.reopen(pool, ticket_id)
        logger.exception("Couldn't save the transcript for ticket id=%s", ticket_id)
        raise TicketError(
            f"I couldn't save the transcript to {log_channel.mention}, so the ticket is still open. "
            "Check my permissions there and try again."
        ) from exc

    logger.info("Closed ticket id=%s in guild=%s by user=%s", ticket_id, guild.id, closed_by.id)
    if channel is None:
        return
    try:
        await channel.delete(reason=f"Ticket #{render.ticket_number(claimed)} closed by {closed_by} ({closed_by.id})")
    except disnake.NotFound:
        pass
    except disnake.HTTPException as exc:
        logger.exception("Couldn't delete the channel of closed ticket id=%s", ticket_id)
        raise TicketError(
            "The transcript is saved and the ticket is closed, but I couldn't delete this "
            "channel — an admin can delete it by hand."
        ) from exc
