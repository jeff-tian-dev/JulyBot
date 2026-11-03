"""/help — every command the viewer can use, grouped by category.

Built from the bot's registered slash commands rather than hand-written text, so a
new command shows up here without anyone remembering to document it. Commands the
viewer lacks permission for are left out (admin commands are hidden from members),
and in a DM only the commands that work in DMs are listed.
"""
from __future__ import annotations

import disnake
from disnake.ext import commands

HELP_COLOUR = 0x5865F2
# How long the category dropdown stays usable.
HELP_TIMEOUT_SECONDS = 300
# Discord's limit on an embed description.
MAX_DESCRIPTION_LENGTH = 4096
# Value of "in a DM with the bot" in a command's contexts list.
_BOT_DM_CONTEXT = 1

# Cog class -> category shown in /help, in display order. Cogs not listed here
# fall back to their class name minus "Commands".
CATEGORIES = {
    "SetupCommands": "Setup",
    "AccountCommands": "Accounts",
    "RosterCommands": "Rosters",
    "RankedCommands": "Ranked Battles",
    "BasePostCommands": "Base posts",
    "PostCommands": "Announcements",
    "XCommands": "X feed",
    "YoutubeCommands": "YouTube feed",
    "ModerationCommands": "Moderation",
    "SubscribeCommands": "Purchases",
    "PurchaseCommands": "Purchases",
    "AgreementCommands": "Purchases",
    "ProductCommands": "Purchases",
    "HelpCommands": "Help",
}


def _category(cmd) -> str:
    name = type(cmd.cog).__name__ if cmd.cog is not None else "Other"
    return CATEGORIES.get(name, name.removesuffix("Commands") or "Other")


def _can_use(cmd, perms: disnake.Permissions | None) -> bool:
    required = cmd.default_member_permissions
    if required is None or not required.value:
        return True
    if perms is None:
        return False
    return perms.administrator or required <= perms


def _works_in_dm(cmd) -> bool:
    return _BOT_DM_CONTEXT in (cmd.body.to_dict().get("contexts") or [])


def _lines(cmd) -> list[tuple[str, str]]:
    """(usage, description) for a command and each of its subcommands."""
    if not cmd.children:
        return [(f"/{cmd.name}", cmd.description)]
    out = []
    for child in cmd.children.values():
        grandchildren = getattr(child, "children", None)
        if grandchildren:
            for leaf in grandchildren.values():
                out.append((f"/{cmd.name} {child.name} {leaf.name}", leaf.body.description))
        else:
            out.append((f"/{cmd.name} {child.name}", child.body.description))
    return out


def collect_help(
    slash_commands, perms: disnake.Permissions | None, *, in_dm: bool
) -> dict[str, list[tuple[str, str]]]:
    """{category: [(usage, description), ...]} of what this viewer can run, here."""
    order = list(dict.fromkeys(CATEGORIES.values()))
    found: dict[str, list[tuple[str, str]]] = {}
    for cmd in sorted(slash_commands, key=lambda c: c.name):
        if not _can_use(cmd, perms):
            continue
        if in_dm and not _works_in_dm(cmd):
            continue
        found.setdefault(_category(cmd), []).extend(_lines(cmd))
    ranked = sorted(found, key=lambda c: order.index(c) if c in order else len(order))
    return {category: found[category] for category in ranked}


def overview_embed(sections: dict[str, list[tuple[str, str]]], *, in_dm: bool) -> disnake.Embed:
    embed = disnake.Embed(
        title="Help",
        description=(
            "Pick a category below for details on each command."
            + ("\nMore commands are available inside a server." if in_dm else "")
        ),
        colour=HELP_COLOUR,
    )
    for category, lines in sections.items():
        names = sorted({usage.split(" ")[0] for usage, _ in lines})
        embed.add_field(name=category, value=" ".join(f"`{n}`" for n in names)[:1024], inline=False)
    return embed


def category_embed(category: str, lines: list[tuple[str, str]]) -> disnake.Embed:
    text = "\n".join(f"`{usage}` — {description}" for usage, description in lines)
    if len(text) > MAX_DESCRIPTION_LENGTH:
        text = text[: MAX_DESCRIPTION_LENGTH - 1] + "…"
    return disnake.Embed(title=f"Help — {category}", description=text, colour=HELP_COLOUR)


class HelpView(disnake.ui.View):
    """A category dropdown on the (ephemeral) help message.

    Short-lived and not persistent: if it expires the user just runs /help again.
    """

    def __init__(self, sections: dict[str, list[tuple[str, str]]], *, in_dm: bool) -> None:
        super().__init__(timeout=HELP_TIMEOUT_SECONDS)
        self.sections = sections
        self.in_dm = in_dm
        select = disnake.ui.StringSelect(
            placeholder="Choose a category",
            options=[disnake.SelectOption(label="Overview", value="")]
            + [disnake.SelectOption(label=category, value=category) for category in sections],
        )
        select.callback = self._on_pick
        self.add_item(select)

    async def _on_pick(self, inter: disnake.MessageInteraction) -> None:
        category = inter.data.values[0]
        if category in self.sections:
            embed = category_embed(category, self.sections[category])
        else:
            embed = overview_embed(self.sections, in_dm=self.in_dm)
        await inter.response.edit_message(embed=embed, view=self)


class HelpCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="help",
        description="See what the bot can do.",
        contexts=disnake.InteractionContextTypes(guild=True, bot_dm=True),
    )
    async def help(self, inter: disnake.ApplicationCommandInteraction) -> None:
        in_dm = inter.guild is None
        perms = None if in_dm else inter.author.guild_permissions
        sections = collect_help(self.bot.slash_commands, perms, in_dm=in_dm)
        await inter.response.send_message(
            embed=overview_embed(sections, in_dm=in_dm),
            view=HelpView(sections, in_dm=in_dm),
            ephemeral=True,
        )


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(HelpCommands(bot))
