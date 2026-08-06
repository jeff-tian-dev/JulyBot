"""/post — post an image to a channel, optionally pinging a role."""
from __future__ import annotations

import logging as _logging
from typing import Union

import disnake
from disnake.ext import commands

from modules.announce.poster import (
    MAX_EMBED_DESCRIPTION_LENGTH,
    PostError,
    post_image,
)

logger = _logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)


class PostCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="post",
        description="Post an image to a channel, optionally pinging a role.",
        default_member_permissions=ADMIN_PERMS,
        # Guild-only: the handler needs inter.guild for permission checks.
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def post(
        self,
        inter: disnake.ApplicationCommandInteraction,
        image: disnake.Attachment = commands.Param(description="The image to post."),
        channel: Union[disnake.TextChannel, disnake.Thread] = commands.Param(
            description="Where to post it."
        ),
        text: str = commands.Param(
            default=None,
            max_length=MAX_EMBED_DESCRIPTION_LENGTH,
            description="Optional text shown above the image (use \\n for a line break).",
        ),
        ping_role: disnake.Role = commands.Param(
            default=None, description="Optional role to ping with the post."
        ),
    ) -> None:
        # Downloading + re-uploading the attachment can outlast the 3s deadline.
        await inter.response.defer(ephemeral=True)

        try:
            result = await post_image(
                channel, image, guild=inter.guild, text=text, ping_role=ping_role
            )
        except PostError as exc:
            await self._respond(inter, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 — surface any failure to the invoker + log
            logger.exception("post failed for channel=%s image=%r", channel.id, image.filename)
            await self._respond(inter, f"Post failed: {type(exc).__name__}: {exc}")
            return

        summary = f"Posted to {channel.mention} — {result.jump_url}"
        if result.pinged_role_id:
            summary += f" (pinged <@&{result.pinged_role_id}>)"
        await self._respond(inter, summary)

    @staticmethod
    async def _respond(inter: disnake.ApplicationCommandInteraction, content: str) -> None:
        """Reply whether or not the interaction was already deferred/responded."""
        try:
            if inter.response.is_done():
                await inter.edit_original_response(
                    content=content, allowed_mentions=disnake.AllowedMentions.none()
                )
            else:
                await inter.response.send_message(
                    content=content,
                    ephemeral=True,
                    allowed_mentions=disnake.AllowedMentions.none(),
                )
        except disnake.HTTPException:
            logger.exception("Failed to send /post response")


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(PostCommands(bot))
