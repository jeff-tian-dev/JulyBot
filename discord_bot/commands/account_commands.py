"""/link, /unlink, /accounts, /adminlink — Discord <-> CoC account linking.

Thin Cog layer: parse args, call modules.account_linker, format the reply.
A Discord user may link multiple CoC accounts (alts).
"""
from __future__ import annotations

import disnake
from disnake.ext import commands

from modules.account_linker.linker import (
    admin_link_account,
    get_all_accounts,
    get_linked_accounts,
    link_account,
    unlink_account,
)

X_BLUE = disnake.Color.from_rgb(45, 136, 255)
ADMIN_PERMS = disnake.Permissions(administrator=True)
MAX_MESSAGE_LEN = 1900  # Discord's hard limit is 2000; leave headroom.
# Personal commands: they only touch the invoker's own (global) data, so they
# also work in a DM with the bot. Everything else is server-only by default
# (bot-wide default_contexts in discord_bot/bot.py).
PERSONAL_CONTEXTS = disnake.InteractionContextTypes(guild=True, bot_dm=True)


def _accounts_embed(title: str, accounts: list[dict]) -> disnake.Embed:
    """Format a list of linked CoC accounts as an embed."""
    lines = []
    for a in accounts:
        name = a.get("coc_name") or "Unknown"
        mark = "✅" if a.get("verified") else "❔"
        lines.append(f"{mark} **{name}** — `{a['coc_tag']}`")
    embed = disnake.Embed(
        title=title,
        description="\n".join(lines),
        color=X_BLUE,
    )
    embed.set_footer(text=f"{len(accounts)} account{'s' if len(accounts) != 1 else ''}")
    return embed


class AccountCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="link",
        description="Link a CoC account to your Discord account.",
        contexts=PERSONAL_CONTEXTS,
    )
    async def link(
        self,
        inter: disnake.ApplicationCommandInteraction,
        coc_tag: str = commands.Param(description="Your player tag, e.g. #2PP0JCCL"),
        token: str = commands.Param(
            description="In-game API token (Settings -> More Settings -> API Token)"
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        try:
            result = await link_account(self.bot.pool, inter.author.id, coc_tag, token)
        except ValueError as e:
            await inter.edit_original_response(content=f"⚠️ {e}")
            return

        if not result["success"]:
            await inter.edit_original_response(content=f"❌ {result['error']}")
            return

        name = result.get("coc_name") or "your account"
        tag = result.get("coc_tag", "")
        await inter.edit_original_response(
            content=f"✅ Linked **{name}** (`{tag}`) to your Discord account."
        )

    @commands.slash_command(
        name="unlink",
        description="Unlink one of your CoC accounts.",
        contexts=PERSONAL_CONTEXTS,
    )
    async def unlink(
        self,
        inter: disnake.ApplicationCommandInteraction,
        coc_tag: str = commands.Param(description="The player tag to unlink, e.g. #2PP0JCCL"),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        try:
            deleted = await unlink_account(self.bot.pool, inter.author.id, coc_tag)
        except ValueError as e:
            await inter.edit_original_response(content=f"⚠️ {e}")
            return

        if deleted:
            await inter.edit_original_response(content="✅ Account unlinked.")
        else:
            await inter.edit_original_response(
                content="That tag isn't linked to your Discord account. Use `/accounts` to see your links."
            )

    @commands.slash_command(
        name="accounts",
        description="Show your linked CoC accounts, or another user's.",
        contexts=PERSONAL_CONTEXTS,
    )
    async def accounts(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(
            default=None, description="Whose accounts to show (default: yours)"
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        target = user or inter.author
        own = target.id == inter.author.id
        accounts = await get_linked_accounts(self.bot.pool, target.id)
        if not accounts:
            content = (
                "You haven't linked any CoC accounts yet. Use `/link` to add one."
                if own
                else f"{target.mention} has no linked CoC accounts."
            )
            await inter.edit_original_response(
                content=content, allowed_mentions=disnake.AllowedMentions.none()
            )
            return

        title = "Your linked CoC accounts" if own else f"{target.display_name}'s linked CoC accounts"
        await inter.edit_original_response(embed=_accounts_embed(title, accounts))

    @commands.slash_command(
        name="adminlink",
        description="Admin: link a CoC tag to a member without their in-game token.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def adminlink(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(description="The member to link the tag to"),
        coc_tag: str = commands.Param(description="Their player tag, e.g. #2PP0JCCL"),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        none = disnake.AllowedMentions.none()
        if user.bot:
            await inter.edit_original_response(content="⚠️ Bots can't have linked accounts.")
            return
        if inter.guild.get_member(user.id) is None:
            await inter.edit_original_response(
                content=f"⚠️ {user.mention} isn't a member of this server.", allowed_mentions=none
            )
            return

        try:
            result = await admin_link_account(self.bot.pool, user.id, coc_tag, inter.author.id)
        except ValueError as e:
            await inter.edit_original_response(content=f"⚠️ {e}")
            return

        if not result["success"]:
            content = f"❌ {result['error']}"
            if result.get("holder_id"):
                content += f" (<@{result['holder_id']}>). They can `/unlink` it first."
            await inter.edit_original_response(content=content, allowed_mentions=none)
            return

        name = result["coc_name"] or "that account"
        tag = result["coc_tag"]
        if result["already"]:
            state = "verified" if result["verified"] else "unverified"
            content = f"ℹ️ **{name}** (`{tag}`) is already linked to {user.mention} ({state})."
        else:
            content = (
                f"✅ Linked **{name}** (`{tag}`) to {user.mention} — unverified (❔). "
                "If they `/link` it themselves with their in-game token, it becomes verified."
            )
        await inter.edit_original_response(content=content, allowed_mentions=none)


    @commands.slash_command(
        name="dumpaccounts",
        description="Admin: list this server's members and their linked CoC accounts.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def dumpaccounts(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.defer(ephemeral=True)
        member_ids = [m.id for m in inter.guild.members]
        accounts = await get_all_accounts(self.bot.pool, member_ids)
        if not accounts:
            await inter.edit_original_response(content="No members of this server have linked accounts yet.")
            return

        # Rows arrive grouped by discord_id (oldest link first); render one
        # block per user, then split into messages under the Discord limit.
        blocks: list[str] = []
        current_id: int | None = None
        lines: list[str] = []
        for a in accounts:
            if a["discord_id"] != current_id:
                if lines:
                    blocks.append("\n".join(lines))
                current_id = a["discord_id"]
                lines = [f"<@{current_id}>"]
            name = a.get("coc_name") or "Unknown"
            mark = "✅" if a.get("verified") else "❔"
            lines.append(f"  {mark} **{name}** — `{a['coc_tag']}`")
        if lines:
            blocks.append("\n".join(lines))

        user_count = len({a["discord_id"] for a in accounts})
        header = f"**Linked accounts in this server** — {user_count} users, {len(accounts)} accounts\n\n"

        chunks: list[str] = []
        buf = header
        for block in blocks:
            piece = block + "\n\n"
            if len(buf) + len(piece) > MAX_MESSAGE_LEN and buf.strip():
                chunks.append(buf.rstrip())
                buf = ""
            buf += piece
        if buf.strip():
            chunks.append(buf.rstrip())

        # Render <@id> as names without notifying anyone.
        no_pings = disnake.AllowedMentions.none()
        await inter.edit_original_response(content=chunks[0], allowed_mentions=no_pings)
        for chunk in chunks[1:]:
            await inter.followup.send(content=chunk, ephemeral=True, allowed_mentions=no_pings)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(AccountCommands(bot))
