"""/roster — admin-managed groups of Discord users and/or CoC tags.

Thin Cog layer: parse args, call modules.roster.storage, format the reply.
Members can be added by Discord user (add/remove/move) or by raw CoC tag
(addtag/removetag/movetag) for people who haven't linked their account.
"""
from __future__ import annotations

import disnake
from disnake.ext import commands

from modules.roster import storage, watcher
from modules.roster.render import (
    COLUMN_SPECS,
    ROSTER_COLOR,
    VIEW_COLUMNS,
    VIEW_HIDDEN,
    render_embed,
    view_cells,
)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()


async def _roster_name_autocomplete(
    inter: disnake.ApplicationCommandInteraction, string: str
) -> list[str]:
    """Suggest existing roster names in this guild, filtered by what's typed."""
    rosters = await storage.list_rosters(inter.bot.pool, inter.guild.id)
    needle = string.lower()
    return [r["name"] for r in rosters if needle in r["name"].lower()][:25]


class RosterView(disnake.ui.View):
    """Ephemeral view with one show/hide button per column.

    Holds the already-resolved rows so toggling re-renders instantly without
    re-hitting the DB or CoC API. A button is green when its column is shown,
    grey when hidden. Watched rosters add Status and Total Out columns.
    """

    def __init__(self, name: str, rows: list[dict], columns, hidden=()) -> None:
        super().__init__(timeout=300)
        self.name = name
        self.rows = rows
        self.columns = tuple(columns)
        self.show = {key: key not in hidden for key in self.columns}
        for key in self.columns:
            self.add_item(self._make_button(key))

    def _make_button(self, key: str) -> disnake.ui.Button:
        button = disnake.ui.Button(
            label=COLUMN_SPECS[key][0],
            custom_id=f"col_{key}",
            style=disnake.ButtonStyle.success if self.show[key] else disnake.ButtonStyle.secondary,
        )

        async def callback(inter: disnake.MessageInteraction) -> None:
            self.show[key] = not self.show[key]
            button.style = (
                disnake.ButtonStyle.success if self.show[key] else disnake.ButtonStyle.secondary
            )
            await inter.response.edit_message(embed=self.embed(), view=self)

        button.callback = callback
        return button

    def embed(self) -> disnake.Embed:
        return render_embed(self.name, self.rows, self.columns, self.show)


class RosterCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="roster",
        description="Manage rosters (groups of players).",
        default_member_permissions=ADMIN_PERMS,
    )
    async def roster(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Parent group; never invoked directly."""

    # --- roster lifecycle ---------------------------------------------------

    @roster.sub_command(name="create", description="Create a new empty roster.")
    async def create(self, inter: disnake.ApplicationCommandInteraction, name: str) -> None:
        try:
            roster = await storage.create_roster(self.bot.pool, inter.guild.id, name)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        await inter.response.send_message(f"✅ Created roster **{roster['name']}**.", ephemeral=True)

    @roster.sub_command(name="delete", description="Delete a roster and all its members.")
    async def delete(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
    ) -> None:
        deleted = await storage.delete_roster(self.bot.pool, inter.guild.id, name)
        if deleted:
            await inter.response.send_message(f"🗑️ Deleted roster **{name.strip()}**.", ephemeral=True)
        else:
            await inter.response.send_message(f"No roster named **{name.strip()}**.", ephemeral=True)

    @roster.sub_command(name="rename", description="Rename a roster.")
    async def rename(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
        new_name: str = commands.Param(description="The new name for the roster"),
    ) -> None:
        try:
            await storage.rename_roster(self.bot.pool, inter.guild.id, name, new_name)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        await inter.response.send_message(
            f"✏️ Renamed **{name.strip()}** → **{new_name.strip()}**.", ephemeral=True
        )

    @roster.sub_command(name="list", description="List all rosters in this server.")
    async def list(self, inter: disnake.ApplicationCommandInteraction) -> None:
        rosters = await storage.list_rosters(self.bot.pool, inter.guild.id)
        if not rosters:
            embed = disnake.Embed(
                title="📋 Rosters",
                description="No rosters yet. Create one with `/roster create`.",
                color=ROSTER_COLOR,
            )
            await inter.response.send_message(embed=embed, ephemeral=True)
            return
        lines = [
            f"{'👁 ' if r['watched'] else ''}**{r['name']}** — "
            f"{r['member_count']} member{'s' if r['member_count'] != 1 else ''}"
            for r in rosters
        ]
        embed = disnake.Embed(title="📋 Rosters", description="\n".join(lines), color=ROSTER_COLOR)
        embed.set_footer(text=f"{len(rosters)} roster{'s' if len(rosters) != 1 else ''}")
        await inter.response.send_message(embed=embed, ephemeral=True)

    async def _user_label(self, guild: disnake.Guild, discord_id: int | None) -> str:
        """Resolve a Discord id to a plain name for the table (no mention markup).

        Tries the guild/user caches first, then a single API fetch on a miss.
        (The members intent is enabled, but the cache can still miss for users
        who haven't been seen since startup.)
        """
        if discord_id is None:
            return "Unlinked"
        member = guild.get_member(discord_id)
        if member is not None:
            return member.display_name
        user = self.bot.get_user(discord_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(discord_id)
            except disnake.HTTPException:
                user = None
        return user.name if user is not None else f"id:{discord_id}"

    @roster.sub_command(
        name="view",
        description="Show a roster: Discord name, CoC tag, in-game name, and current clan.",
    )
    async def view(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        try:
            view = await storage.build_roster_view(self.bot.pool, inter.guild.id, name)
        except ValueError as e:
            await inter.edit_original_response(content=str(e))
            return

        marker = " 👁" if view["watched"] else ""
        if not view["members"]:
            embed = disnake.Embed(
                title=f"📋 {view['name']}{marker}",
                description="_This roster is empty._",
                color=ROSTER_COLOR,
            )
            await inter.edit_original_response(embed=embed)
            return

        # Trophy-sorted; unlinked members (no trophies) fall to the bottom.
        members = sorted(
            view["members"], key=lambda m: m["trophies"] if m["trophies"] is not None else -1, reverse=True
        )
        rows = []
        for m in members:
            discord = await self._user_label(inter.guild, m["discord_id"])
            rows.append(view_cells(m, discord))

        roster_view = RosterView(f"{view['name']}{marker}", rows, VIEW_COLUMNS, VIEW_HIDDEN)
        await inter.edit_original_response(embed=roster_view.embed(), view=roster_view)

    @roster.sub_command(
        name="watch",
        description="Watch this roster: alert on main-clan leave/rejoin and track absence.",
    )
    async def watch(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
    ) -> None:
        try:
            rname = await storage.set_watched(self.bot.pool, inter.guild.id, name, True)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        await inter.response.send_message(
            f"👁 Now watching **{rname}** for main-clan leaves and rejoins.", ephemeral=True
        )

    @roster.sub_command(name="unwatch", description="Stop watching this roster for clan changes.")
    async def unwatch(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
    ) -> None:
        try:
            rname = await storage.set_watched(self.bot.pool, inter.guild.id, name, False)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        await inter.response.send_message(f"Stopped watching **{rname}**.", ephemeral=True)

    # --- membership by Discord user -----------------------------------------

    @roster.sub_command(name="add", description="Add a Discord user to a roster.")
    async def add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
        user: disnake.User = commands.Param(description="The Discord user to add"),
    ) -> None:
        try:
            added = await storage.add_member_by_discord(self.bot.pool, inter.guild.id, name, user.id)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        verb = "Added" if added else "Already in roster:"
        await inter.response.send_message(
            f"{verb} {user.mention} — **{name.strip()}**.", ephemeral=True, allowed_mentions=NO_PINGS
        )

    @roster.sub_command(name="remove", description="Remove a Discord user from a roster.")
    async def remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
        user: disnake.User = commands.Param(description="The Discord user to remove"),
    ) -> None:
        try:
            removed = await storage.remove_member_by_discord(self.bot.pool, inter.guild.id, name, user.id)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        if removed:
            msg = f"Removed {user.mention} from **{name.strip()}**."
        else:
            msg = f"{user.mention} isn't in **{name.strip()}**."
        await inter.response.send_message(msg, ephemeral=True, allowed_mentions=NO_PINGS)

    @roster.sub_command(name="move", description="Move a Discord user from one roster to another.")
    async def move(
        self,
        inter: disnake.ApplicationCommandInteraction,
        user: disnake.User = commands.Param(description="The Discord user to move"),
        from_roster: str = commands.Param(autocomplete=_roster_name_autocomplete),
        to_roster: str = commands.Param(autocomplete=_roster_name_autocomplete),
    ) -> None:
        try:
            result = await storage.move_member_by_discord(
                self.bot.pool, inter.guild.id, from_roster, to_roster, user.id
            )
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        if result == "not_in_source":
            msg = f"{user.mention} isn't in **{from_roster.strip()}**."
        else:
            msg = f"Moved {user.mention}: **{from_roster.strip()}** → **{to_roster.strip()}**."
        await inter.response.send_message(msg, ephemeral=True, allowed_mentions=NO_PINGS)

    # --- membership by CoC tag (for unlinked people) ------------------------

    @roster.sub_command(name="addtag", description="Add a raw CoC tag to a roster.")
    async def addtag(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
        tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
    ) -> None:
        try:
            added = await storage.add_member_by_tag(self.bot.pool, inter.guild.id, name, tag)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        verb = "Added" if added else "Already in roster:"
        await inter.response.send_message(
            f"{verb} `{storage.normalize_tag(tag)}` — **{name.strip()}**.", ephemeral=True
        )

    @roster.sub_command(name="removetag", description="Remove a raw CoC tag from a roster.")
    async def removetag(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_roster_name_autocomplete),
        tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
    ) -> None:
        try:
            removed = await storage.remove_member_by_tag(self.bot.pool, inter.guild.id, name, tag)
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        norm = storage.normalize_tag(tag)
        if removed:
            msg = f"Removed `{norm}` from **{name.strip()}**."
        else:
            msg = f"`{norm}` isn't in **{name.strip()}**."
        await inter.response.send_message(msg, ephemeral=True)

    @roster.sub_command(name="movetag", description="Move a raw CoC tag from one roster to another.")
    async def movetag(
        self,
        inter: disnake.ApplicationCommandInteraction,
        tag: str = commands.Param(description="CoC player tag, e.g. #2PP0JCCL"),
        from_roster: str = commands.Param(autocomplete=_roster_name_autocomplete),
        to_roster: str = commands.Param(autocomplete=_roster_name_autocomplete),
    ) -> None:
        try:
            result = await storage.move_member_by_tag(
                self.bot.pool, inter.guild.id, from_roster, to_roster, tag
            )
        except ValueError as e:
            await inter.response.send_message(str(e), ephemeral=True)
            return
        norm = storage.normalize_tag(tag)
        if result == "not_in_source":
            msg = f"`{norm}` isn't in **{from_roster.strip()}**."
        else:
            msg = f"Moved `{norm}`: **{from_roster.strip()}** → **{to_roster.strip()}**."
        await inter.response.send_message(msg, ephemeral=True)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(RosterCommands(bot))
