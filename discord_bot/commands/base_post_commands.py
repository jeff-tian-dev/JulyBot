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
    embed_from_record,
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
# Downloader lists are chunked well under Discord's 2000-char message limit.
MAX_DOWNLOADER_LINES = 40


def _can_edit(user: disnake.abc.User, record) -> bool:
    """The original poster, or anyone with Manage Messages / admin in the guild."""
    if user.id == record["author_id"]:
        return True
    perms = getattr(user, "guild_permissions", None)
    return bool(perms and (perms.administrator or perms.manage_messages))


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
        view = BasePostView(self.base_post_id, count)

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

    def __init__(self, base_post_id: int, download_count: int = 0) -> None:
        super().__init__(timeout=None)
        self.base_post_id = base_post_id

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
            label=f"{download_count} Downloads",
            style=disnake.ButtonStyle.primary,
            custom_id=f"{CUSTOM_ID_PREFIX}:downloads:{base_post_id}",
        )
        self.downloads_button.callback = self._on_downloads
        self.add_item(self.downloads_button)

    async def _load(self, inter: disnake.MessageInteraction):
        record = await base_storage.get_base_post(inter.bot.pool, self.base_post_id)
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

        count = await base_storage.record_download(
            inter.bot.pool, self.base_post_id, inter.author.id
        )
        await inter.response.send_message(
            f"🔗 **Base layout link**\n{record['link']}",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )

        # Refresh the counter on the public message; a failure here is cosmetic.
        if count != self._current_count():
            self.downloads_button.label = f"{count} Downloads"
            try:
                await inter.message.edit(view=self)
            except disnake.HTTPException:
                logger.warning(
                    "Couldn't refresh download count on base post id=%s", self.base_post_id
                )

    def _current_count(self) -> int:
        label = self.downloads_button.label or ""
        head = label.split(" ", 1)[0]
        return int(head) if head.isdigit() else -1

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
        """Show the presser a private list of everyone who fetched the link."""
        rows = await base_storage.list_downloaders(inter.bot.pool, self.base_post_id)
        if not rows:
            await inter.response.send_message(
                "Nobody has fetched this link yet.", ephemeral=True
            )
            return

        shown = rows[:MAX_DOWNLOADER_LINES]
        lines = [f"{i}. <@{row['user_id']}>" for i, row in enumerate(shown, start=1)]
        if len(rows) > len(shown):
            lines.append(f"…and {len(rows) - len(shown)} more.")

        await inter.response.send_message(
            f"**{len(rows)} unique download(s)**\n" + "\n".join(lines),
            ephemeral=True,
            allowed_mentions=NO_PINGS,
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
        )

        embed = build_base_embed(
            title=cleaned["title"],
            cc=cleaned["cc"],
            description=cleaned["description"],
            image_filename=file.filename,
            author=inter.author,
        )
        view = BasePostView(record["id"], download_count=0)

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
        ids = await base_storage.list_base_post_ids(bot.pool)
    except Exception:  # noqa: BLE001 — never block startup on this
        logger.exception("Couldn't load base posts to restore views")
        return

    for base_post_id in ids:
        count = await base_storage.count_downloads(bot.pool, base_post_id)
        bot.add_view(BasePostView(base_post_id, count))
    if ids:
        logger.info("Restored %d base post view(s)", len(ids))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(BasePostCommands(bot))
