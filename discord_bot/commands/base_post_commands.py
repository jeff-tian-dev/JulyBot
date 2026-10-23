"""/postbase — post a CoC base layout whose link is behind a Fetch Link button.

The layout link is never rendered in the message; members reveal it (ephemerally)
by pressing Fetch Link, and every distinct presser is counted.

**The post is a components-v2 message**: one pink-accented container holding the
title, a separator, the CC + notes, the screenshot as a full-width media
gallery, and the buttons. It replaced an embed because Discord renders embed
images small and offers no way to enlarge them; a media gallery is full width.

**Buttons are handled by one stateless listener** (`on_button_click`), routed
purely by custom_id (`basepost:<action>:<base_post_id>`), not by a persistent
View. That works for every base post ever made — components-v2, the brief
embed version, and the original plain messages — and needs nothing restored on
startup. Do not ALSO register a persistent View for these ids: both would
answer the same click and the second response would fail.
"""
from __future__ import annotations

import logging as _logging
from typing import Union

import disnake
from disnake.ext import commands

from modules.announce import base_storage
from modules.announce.base_poster import (
    BASE_POST_COLOUR,
    MAX_CC_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_LINK_LENGTH,
    MAX_TITLE_LENGTH,
    PostError,
    build_post_sections,
    embed_from_record,
    downloaders_embed,
    normalize_text,
    validate_base_input,
    validate_image,
    validate_target,
)
from modules.announce.poster import safe_filename

logger = _logging.getLogger(__name__)

NO_PINGS = disnake.AllowedMentions.none()
# Prefix for every custom_id this feature owns, so the ids stay unambiguous.
CUSTOM_ID_PREFIX = "basepost"
ACTION_FETCH = "fetch"
ACTION_EDIT = "edit"
ACTION_DOWNLOADS = "downloads"


def _is_moderator(user: disnake.abc.User) -> bool:
    """True if the user has Manage Messages or administrator in this guild."""
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_messages))


def _can_edit(user: disnake.abc.User, record) -> bool:
    """The original poster, or anyone with Manage Messages / admin in the guild."""
    return user.id == record["author_id"] or _is_moderator(user)


def _can_view_stats(user: disnake.abc.User, record) -> bool:
    """Who may see the download count and downloader list.

    Open to everyone unless the post was created with stats_admin_only, in which
    case it's moderators only. The author is deliberately NOT exempt — the point
    of the flag is to keep the tally from the wider channel, and the author can
    still see it if they're a moderator.
    """
    if not record["stats_admin_only"]:
        return True
    return _is_moderator(user)


def parse_custom_id(custom_id: str | None) -> tuple[str, int] | None:
    """(action, base_post_id) for one of our buttons, or None if it isn't ours.

    The id always comes from the clicked button — the same rule every
    persistent button in this repo follows, since nothing else reliably says
    which post was clicked.
    """
    parts = (custom_id or "").split(":")
    if len(parts) != 3 or parts[0] != CUSTOM_ID_PREFIX:
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


def downloads_label(count: int, *, stats_admin_only: bool) -> str:
    """Button text. Every viewer shares one component payload, so when stats are
    admin-only the number is omitted entirely — a label like "24 Downloads"
    would leak the tally to exactly the people it's hidden from.
    """
    return "Downloads" if stats_admin_only else f"{count} Downloads"


def post_buttons(
    base_post_id: int, *, download_count: int, stats_admin_only: bool
) -> disnake.ui.ActionRow:
    """Fetch Link / Edit / N Downloads, as one row. Handled by the listener."""
    return disnake.ui.ActionRow(
        disnake.ui.Button(
            label="Fetch Link",
            emoji="🔗",
            style=disnake.ButtonStyle.secondary,
            custom_id=f"{CUSTOM_ID_PREFIX}:{ACTION_FETCH}:{base_post_id}",
        ),
        disnake.ui.Button(
            label="Edit",
            style=disnake.ButtonStyle.primary,
            custom_id=f"{CUSTOM_ID_PREFIX}:{ACTION_EDIT}:{base_post_id}",
        ),
        disnake.ui.Button(
            label=downloads_label(download_count, stats_admin_only=stats_admin_only),
            style=disnake.ButtonStyle.primary,
            custom_id=f"{CUSTOM_ID_PREFIX}:{ACTION_DOWNLOADS}:{base_post_id}",
        ),
    )


def build_post_components(
    record, *, download_count: int, image_filename: str | None
) -> list[disnake.ui.Container]:
    """The whole base post: one pink-accented container.

    The screenshot is `attachment://<image_filename>` on send AND on edit. On
    edit the upload is kept by not passing `attachments`, and the reference
    resolves against it. Never swap in the CDN URL: it's signed and expires, so
    the picture would vanish later.
    """
    heading, body = build_post_sections(
        title=record["title"], cc=record["cc"], description=record["description"]
    )
    children: list = []
    if heading:
        children.append(disnake.ui.TextDisplay(heading))
        children.append(disnake.ui.Separator())
    if body:
        children.append(disnake.ui.TextDisplay(body))
    if image_filename:
        children.append(
            disnake.ui.MediaGallery(disnake.MediaGalleryItem(f"attachment://{image_filename}"))
        )
    children.append(
        post_buttons(
            record["id"],
            download_count=download_count,
            stats_admin_only=bool(record["stats_admin_only"]),
        )
    )
    return [disnake.ui.Container(*children, accent_colour=disnake.Colour(BASE_POST_COLOUR))]


def _is_components_v2(message: disnake.Message | None) -> bool:
    flags = getattr(message, "flags", None)
    return bool(flags and flags.is_components_v2)


def _image_filename(record, message: disnake.Message | None) -> str | None:
    """The attached screenshot's filename, for the attachment:// reference.

    Prefer the live message's attachment (it is what the reference must match),
    falling back to the stored name.
    """
    attachments = getattr(message, "attachments", None) or []
    if attachments:
        return attachments[0].filename
    return record["image_filename"]


def render_kwargs(record, *, message: disnake.Message | None, download_count: int) -> dict:
    """Edit kwargs that re-render a base post in whichever layout it already has.

    A message can't be switched into the components layout after it was sent,
    so posts made before it keep their embed: `content=None` (clearing the
    text of the original plain-message posts), the embed, and a top-level
    button row. Components-v2 posts are rebuilt whole.
    """
    filename = _image_filename(record, message)
    if _is_components_v2(message):
        return {
            "components": build_post_components(
                record, download_count=download_count, image_filename=filename
            )
        }
    return {
        "content": None,
        "embed": embed_from_record(record, image_filename=filename),
        "components": [
            post_buttons(
                record["id"],
                download_count=download_count,
                stats_admin_only=bool(record["stats_admin_only"]),
            )
        ],
    }


def refresh_kwargs(record, *, message: disnake.Message | None, download_count: int) -> dict:
    """Edit kwargs that update ONLY the download count, leaving the post's look alone.

    Unlike render_kwargs (used by Edit), this must not restyle an old post just
    because someone fetched its link — so a pre-components post only has its
    button row swapped. A components-v2 post has no separate button row (the
    buttons live inside the container), so it is rebuilt whole.
    """
    if _is_components_v2(message):
        return render_kwargs(record, message=message, download_count=download_count)
    return {
        "components": [
            post_buttons(
                record["id"],
                download_count=download_count,
                stats_admin_only=bool(record["stats_admin_only"]),
            )
        ]
    }


class InfoModal(disnake.ui.Modal):
    """A popup panel that displays text without any editable field.

    The body is a `TextDisplay` (component type 10), which is static text —
    NOT a `TextInput`, which is always editable and would let the viewer type
    over the content. Discord still draws its own Cancel/Submit row and the
    "this form will be submitted" banner; those can't be removed.
    """

    def __init__(self, *, title: str, body: str, custom_id: str) -> None:
        super().__init__(
            title=title[:45],  # Discord truncates modal titles past 45 chars.
            custom_id=custom_id,
            components=[disnake.ui.TextDisplay(content=body[:4000])],
        )

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        # Nothing to save. `with_message` defaults to True on a modal submit,
        # which promises a follow-up message and leaves a "JulyBot is
        # thinking..." placeholder hanging forever. False sends a bare
        # deferred_message_update instead, so the popup just closes.
        await inter.response.defer(with_message=False)


class BaseEditModal(disnake.ui.Modal):
    """Pre-filled modal for editing a base post's text fields and link."""

    def __init__(self, record) -> None:
        self.base_post_id = record["id"]
        components = [
            disnake.ui.TextInput(
                label="Title",
                custom_id="title",
                value=record["title"] or "",
                required=False,
                max_length=MAX_TITLE_LENGTH,
                style=disnake.TextInputStyle.short,
            ),
            disnake.ui.TextInput(
                label="CC",
                custom_id="cc",
                value=record["cc"] or "",
                required=False,
                max_length=MAX_CC_LENGTH,
                style=disnake.TextInputStyle.short,
            ),
            disnake.ui.TextInput(
                label="Description (Notes)",
                custom_id="description",
                value=record["description"] or "",
                required=False,
                max_length=MAX_DESCRIPTION_LENGTH,
                style=disnake.TextInputStyle.paragraph,
            ),
            disnake.ui.TextInput(
                label="Layout link",
                custom_id="link",
                value=record["link"] or "",
                required=False,
                max_length=MAX_LINK_LENGTH,
                style=disnake.TextInputStyle.short,
            ),
        ]
        super().__init__(
            title="Edit base post",
            custom_id=f"{CUSTOM_ID_PREFIX}:modal:{record['id']}",
            components=components,
        )

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        values = inter.text_values
        pool = inter.bot.pool

        # A blank field means "clear it"; storage maps "" -> NULL. The link is
        # the exception: an empty link would break Fetch Link, so it's kept.
        link = normalize_text(values.get("link")) or None
        try:
            cleaned = validate_base_input(
                link=link or "https://placeholder.invalid",
                title=values.get("title"),
                cc=values.get("cc"),
                description=values.get("description"),
            )
            # Check the combined length BEFORE saving, so an over-long edit is
            # rejected rather than stored and then impossible to render.
            build_post_sections(
                title=cleaned["title"], cc=cleaned["cc"], description=cleaned["description"]
            )
        except PostError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return

        updated = await base_storage.update_base_post(
            pool,
            self.base_post_id,
            title=values.get("title", "") or "",
            cc=values.get("cc", "") or "",
            description=values.get("description", "") or "",
            link=cleaned["link"] if link else None,
        )
        if updated is None:
            await inter.response.send_message("That base post no longer exists.", ephemeral=True)
            return

        count = await base_storage.count_downloads(pool, self.base_post_id)
        try:
            # Attachments are not passed, so the upload is kept and the
            # attachment:// reference keeps pointing at it.
            await inter.response.edit_message(
                **render_kwargs(updated, message=inter.message, download_count=count)
            )
        except (disnake.HTTPException, PostError):
            logger.exception("Failed to apply base post edit id=%s", self.base_post_id)
            # edit_message may have already consumed the response slot, in which
            # case send_message would raise InteractionResponded and hide the
            # real error — fall back to a follow-up.
            try:
                if inter.response.is_done():
                    await inter.followup.send("Couldn't update the post.", ephemeral=True)
                else:
                    await inter.response.send_message(
                        "Couldn't update the post.", ephemeral=True
                    )
            except disnake.HTTPException:
                logger.exception("Also failed to report the edit failure")


async def _load(inter: disnake.MessageInteraction, base_post_id: int):
    record = await base_storage.get_base_post(inter.bot.pool, base_post_id)
    if record is None:
        await inter.response.send_message(
            "That base post's data is gone — it may have been deleted.", ephemeral=True
        )
    return record


async def on_fetch(inter: disnake.MessageInteraction, base_post_id: int) -> None:
    """Reveal the link to the presser only, and count them once."""
    record = await _load(inter, base_post_id)
    if record is None:
        return

    count = await base_storage.record_download(inter.bot.pool, base_post_id, inter.author.id)
    # A popup panel built from a static TextDisplay, so nothing is editable.
    # A modal must be the FIRST response to an interaction, so it's sent before
    # the counter refresh below (which edits the message, not the interaction,
    # and so doesn't consume the response slot).
    await inter.response.send_modal(
        InfoModal(
            title="Copy Layout",
            body=f"Click the link below to copy the layout:\n\n{record['link']}",
            custom_id=f"{CUSTOM_ID_PREFIX}:linkmodal:{base_post_id}",
        )
    )

    # When stats are admin-only the label carries no number, so there's nothing
    # to refresh and editing would only burn an API call.
    if record["stats_admin_only"]:
        return
    try:
        await inter.message.edit(
            **refresh_kwargs(record, message=inter.message, download_count=count)
        )
    except (disnake.HTTPException, PostError):
        # Cosmetic — the fetch itself already succeeded.
        logger.warning("Couldn't refresh download count on base post id=%s", base_post_id)


async def on_edit(inter: disnake.MessageInteraction, base_post_id: int) -> None:
    record = await _load(inter, base_post_id)
    if record is None:
        return
    if not _can_edit(inter.author, record):
        await inter.response.send_message(
            "Only the person who posted this base (or a moderator) can edit it.",
            ephemeral=True,
        )
        return
    await inter.response.send_modal(BaseEditModal(record))


async def on_downloads(inter: disnake.MessageInteraction, base_post_id: int) -> None:
    """Show the presser a private list of everyone who fetched the link.

    Gated on the post's stats_admin_only flag, read fresh from the DB.
    """
    record = await _load(inter, base_post_id)
    if record is None:
        return
    if not _can_view_stats(inter.author, record):
        await inter.response.send_message(
            "Only moderators can view download stats for this base.", ephemeral=True
        )
        return

    rows = await base_storage.list_downloaders(inter.bot.pool, base_post_id)
    await inter.response.send_message(
        embed=downloaders_embed(rows), ephemeral=True, allowed_mentions=NO_PINGS
    )


BUTTON_HANDLERS = {
    ACTION_FETCH: on_fetch,
    ACTION_EDIT: on_edit,
    ACTION_DOWNLOADS: on_downloads,
}


class BasePostCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.Cog.listener("on_button_click")
    async def on_button_click(self, inter: disnake.MessageInteraction) -> None:
        """Route every base post button, for posts from any process or layout."""
        parsed = parse_custom_id(inter.data.custom_id if inter.data else None)
        if parsed is None:
            return  # not one of ours
        action, base_post_id = parsed
        handler = BUTTON_HANDLERS.get(action)
        if handler is None:
            logger.warning("Unknown base post button action %r", action)
            return
        await handler(inter, base_post_id)

    @commands.slash_command(
        name="postbase",
        description="Post a base layout — the link is revealed via a Fetch Link button.",
        # Guild-only: the handler needs inter.guild for permission checks.
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def postbase(
        self,
        inter: disnake.ApplicationCommandInteraction,
        link: str = commands.Param(
            max_length=MAX_LINK_LENGTH,
            description="The base layout link (hidden behind the Fetch Link button).",
        ),
        image: disnake.Attachment = commands.Param(description="Screenshot of the base."),
        description: str = commands.Param(
            max_length=MAX_DESCRIPTION_LENGTH,
            description="Notes about the base (use \\n for a line break).",
        ),
        channel: Union[disnake.TextChannel, disnake.Thread] = commands.Param(
            default=None, description="Where to post it (defaults to this channel)."
        ),
        title: str = commands.Param(
            default=None, max_length=MAX_TITLE_LENGTH, description="Base name."
        ),
        cc: str = commands.Param(
            default=None, max_length=MAX_CC_LENGTH, description="Clan Castle troops."
        ),
        admin_only_stats: bool = commands.Param(
            default=False,
            description="Hide the download count and downloader list from non-moderators.",
        ),
    ) -> None:
        # Downloading + re-uploading the attachment can outlast the 3s deadline.
        await inter.response.defer(ephemeral=True)

        target = channel or inter.channel
        try:
            cleaned = validate_base_input(
                link=link, title=title, cc=cc, description=description
            )
            validate_image(image)
            validate_target(target, inter.guild)
            build_post_sections(
                title=cleaned["title"], cc=cleaned["cc"], description=cleaned["description"]
            )
        except PostError as exc:
            await self._respond(inter, str(exc))
            return

        try:
            file = await image.to_file()
        except disnake.HTTPException as exc:
            await self._respond(inter, f"Couldn't download the uploaded image: {exc}")
            return
        # The attachment:// reference must match File.filename exactly.
        file.filename = safe_filename(file.filename)

        record = await base_storage.create_base_post(
            self.bot.pool,
            guild_id=inter.guild.id,
            channel_id=target.id,
            author_id=inter.author.id,
            link=cleaned["link"],
            title=cleaned["title"],
            cc=cleaned["cc"],
            description=cleaned["description"],
            image_filename=file.filename,
            stats_admin_only=admin_only_stats,
        )

        components = build_post_components(
            record, download_count=0, image_filename=file.filename
        )

        try:
            message = await target.send(
                components=components, file=file, allowed_mentions=NO_PINGS
            )
        except disnake.HTTPException as exc:
            # Roll back so no orphan row is left pointing at a message that
            # never existed.
            await base_storage.delete_base_post(self.bot.pool, record["id"])
            await self._respond(inter, f"Failed to post the base: {exc.text or exc}")
            return
        except Exception as exc:  # noqa: BLE001 — surface any failure + log
            await base_storage.delete_base_post(self.bot.pool, record["id"])
            logger.exception("postbase failed for channel=%s", target.id)
            await self._respond(inter, f"Post failed: {type(exc).__name__}: {exc}")
            return

        await base_storage.attach_message(self.bot.pool, record["id"], message.id)

        # Record where Discord is hosting the upload — for reference/debugging
        # only. Rendering always uses attachment://, never this expiring URL.
        if message.attachments:
            await base_storage.update_base_post(
                self.bot.pool, record["id"], image_url=message.attachments[0].url
            )

        await self._respond(inter, f"Posted to {target.mention} — {message.jump_url}")

    @staticmethod
    async def _respond(inter: disnake.ApplicationCommandInteraction, content: str) -> None:
        """Reply whether or not the interaction was already deferred/responded."""
        try:
            if inter.response.is_done():
                await inter.edit_original_response(content=content, allowed_mentions=NO_PINGS)
            else:
                await inter.response.send_message(
                    content=content, ephemeral=True, allowed_mentions=NO_PINGS
                )
        except disnake.HTTPException:
            logger.exception("Failed to send /postbase response")


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(BasePostCommands(bot))
