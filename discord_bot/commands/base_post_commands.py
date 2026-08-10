"""/postbase — post a CoC base layout whose link is behind a Fetch Link button.

The layout link is never rendered in the message; members reveal it (ephemerally)
by pressing Fetch Link, and every distinct presser is counted. The buttons are a
*persistent* view: custom_ids carry the base_posts row id and the view has no
timeout, so they keep working after a bot restart (see register_persistent_views).
"""
from __future__ import annotations

import logging as _logging
from typing import Union

import disnake
from disnake.ext import commands

from modules.announce import base_storage
from modules.announce.base_poster import (
    MAX_CC_LENGTH,
    MAX_DESCRIPTION_LENGTH,
    MAX_LINK_LENGTH,
    MAX_TITLE_LENGTH,
    PostError,
    build_base_embed,
    downloaders_embed,
    embed_from_record,
    link_embed,
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


class BaseEditModal(disnake.ui.Modal):
    """Pre-filled modal for editing a base post's text fields and image URL."""

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
                label="Image URL (leave as-is to keep current)",
                custom_id="image_url",
                value=record["image_url"] or "",
                required=False,
                max_length=MAX_LINK_LENGTH,
                style=disnake.TextInputStyle.short,
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
            image_url=values.get("image_url", "") or "",
        )
        if updated is None:
            await inter.response.send_message("That base post no longer exists.", ephemeral=True)
            return

        author = inter.guild.get_member(updated["author_id"]) if inter.guild else None
        embed = embed_from_record(updated, author=author)
        count = await base_storage.count_downloads(pool, self.base_post_id)
        view = BasePostView(
            self.base_post_id, count, stats_admin_only=bool(updated["stats_admin_only"])
        )

        try:
            await inter.response.edit_message(embed=embed, view=view)
        except disnake.HTTPException:
            logger.exception("Failed to apply base post edit id=%s", self.base_post_id)
            await inter.response.send_message("Couldn't update the post.", ephemeral=True)


class BasePostView(disnake.ui.View):
    """The three buttons under a base post: Fetch Link, Edit, N Downloads.

    Persistent (timeout=None) with deterministic custom_ids, so a restarted bot
    can re-attach handlers to messages it posted in a previous run.
    """

    def __init__(
        self,
        base_post_id: int,
        download_count: int = 0,
        stats_admin_only: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.base_post_id = base_post_id
        self.stats_admin_only = stats_admin_only

        fetch = disnake.ui.Button(
            label="Fetch Link",
            emoji="🔗",
            style=disnake.ButtonStyle.secondary,
            custom_id=f"{CUSTOM_ID_PREFIX}:fetch:{base_post_id}",
        )
        fetch.callback = self._on_fetch
        self.add_item(fetch)

        edit = disnake.ui.Button(
            label="Edit",
            style=disnake.ButtonStyle.primary,
            custom_id=f"{CUSTOM_ID_PREFIX}:edit:{base_post_id}",
        )
        edit.callback = self._on_edit
        self.add_item(edit)

        self.downloads_button = disnake.ui.Button(
            label=self._downloads_label(download_count),
            style=disnake.ButtonStyle.primary,
            custom_id=f"{CUSTOM_ID_PREFIX}:downloads:{base_post_id}",
        )
        self.downloads_button.callback = self._on_downloads
        self.add_item(self.downloads_button)

    def _downloads_label(self, count: int) -> str:
        """Button text. Every viewer shares one component payload, so when stats
        are admin-only the number is omitted entirely — a label like "24
        Downloads" would leak the tally to exactly the people it's hidden from.
        """
        return "Downloads" if self.stats_admin_only else f"{count} Downloads"

    def _id_from(self, inter: disnake.MessageInteraction) -> int:
        """The base post id encoded in the clicked button's custom_id.

        A persistent view is matched by custom_id, but the callback runs on
        whichever registered instance disnake dispatches to — its
        `self.base_post_id` is NOT necessarily the clicked post's. Always take
        the id from the interaction, or clicks land on the wrong row.
        """
        custom_id = (inter.data.custom_id or "") if inter.data else ""
        try:
            return int(custom_id.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            logger.warning("Unparsable base post custom_id %r", custom_id)
            return self.base_post_id

    async def _load(self, inter: disnake.MessageInteraction):
        record = await base_storage.get_base_post(inter.bot.pool, self._id_from(inter))
        if record is None:
            await inter.response.send_message(
                "That base post's data is gone — it may have been deleted.", ephemeral=True
            )
        return record

    async def _on_fetch(self, inter: disnake.MessageInteraction) -> None:
        """Reveal the link to the presser only, and count them once."""
        record = await self._load(inter)
        if record is None:
            return

        base_post_id = self._id_from(inter)
        count = await base_storage.record_download(
            inter.bot.pool, base_post_id, inter.author.id
        )
        # An ephemeral message, not a modal: only a message renders the link as
        # a real clickable hyperlink and is read-only. A modal body can only be
        # a TextInput, which is always editable.
        await inter.response.send_message(
            embed=link_embed(record["link"]), ephemeral=True, allowed_mentions=NO_PINGS
        )

        # Keep the view in step with the stored flag; a restored view can be
        # stale if the post's visibility changed after this process started.
        self.stats_admin_only = bool(record["stats_admin_only"])

        # Refresh the counter on the public message; a failure here is cosmetic.
        # When stats are admin-only the label carries no number, so there's
        # nothing to refresh and editing would only burn an API call.
        new_label = self._downloads_label(count)
        if new_label != self.downloads_button.label:
            self.downloads_button.label = new_label
            try:
                await inter.message.edit(view=self)
            except disnake.HTTPException:
                logger.warning(
                    "Couldn't refresh download count on base post id=%s", base_post_id
                )

    async def _on_edit(self, inter: disnake.MessageInteraction) -> None:
        record = await self._load(inter)
        if record is None:
            return
        if not _can_edit(inter.author, record):
            await inter.response.send_message(
                "Only the person who posted this base (or a moderator) can edit it.",
                ephemeral=True,
            )
            return
        await inter.response.send_modal(BaseEditModal(record))

    async def _on_downloads(self, inter: disnake.MessageInteraction) -> None:
        """Show the presser a private list of everyone who fetched the link.

        Gated on the post's stats_admin_only flag, read fresh from the DB so a
        stale restored view can't leak the tally.
        """
        record = await self._load(inter)
        if record is None:
            return
        if not _can_view_stats(inter.author, record):
            await inter.response.send_message(
                "Only moderators can view download stats for this base.", ephemeral=True
            )
            return

        rows = await base_storage.list_downloaders(inter.bot.pool, self._id_from(inter))
        await inter.response.send_message(
            embed=downloaders_embed(rows), ephemeral=True, allowed_mentions=NO_PINGS
        )


class BasePostCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

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
        except PostError as exc:
            await self._respond(inter, str(exc))
            return

        try:
            file = await image.to_file()
        except disnake.HTTPException as exc:
            await self._respond(inter, f"Couldn't download the uploaded image: {exc}")
            return
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

        embed = build_base_embed(
            title=cleaned["title"],
            cc=cleaned["cc"],
            description=cleaned["description"],
            image_filename=file.filename,
            author=inter.author,
        )
        view = BasePostView(
            record["id"], download_count=0, stats_admin_only=admin_only_stats
        )

        try:
            message = await target.send(
                embed=embed, file=file, view=view, allowed_mentions=NO_PINGS
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

        # The uploaded attachment is now hosted by Discord; store its URL so
        # edits can re-render the embed without re-uploading the file.
        if message.embeds and message.embeds[0].image:
            await base_storage.update_base_post(
                self.bot.pool, record["id"], image_url=message.embeds[0].image.url
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


async def register_persistent_views(bot: commands.InteractionBot) -> None:
    """Re-attach button handlers to base posts from previous bot runs.

    Called once after login. Without this, buttons on old messages do nothing
    ("This interaction failed") because the in-memory View is gone.
    """
    try:
        rows = await base_storage.list_views_to_restore(bot.pool)
    except Exception:  # noqa: BLE001 — never block startup on this
        logger.exception("Couldn't load base posts to restore views")
        return

    for row in rows:
        bot.add_view(
            BasePostView(
                row["id"],
                int(row["download_count"]),
                stats_admin_only=bool(row["stats_admin_only"]),
            )
        )
    if rows:
        logger.info("Restored %d base post view(s)", len(rows))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(BasePostCommands(bot))
