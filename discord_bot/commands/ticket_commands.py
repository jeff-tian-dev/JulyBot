"""/ticket — per-server ticket system: setup, the panel, ticket types, staff roles.

Thin Cog layer over modules/tickets: parse args, call the module, format the
reply. Admin-only through the parent group; the panel's buttons are for everyone.

**Buttons are handled by one stateless listener** (`on_button_click`), routed
by custom_id (`ticket:<action>:<id>`), exactly like /postbase. Nothing is
restored on startup, and a panel or Close button from any earlier process keeps
working. Do not also register a persistent View for these ids: both would
answer the same click and the second response would fail.

**Text is written in a modal**, because a slash-command option can't hold line
breaks and a ticket message needs them (bullet lists, headings). A modal must
be the FIRST response to an interaction, so the commands that open one do only
quick checks beforehand and leave the slow work (image download, publishing)
to the modal's callback, which defers first.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from modules.announce.poster import is_image
from modules.tickets import images, render, service, storage
from modules.tickets.render import TicketError

logger = logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
# How long an admin has to finish writing in the popup before disnake forgets it.
MODAL_TIMEOUT_SECONDS = 1800
# Discord truncates modal titles past 45 characters.
MAX_MODAL_TITLE_LENGTH = 45
MAX_AUTOCOMPLETE_CHOICES = 25
FORMATTING_HINT = "**bold**  *italics*  - bullet point  # Heading"
NEXT_STEPS = (
    "Next: `/ticket role add` for staff, `/ticket type add` for each kind of ticket, "
    "then `/ticket panel` to post the message with the buttons."
)


def check_image(attachment: disnake.Attachment | None) -> None:
    """The checks that need no download, so they can run before the modal opens."""
    if attachment is None:
        return
    if not is_image(attachment) or images.extension_of(attachment.filename) is None:
        allowed = ", ".join(ext.lstrip(".") for ext in images.ALLOWED_EXTENSIONS)
        raise TicketError(f"`{attachment.filename}` isn't a supported image. Use: {allowed}.")
    if attachment.size > images.MAX_IMAGE_BYTES:
        limit_mb = images.MAX_IMAGE_BYTES // (1024 * 1024)
        raise TicketError(f"That image is too big — the limit is {limit_mb} MB.")


def usable_emojis(bot, guild) -> list:
    """Every custom emoji the bot can put on a button, this server's first.

    Bots can use custom emojis (animated too) from any server they're in, with
    no Nitro — which is what lets an admin without Nitro pick one by name.
    """
    local = list(guild.emojis)
    return local + [e for e in bot.emojis if e.guild_id != guild.id]


async def apply_image(
    guild_id: int,
    stem: str,
    current: str | None,
    attachment: disnake.Attachment | None,
    remove: bool,
) -> str | None:
    """Save a new banner, remove the old one, or keep it. Returns the filename to store."""
    if attachment is not None:
        check_image(attachment)
        try:
            data = await attachment.read()
        except disnake.HTTPException as exc:
            raise TicketError(f"I couldn't download the image: {exc}") from exc
        filename = await images.save(
            guild_id, stem, images.extension_of(attachment.filename), data
        )
        if current and current != filename:
            await images.delete(guild_id, current)
        return filename
    if remove:
        await images.delete(guild_id, current)
        return None
    return current


class ContentModal(disnake.ui.Modal):
    """Title + message popup. `on_submit(inter, title, body)` does the saving.

    Not persistent: the instance holds the callback, and disnake hands the
    submit back to this same instance. A bot restart while the form is open
    loses what was typed.
    """

    def __init__(self, *, heading: str, title_value: str | None, body_value: str | None, on_submit, custom_id: str) -> None:
        super().__init__(
            title=heading[:MAX_MODAL_TITLE_LENGTH],
            custom_id=custom_id,
            timeout=MODAL_TIMEOUT_SECONDS,
            components=[
                disnake.ui.TextInput(
                    label="Title",
                    custom_id="title",
                    value=title_value or None,
                    required=False,
                    max_length=render.MAX_TITLE_LENGTH,
                    style=disnake.TextInputStyle.short,
                ),
                disnake.ui.TextInput(
                    label="Message",
                    custom_id="body",
                    value=body_value or None,
                    placeholder=FORMATTING_HINT,
                    required=False,
                    max_length=render.MAX_BODY_LENGTH,
                    style=disnake.TextInputStyle.paragraph,
                ),
            ],
        )
        self._on_submit = on_submit

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        # Downloading an image and publishing can outlast the 3s deadline.
        await inter.response.defer(ephemeral=True)
        values = inter.text_values
        try:
            await self._on_submit(inter, values.get("title"), values.get("body"))
        except TicketError as exc:
            await inter.edit_original_response(content=f"⚠️ {exc}")
        except disnake.HTTPException as exc:
            logger.exception("Ticket modal failed on a Discord call")
            await inter.edit_original_response(content=f"⚠️ Discord refused that: {exc.text or exc}")
        except Exception:  # noqa: BLE001 — surface it instead of a silent timeout
            logger.exception("Ticket modal failed")
            await inter.edit_original_response(content="⚠️ Something went wrong — it's in the log.")


async def _send_preview(inter: disnake.Interaction, record: dict) -> None:
    """Show the admin what a ticket of this type will open with."""
    banner = await service.load_media(inter.guild.id, record.get("image"))
    thumbnail = await service.load_media(inter.guild.id, record.get("thumbnail"))
    files = service.media_files(banner, thumbnail)
    kwargs = {"files": files} if files else {}
    try:
        await inter.followup.send(
            components=render.build_ticket_components(
                record,
                ticket_id=0,
                opener_id=None,
                staff_role_ids=[],
                image_filename=banner[0],
                thumbnail_filename=thumbnail[0],
                preview=True,
            ),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
            **kwargs,
        )
    except disnake.HTTPException:
        logger.exception("Couldn't send the ticket type preview")


async def _type_autocomplete(inter: disnake.ApplicationCommandInteraction, string: str) -> list[str]:
    types = await storage.list_types(inter.bot.pool, inter.guild.id)
    needle = (string or "").lower()
    return [t["name"] for t in types if needle in t["name"].lower()][:MAX_AUTOCOMPLETE_CHOICES]


# --- buttons ------------------------------------------------------------------

async def on_open(inter: disnake.MessageInteraction, type_id: int) -> None:
    """A member pressed a ticket type on the panel."""
    # Creating the channel can outlast the 3s deadline.
    await inter.response.defer(with_message=True, ephemeral=True)
    try:
        result = await service.open_ticket(inter.bot.pool, inter.guild, inter.author, type_id)
    except TicketError as exc:
        await inter.edit_original_response(content=f"⚠️ {exc}")
        return
    if result.created:
        await inter.edit_original_response(content=f"Your ticket is open: {result.channel.mention}")
    else:
        await inter.edit_original_response(
            content=f"You already have this ticket open: {result.channel.mention}"
        )


async def on_close(inter: disnake.MessageInteraction, ticket_id: int) -> None:
    """Ask before closing: it deletes the channel."""
    pool = inter.bot.pool
    ticket = await storage.get_ticket(pool, ticket_id)
    if ticket is None or ticket["guild_id"] != inter.guild.id or ticket["closed_at"] is not None:
        await inter.response.send_message("This ticket is already closed.", ephemeral=True)
        return
    if not await service.can_close(pool, inter.author):
        await inter.response.send_message(
            "Only staff can close a ticket. Let them know when you're done.", ephemeral=True
        )
        return
    await inter.response.send_message(
        "Close this ticket? The conversation is saved to the ticket log, then this channel "
        "is deleted.",
        components=[render.confirm_close_row(ticket_id)],
        ephemeral=True,
    )


async def on_confirm(inter: disnake.MessageInteraction, ticket_id: int) -> None:
    pool = inter.bot.pool
    # Checked again: the confirmation could have been open while a role changed.
    if not await service.can_close(pool, inter.author):
        await inter.response.send_message("Only staff can close a ticket.", ephemeral=True)
        return
    await inter.response.edit_message(content="Saving the transcript…", components=[])
    try:
        await service.close_ticket(pool, inter.guild, ticket_id, inter.author)
    except TicketError as exc:
        await inter.edit_original_response(content=f"⚠️ {exc}")
    # On success the channel (and this reply with it) is gone; nothing to say.


BUTTON_HANDLERS = {
    render.ACTION_OPEN: on_open,
    render.ACTION_CLOSE: on_close,
    render.ACTION_CONFIRM: on_confirm,
}


async def _report_failure(inter: disnake.MessageInteraction) -> None:
    message = "⚠️ Something went wrong — please try again, or tell an admin."
    try:
        if inter.response.is_done():
            await inter.followup.send(message, ephemeral=True)
        else:
            await inter.response.send_message(message, ephemeral=True)
    except disnake.HTTPException:
        logger.exception("Also failed to report a ticket button failure")


class TicketCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    # --- listeners ------------------------------------------------------------

    @commands.Cog.listener("on_button_click")
    async def on_button_click(self, inter: disnake.MessageInteraction) -> None:
        parsed = render.parse_custom_id(inter.data.custom_id if inter.data else None)
        if parsed is None:
            return  # not one of ours
        action, target_id = parsed
        handler = BUTTON_HANDLERS.get(action)
        if handler is None or inter.guild is None:
            return
        try:
            await handler(inter, target_id)
        except Exception:  # noqa: BLE001 — a click must never fail silently
            logger.exception("Ticket button %s:%s failed", action, target_id)
            await _report_failure(inter)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: disnake.abc.GuildChannel) -> None:
        """A ticket channel deleted by hand closes its ticket.

        Otherwise the row would stay open and the member could never open that
        ticket type again. A no-op for the bot's own deletions (already closed)
        and for every non-ticket channel.
        """
        ticket = await storage.close_by_channel(self.bot.pool, channel.id)
        if ticket is not None:
            logger.info("Ticket id=%s closed because its channel was deleted", ticket["id"])

    # --- /ticket --------------------------------------------------------------

    @commands.slash_command(
        name="ticket",
        description="Admin: set up and manage tickets for this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def ticket(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Parent group; disnake never invokes this directly."""

    @ticket.sub_command(
        name="setup", description="Set the open-ticket channel, the tickets category and the log channel."
    )
    async def setup_cmd(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(
            description="Where the message with the ticket buttons sits."
        ),
        category: disnake.CategoryChannel = commands.Param(
            description="The category new ticket channels are created under."
        ),
        log_channel: disnake.TextChannel = commands.Param(
            description="Where closed tickets' transcripts are saved (keep it staff-only)."
        ),
    ) -> None:
        problems = service.setup_problems(inter.guild, channel, category, log_channel)
        if problems:
            await inter.response.send_message(
                "I can't run tickets with that yet:\n" + "\n".join(f"- {p}" for p in problems),
                ephemeral=True,
            )
            return
        before = await storage.get_config(self.bot.pool, inter.guild.id)
        await storage.set_locations(
            self.bot.pool,
            inter.guild.id,
            panel_channel_id=channel.id,
            category_id=category.id,
            log_channel_id=log_channel.id,
        )
        lines = [
            f"Tickets are set up: buttons in {channel.mention}, ticket channels under "
            f"**{category.name}**, transcripts to {log_channel.mention}."
        ]
        if log_channel.permissions_for(inter.guild.default_role).view_channel:
            lines.append(
                f"⚠️ Everyone can see {log_channel.mention}, so everyone could read the "
                "transcripts. Make it staff-only."
            )
        moved = (
            before is not None
            and before.get("panel_message_id")
            and before.get("panel_channel_id") != channel.id
        )
        if moved:
            lines.append(
                f"The old panel in <#{before['panel_channel_id']}> still works — delete it, "
                "then run `/ticket panel` to post it in the new channel."
            )
        else:
            lines.append(NEXT_STEPS)
        await inter.response.send_message("\n".join(lines), ephemeral=True)

    @ticket.sub_command(
        name="panel", description="Write the message with the ticket buttons and post or update it."
    )
    async def panel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        image: disnake.Attachment = commands.Param(
            default=None, description="A banner shown full width under the text."
        ),
        remove_image: bool = commands.Param(default=False, description="Take the current banner off."),
        thumbnail: disnake.Attachment = commands.Param(
            default=None, description="A small picture top-right, beside the text."
        ),
        remove_thumbnail: bool = commands.Param(
            default=False, description="Take the current thumbnail off."
        ),
    ) -> None:
        config = await storage.get_config(self.bot.pool, inter.guild.id)
        if config is None:
            await inter.response.send_message("Run `/ticket setup` first.", ephemeral=True)
            return
        try:
            check_image(image)
            check_image(thumbnail)
        except TicketError as exc:
            await inter.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        async def submit(modal_inter: disnake.ModalInteraction, title, body) -> None:
            title, body = render.validate_content(title, body)
            guild = modal_inter.guild
            filename = await apply_image(
                guild.id, images.PANEL_STEM, config.get("panel_image"), image, remove_image
            )
            thumb = await apply_image(
                guild.id,
                images.PANEL_THUMBNAIL_STEM,
                config.get("panel_thumbnail"),
                thumbnail,
                remove_thumbnail,
            )
            await storage.set_panel_content(
                self.bot.pool, guild.id, title=title, body=body, image=filename, thumbnail=thumb
            )
            message = await service.publish_panel(self.bot.pool, guild)
            await modal_inter.edit_original_response(content=f"The panel is up: {message.jump_url}")

        await inter.response.send_modal(
            ContentModal(
                heading="Ticket panel",
                title_value=config.get("panel_title"),
                body_value=config.get("panel_body"),
                on_submit=submit,
                custom_id=f"{render.CUSTOM_ID_PREFIX}:panelmodal:{inter.id}",
            )
        )

    # --- /ticket type ---------------------------------------------------------

    @ticket.sub_command_group(name="type")
    async def type_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Subcommand group; disnake never invokes this directly."""

    @type_group.sub_command(name="add", description="Add a kind of ticket: a button on the panel and its opening message.")
    async def type_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(
            max_length=render.MAX_NAME_LENGTH, description="The button's label, e.g. Support."
        ),
        emoji: str = commands.Param(
            default=None,
            description="An emoji for the button. Custom/animated: type its name, e.g. :spin:",
        ),
        image: disnake.Attachment = commands.Param(
            default=None, description="A banner shown at the top of every ticket of this type."
        ),
        thumbnail: disnake.Attachment = commands.Param(
            default=None, description="A small picture top-right, beside the ticket's text."
        ),
    ) -> None:
        pool = self.bot.pool
        try:
            name = render.validate_name(name)
            emoji = render.resolve_emoji(emoji, usable_emojis(self.bot, inter.guild))
            check_image(image)
            check_image(thumbnail)
            types = await storage.list_types(pool, inter.guild.id)
            if len(types) >= render.MAX_TICKET_TYPES:
                raise TicketError(
                    f"This server already has {render.MAX_TICKET_TYPES} ticket types, the most "
                    "that fit on one message. Remove one first."
                )
            if any(t["name"].lower() == name.lower() for t in types):
                raise TicketError(f"There's already a ticket type called **{name}**.")
        except TicketError as exc:
            await inter.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return

        async def submit(modal_inter: disnake.ModalInteraction, title, body) -> None:
            title, body = render.validate_content(title, body)
            guild = modal_inter.guild
            try:
                record = await storage.create_type(
                    pool,
                    guild.id,
                    name=name,
                    emoji=emoji,
                    title=title,
                    body=body,
                    created_by=modal_inter.author.id,
                )
            except storage.DuplicateTypeError as exc:
                raise TicketError(f"There's already a ticket type called **{name}**.") from exc

            notes: list[str] = []
            if image is not None or thumbnail is not None:
                # Files are named after the type's id, so they're saved second.
                filename = thumb = None
                try:
                    filename = await apply_image(
                        guild.id, images.type_stem(record["id"]), None, image, False
                    )
                except TicketError as exc:
                    notes.append(f"⚠️ Saved without the banner: {exc}")
                try:
                    thumb = await apply_image(
                        guild.id, images.type_thumbnail_stem(record["id"]), None, thumbnail, False
                    )
                except TicketError as exc:
                    notes.append(f"⚠️ Saved without the thumbnail: {exc}")
                if filename or thumb:
                    record = await storage.update_type(
                        pool,
                        record["id"],
                        name=name,
                        emoji=emoji,
                        title=title,
                        body=body,
                        image=filename,
                        thumbnail=thumb,
                    ) or record
            note = await service.refresh_panel(pool, guild)
            if note:
                notes.append(note)
            await modal_inter.edit_original_response(
                content="\n".join([f"Added **{name}**. This is what its tickets open with:"] + notes)
            )
            await _send_preview(modal_inter, record)

        await inter.response.send_modal(
            ContentModal(
                heading=f"New ticket type: {name}",
                title_value=None,
                body_value=None,
                on_submit=submit,
                custom_id=f"{render.CUSTOM_ID_PREFIX}:typemodal:{inter.id}",
            )
        )

    @type_group.sub_command(name="edit", description="Change a ticket type's name, emoji, pictures or opening message.")
    async def type_edit(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_type_autocomplete, description="The ticket type."),
        new_name: str = commands.Param(
            default=None, max_length=render.MAX_NAME_LENGTH, description="Rename the button."
        ),
        emoji: str = commands.Param(
            default=None,
            description="A new emoji for the button. Custom/animated: type its name, e.g. :spin:",
        ),
        remove_emoji: bool = commands.Param(default=False, description="Take the emoji off the button."),
        image: disnake.Attachment = commands.Param(default=None, description="A new banner."),
        remove_image: bool = commands.Param(default=False, description="Take the current banner off."),
        thumbnail: disnake.Attachment = commands.Param(
            default=None, description="A new small picture top-right, beside the text."
        ),
        remove_thumbnail: bool = commands.Param(
            default=False, description="Take the current thumbnail off."
        ),
    ) -> None:
        pool = self.bot.pool
        record = await storage.get_type_by_name(pool, inter.guild.id, name)
        if record is None:
            await inter.response.send_message(
                "There's no ticket type with that name. See `/ticket type list`.", ephemeral=True
            )
            return
        try:
            final_name = render.validate_name(new_name) if new_name else record["name"]
            new_emoji = render.resolve_emoji(emoji, usable_emojis(self.bot, inter.guild))
            check_image(image)
            check_image(thumbnail)
        except TicketError as exc:
            await inter.response.send_message(f"⚠️ {exc}", ephemeral=True)
            return
        final_emoji = None if remove_emoji else (new_emoji or record.get("emoji"))

        async def submit(modal_inter: disnake.ModalInteraction, title, body) -> None:
            title, body = render.validate_content(title, body)
            guild = modal_inter.guild
            filename = await apply_image(
                guild.id, images.type_stem(record["id"]), record.get("image"), image, remove_image
            )
            thumb = await apply_image(
                guild.id,
                images.type_thumbnail_stem(record["id"]),
                record.get("thumbnail"),
                thumbnail,
                remove_thumbnail,
            )
            try:
                updated = await storage.update_type(
                    pool,
                    record["id"],
                    name=final_name,
                    emoji=final_emoji,
                    title=title,
                    body=body,
                    image=filename,
                    thumbnail=thumb,
                )
            except storage.DuplicateTypeError as exc:
                raise TicketError(f"There's already a ticket type called **{final_name}**.") from exc
            if updated is None:
                raise TicketError("That ticket type was removed while you were editing it.")
            lines = [
                f"Updated **{final_name}**. New tickets open with this; tickets already open "
                "keep the message they have."
            ]
            note = await service.refresh_panel(pool, guild)
            if note:
                lines.append(note)
            await modal_inter.edit_original_response(content="\n".join(lines))
            await _send_preview(modal_inter, updated)

        await inter.response.send_modal(
            ContentModal(
                heading=f"Edit ticket type: {record['name']}",
                title_value=record.get("title"),
                body_value=record.get("body"),
                on_submit=submit,
                custom_id=f"{render.CUSTOM_ID_PREFIX}:typemodal:{inter.id}",
            )
        )

    @type_group.sub_command(name="remove", description="Remove a ticket type and its button.")
    async def type_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_type_autocomplete, description="The ticket type."),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        pool = self.bot.pool
        record = await storage.get_type_by_name(pool, inter.guild.id, name)
        if record is None or not await storage.remove_type(pool, inter.guild.id, record["id"]):
            await inter.edit_original_response(
                content="There's no ticket type with that name. See `/ticket type list`."
            )
            return
        await images.delete(inter.guild.id, record.get("image"))
        await images.delete(inter.guild.id, record.get("thumbnail"))
        lines = [
            f"Removed **{record['name']}**. Tickets of that type that are already open stay "
            "open and can still be closed."
        ]
        note = await service.refresh_panel(pool, inter.guild)
        if note:
            lines.append(note)
        await inter.edit_original_response(content="\n".join(lines))

    @type_group.sub_command(name="list", description="List this server's ticket types.")
    async def type_list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        types = await storage.list_types(self.bot.pool, inter.guild.id)
        if not types:
            await inter.response.send_message(
                "No ticket types yet — add one with `/ticket type add`.", ephemeral=True
            )
            return
        lines = []
        for t in types:
            label = f"{t['emoji']} " if t.get("emoji") else ""
            extras = [t["title"]] if t.get("title") else []
            if t.get("image"):
                extras.append("has a banner")
            if t.get("thumbnail"):
                extras.append("has a thumbnail")
            lines.append(f"- {label}**{t['name']}**" + (f" — {', '.join(extras)}" if extras else ""))
        await inter.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=NO_PINGS)

    # --- /ticket role ---------------------------------------------------------

    @ticket.sub_command_group(name="role")
    async def role_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Subcommand group; disnake never invokes this directly."""

    @role_group.sub_command(name="add", description="Let a role see every ticket and close them.")
    async def role_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        role: disnake.Role = commands.Param(description="The staff role."),
    ) -> None:
        pool = self.bot.pool
        if role.is_default():
            await inter.response.send_message(
                "@everyone can't be ticket staff — tickets would stop being private.", ephemeral=True
            )
            return
        current = await storage.list_staff_roles(pool, inter.guild.id)
        if role.id not in current and len(current) >= render.MAX_STAFF_ROLES:
            await inter.response.send_message(
                f"This server already has {render.MAX_STAFF_ROLES} ticket staff roles. "
                "Remove one first.",
                ephemeral=True,
            )
            return
        added = await storage.add_staff_role(pool, inter.guild.id, role.id, inter.author.id)
        if not added:
            await inter.response.send_message(
                f"{role.mention} is already ticket staff.", ephemeral=True, allowed_mentions=NO_PINGS
            )
            return
        lines = [
            f"{role.mention} is now ticket staff: it can close tickets right away, and it will "
            "see and be pinged in every ticket opened from now on. Tickets already open aren't "
            "changed."
        ]
        if not role.mentionable and not inter.guild.me.guild_permissions.mention_everyone:
            lines.append(
                "⚠️ That role isn't mentionable and I don't have Mention Everyone, so new "
                "tickets won't notify it until one of those changes."
            )
        await inter.response.send_message("\n".join(lines), ephemeral=True, allowed_mentions=NO_PINGS)

    @role_group.sub_command(name="remove", description="Stop a role from seeing and closing tickets.")
    async def role_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        role: disnake.Role = commands.Param(description="The staff role."),
    ) -> None:
        removed = await storage.remove_staff_role(self.bot.pool, inter.guild.id, role.id)
        if not removed:
            await inter.response.send_message(
                f"{role.mention} isn't ticket staff. See `/ticket role list`.",
                ephemeral=True,
                allowed_mentions=NO_PINGS,
            )
            return
        await inter.response.send_message(
            f"{role.mention} is no longer ticket staff: it can't close tickets, and won't be "
            "added to new ones. It can still see tickets that are already open.",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

    @role_group.sub_command(name="list", description="List the roles that can see and close tickets.")
    async def role_list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        role_ids = await storage.list_staff_roles(self.bot.pool, inter.guild.id)
        if not role_ids:
            await inter.response.send_message(
                "No ticket staff roles — only admins can see and close tickets. "
                "Add one with `/ticket role add`.",
                ephemeral=True,
            )
            return
        await inter.response.send_message(
            "Ticket staff: " + ", ".join(f"<@&{rid}>" for rid in role_ids),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(TicketCommands(bot))
