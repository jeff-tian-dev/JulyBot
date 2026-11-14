"""/setup and /toggle — per-server configuration, plus the welcome message on join.

/toggle sets a posting feature (roll call, watchdog, X, YouTube) to on, testing
or off; see modules/guild_config/modes.py for what each mode does.

Thin Cog layer: parse args, call modules.guild_config, format the reply. Every
value set here is scoped to the server it was run in; nothing falls back to
another server's channel or role (see the 2026-11-02 CLAUDE.md entry).
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from config.settings import settings
from modules.guild_config import modes, storage
from modules.guild_config.setup import SetupError, add_family_clan
from modules.subscriptions import products

logger = logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
SETUP_COLOUR = 0x5865F2
FEED_CHOICES = {"X": "x", "YouTube": "youtube"}
FEED_LABELS = {"x": "X", "youtube": "YouTube"}
FEATURE_CHOICES = {f.label: key for key, f in modes.FEATURES.items()}
MODE_CHOICES = {"On": modes.ON, "Testing": modes.TESTING, "Off": modes.OFF}
# The command that sets each feature's own channel (used in hints).
SET_CHANNEL_COMMANDS = {
    "rollcall": "/setup rollcall",
    "watchdog": "/setup watchdog",
    "x": "/x setchannel",
    "youtube": "/yt setchannel",
}
# What the bot needs in any channel it posts embeds to.
POSTING_PERMS = ("view_channel", "send_messages", "embed_links")


def _perm_label(name: str) -> str:
    return name.replace("_", " ").title()


def missing_post_perms(channel) -> list[str]:
    """Permissions the bot lacks to post embeds in `channel` (empty if fine)."""
    perms = channel.permissions_for(channel.guild.me)
    return [_perm_label(p) for p in POSTING_PERMS if not getattr(perms, p)]


def _channel_line(guild: disnake.Guild, channel_id: int | None, fix: str) -> str:
    if not channel_id:
        return f"⚠️ Not set — `{fix}`"
    channel = guild.get_channel(channel_id)
    if channel is None:
        return f"⚠️ Channel was deleted — `{fix}`"
    missing = missing_post_perms(channel)
    if missing:
        return f"⚠️ {channel.mention} — I'm missing: {', '.join(missing)}"
    return f"✅ {channel.mention}"


def _role_line(guild: disnake.Guild, role_id: int | None, feed: str) -> str:
    fix = f"/setup pingrole feed:{FEED_LABELS[feed]}"
    if not role_id:
        return f"No ping role (posts don't mention anyone) — `{fix}` to add one"
    role = guild.get_role(role_id)
    if role is None:
        return f"⚠️ Ping role was deleted — `{fix}`"
    if not role.mentionable and not guild.me.guild_permissions.mention_everyone:
        return f"⚠️ Pings {role.mention}, but I can't mention it — make it mentionable"
    return f"Pings {role.mention}"


def _mode_line(config: dict, feature: str) -> str:
    """One feature's /toggle mode for /setup status."""
    mode = modes.mode_of(config, feature)
    label = modes.FEATURES[feature].label
    if mode == modes.ON:
        return "Mode: 🟢 On"
    if mode == modes.OFF:
        return f"Mode: ⚪ Off — `/toggle feature:{label} mode:On` to turn it on"
    test_channel_id = config.get(modes.TEST_CHANNEL_COLUMN)
    if not test_channel_id:
        return "Mode: 🧪 Testing — ⚠️ no test channel, so nothing posts — `/setup testchannel`"
    return f"Mode: 🧪 Testing — posts go to <#{test_channel_id}> instead, without pings"


def _test_channel_line(guild: disnake.Guild, config: dict) -> str:
    if config.get(modes.TEST_CHANNEL_COLUMN):
        return _channel_line(guild, config.get(modes.TEST_CHANNEL_COLUMN), "/setup testchannel")
    testing = [
        f.label for key, f in modes.FEATURES.items() if modes.mode_of(config, key) == modes.TESTING
    ]
    if testing:
        return f"⚠️ Not set, but {', '.join(testing)} is in testing mode — `/setup testchannel`"
    return "Not set — `/setup testchannel` before using testing mode"


def toggle_reply(config: dict, feature: str, mode: str) -> str:
    """What /toggle says after changing a feature's mode, including anything missing."""
    label = modes.FEATURES[feature].label
    if mode == modes.OFF:
        return f"**{label}** is now **off** — it won't post anything."
    if mode == modes.TESTING:
        test_channel_id = config.get(modes.TEST_CHANNEL_COLUMN)
        if not test_channel_id:
            return (
                f"**{label}** is now **testing**, but there's no test channel, so nothing will "
                "post until you run `/setup testchannel`."
            )
        return (
            f"**{label}** is now **testing** — it posts in <#{test_channel_id}>, "
            "without pinging anyone."
        )
    channel_id = config.get(modes.FEATURES[feature].channel_column)
    if not channel_id:
        return (
            f"**{label}** is now **on**, but it has no channel yet — "
            f"`{SET_CHANNEL_COMMANDS[feature]}`."
        )
    return f"**{label}** is now **on** — it posts in <#{channel_id}>."


def build_status_embed(
    guild: disnake.Guild, config: dict, clans: list[dict], product_count: int = 0
) -> disnake.Embed:
    """The /setup status checklist: what's set on this server and what's missing."""
    embed = disnake.Embed(
        title=f"Setup — {guild.name}",
        description="Everything here applies to this server only.",
        colour=SETUP_COLOUR,
    )

    embed.add_field(
        name="Mod log",
        value=_channel_line(guild, config.get("mod_log_channel_id"), "/setup modlog"),
        inline=False,
    )
    embed.add_field(name="Test channel", value=_test_channel_line(guild, config), inline=False)

    if clans:
        clan_text = ", ".join(
            f"{c['clan_name'] or 'Unknown'} (`{c['clan_tag']}`)" for c in clans
        )
        clan_line = f"✅ {clan_text}"
    else:
        clan_line = "⚠️ No clans — `/setup clan add`"
    embed.add_field(
        name="Watchdog",
        value=(
            f"Clans: {clan_line}\n"
            f"Alerts: {_channel_line(guild, config.get('clan_watch_channel_id'), '/setup watchdog')}\n"
            f"{_mode_line(config, 'watchdog')}\n"
            "Pick rosters to watch with `/roster watch`."
        ),
        inline=False,
    )
    embed.add_field(
        name="Roll call",
        value=(
            f"Reports: {_channel_line(guild, config.get('rollcall_channel_id'), '/setup rollcall')}\n"
            f"{_mode_line(config, 'rollcall')}\n"
            "Daily at the legend reset, for the watchdog's clans and watched rosters."
        ),
        inline=False,
    )

    for feed, label, channel_key, role_key, fix in (
        ("x", "X posts", "twitter_channel_id", "twitter_ping_role_id", "/x setchannel"),
        ("youtube", "YouTube videos", "youtube_channel_id", "youtube_ping_role_id", "/yt setchannel"),
    ):
        if feed == "x" and not settings.X_COOKIES:
            embed.add_field(name=label, value="Not available on this bot right now.", inline=False)
            continue
        lines = [_channel_line(guild, config.get(channel_key), fix)]
        lines.append(_mode_line(config, feed))
        lines.append(_role_line(guild, config.get(role_key), feed))
        embed.add_field(name=label, value="\n".join(lines), inline=False)

    if product_count:
        purchases = (
            f"✅ {product_count} button{'s' if product_count != 1 else ''} under `/subscribe` "
            "— `/product list`"
        )
    else:
        purchases = "Not set up — `/product add` the buttons `/subscribe` should show"
    embed.add_field(name="Purchases", value=purchases, inline=False)

    embed.set_footer(text="Run /help to see every command.")
    return embed


def welcome_embed() -> disnake.Embed:
    """Posted once when the bot joins a server."""
    return disnake.Embed(
        title="Thanks for adding me!",
        description=(
            "Admins: run `/setup status` to see what to set up here — mod log, "
            "watchdog, roll call, X/YouTube post channels and `/subscribe` buttons. "
            "Everything is per server.\n\n"
            "Everyone: run `/help` to see what I can do."
        ),
        colour=SETUP_COLOUR,
    )


def welcome_channel(guild: disnake.Guild):
    """The system channel if the bot can post there, else the first text channel it can."""
    candidates = [guild.system_channel] if guild.system_channel else []
    candidates += sorted(guild.text_channels, key=lambda c: c.position)
    for channel in candidates:
        if channel is not None and not missing_post_perms(channel):
            return channel
    return None


async def _clan_autocomplete(inter: disnake.ApplicationCommandInteraction, string: str) -> list[str]:
    clans = await storage.list_clans(inter.bot.pool, inter.guild.id)
    needle = string.lower()
    return [
        c["clan_tag"]
        for c in clans
        if needle in c["clan_tag"].lower() or needle in (c["clan_name"] or "").lower()
    ][:25]


class SetupCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_guild_join(self, guild: disnake.Guild) -> None:
        logger.info("Joined guild=%s (%s)", guild.id, guild.name)
        channel = welcome_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(embed=welcome_embed())
        except disnake.HTTPException:
            logger.warning("Couldn't post welcome message in guild=%s", guild.id)

    @commands.slash_command(
        name="setup",
        description="Admin: set the bot up for this server.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def setup_group(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Parent group; disnake never invokes this directly."""

    @setup_group.sub_command(name="status", description="See what's set up on this server and what's missing.")
    async def status(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.defer(ephemeral=True)
        config = await storage.get_guild_config(self.bot.pool, inter.guild.id)
        clans = await storage.list_clans(self.bot.pool, inter.guild.id)
        rows = await products.list_products(self.bot.pool, inter.guild.id)
        await inter.edit_original_response(
            embed=build_status_embed(inter.guild, config, clans, len(rows))
        )

    async def _set_channel(
        self, inter: disnake.ApplicationCommandInteraction, column: str, channel, what: str
    ) -> None:
        if channel is not None:
            missing = missing_post_perms(channel)
            if missing:
                await inter.response.send_message(
                    f"I can't post in {channel.mention} — I'm missing: {', '.join(missing)}. "
                    "Fix the channel's permissions and try again.",
                    ephemeral=True,
                )
                return
        await storage.set_channel(self.bot.pool, inter.guild.id, column, channel.id if channel else None)
        if channel is None:
            await inter.response.send_message(f"{what} turned off for this server.", ephemeral=True)
        else:
            await inter.response.send_message(f"{what} will post in {channel.mention}.", ephemeral=True)

    @setup_group.sub_command(name="modlog", description="Set where kicks, bans and purges are logged.")
    async def modlog(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(
            default=None, description="Leave empty to turn mod logging off."
        ),
    ) -> None:
        await self._set_channel(inter, "mod_log_channel_id", channel, "The mod log")

    @setup_group.sub_command(name="watchdog", description="Set where clan leave/rejoin alerts and daily boards post.")
    async def watchdog(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(
            default=None, description="Leave empty to turn watchdog alerts off."
        ),
    ) -> None:
        await self._set_channel(inter, "clan_watch_channel_id", channel, "Watchdog")

    @setup_group.sub_command(
        name="rollcall", description="Set where the daily roll call (who missed the reset) posts."
    )
    async def rollcall(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(
            default=None, description="Leave empty to clear it."
        ),
    ) -> None:
        await self._set_channel(inter, "rollcall_channel_id", channel, "Roll call")

    @setup_group.sub_command(
        name="testchannel", description="Set where features in testing mode post (no pings)."
    )
    async def testchannel(
        self,
        inter: disnake.ApplicationCommandInteraction,
        channel: disnake.TextChannel = commands.Param(
            default=None, description="Leave empty to clear it."
        ),
    ) -> None:
        await self._set_channel(inter, "test_channel_id", channel, "Testing mode")

    @setup_group.sub_command_group(name="clan")
    async def clan(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Subcommand group; disnake never invokes this directly."""

    @clan.sub_command(name="add", description="Add a clan to this server's family for the watchdog.")
    async def clan_add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        tag: str = commands.Param(description="Clan tag, e.g. #2PP0JCCL"),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        try:
            result = await add_family_clan(self.bot.pool, inter.guild.id, tag, inter.author.id)
        except SetupError as e:
            await inter.edit_original_response(f"⚠️ {e}")
            return
        name = result["name"] or "Unknown"
        if result["added"]:
            await inter.edit_original_response(
                f"Added **{name}** (`{result['tag']}`) to this server's clan family."
            )
        else:
            await inter.edit_original_response(f"**{name}** (`{result['tag']}`) is already in the family.")

    @clan.sub_command(name="remove", description="Remove a clan from this server's family.")
    async def clan_remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        tag: str = commands.Param(autocomplete=_clan_autocomplete, description="The clan tag."),
    ) -> None:
        try:
            removed = await storage.remove_clan(self.bot.pool, inter.guild.id, tag)
        except ValueError as e:
            await inter.response.send_message(f"⚠️ {e}", ephemeral=True)
            return
        if removed:
            await inter.response.send_message("Removed it from this server's clan family.", ephemeral=True)
        else:
            await inter.response.send_message(
                "That clan isn't in this server's family. See `/setup status`.", ephemeral=True
            )

    @setup_group.sub_command(name="pingrole", description="Set the role pinged for new X posts or YouTube videos.")
    async def pingrole(
        self,
        inter: disnake.ApplicationCommandInteraction,
        feed: str = commands.Param(choices=FEED_CHOICES, description="Which feed."),
        role: disnake.Role = commands.Param(default=None, description="Leave empty to stop pinging."),
    ) -> None:
        label = FEED_LABELS[feed]
        if role is not None and role.is_default():
            await inter.response.send_message(
                "Pick a specific role — pinging @everyone on every post isn't allowed.",
                ephemeral=True,
            )
            return
        await storage.set_ping_role(self.bot.pool, inter.guild.id, feed, role.id if role else None)
        if role is None:
            await inter.response.send_message(f"New {label} posts won't ping anyone.", ephemeral=True)
            return
        message = f"New {label} posts will ping {role.mention}."
        if not role.mentionable and not inter.guild.me.guild_permissions.mention_everyone:
            message += (
                "\n⚠️ That role isn't mentionable and I don't have Mention Everyone, "
                "so the ping won't notify anyone until one of those changes."
            )
        await inter.response.send_message(message, ephemeral=True, allowed_mentions=NO_PINGS)

    @commands.slash_command(
        name="toggle",
        description="Admin: set a feature to on, testing (test channel, no pings) or off.",
        default_member_permissions=ADMIN_PERMS,
    )
    async def toggle(
        self,
        inter: disnake.ApplicationCommandInteraction,
        feature: str = commands.Param(choices=FEATURE_CHOICES, description="Which feature."),
        mode: str = commands.Param(choices=MODE_CHOICES, description="On, testing or off."),
    ) -> None:
        await modes.set_mode(self.bot.pool, inter.guild.id, feature, mode)
        config = await storage.get_guild_config(self.bot.pool, inter.guild.id)
        reply = toggle_reply(config, feature, mode)
        if feature == "x" and not settings.X_COOKIES:
            reply += "\nNote: X isn't available on this bot right now, so nothing posts either way."
        await inter.response.send_message(reply, ephemeral=True, allowed_mentions=NO_PINGS)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(SetupCommands(bot))
