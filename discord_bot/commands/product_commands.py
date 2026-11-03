"""/product — the link buttons /subscribe shows, per server.

    /product add <label> <link>
    /product remove <label>
    /product list

A product is a button label plus a website page (e.g. "July Sub" →
https://seventhmonth.com/sub/). `/subscribe <member>` posts one link button per
product of the server it's run in, so each server only ever shows its own.

Adding a product also scans its page for Stripe Payment Links and claims them for
this server (first server to add a link owns it), which is what lets Confirm list
this server's payments — and only this server's. The reply says what was found,
including links that belong to another server. See
modules/subscriptions/payment_links.py.

All subcommands are admin-only via the parent group's permissions.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from modules.subscriptions import payment_links, products

logger = logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
EMBED_COLOUR = 0x5865F2


async def _product_name_autocomplete(
    inter: disnake.ApplicationCommandInteraction, string: str
) -> list[str]:
    """Suggest live product labels in this guild, filtered by what's typed."""
    rows = await products.list_products(inter.bot.pool, inter.guild.id)
    return products.matching_names(rows, string)


def describe_scan(result: payment_links.ScanResult) -> str:
    """What scanning a product page means for Confirm, in one or two lines."""
    if result.error:
        return f"⚠️ {result.error} Confirm won't list its payments until that's fixed."
    if not result.found:
        return (
            "⚠️ No Stripe checkout links found on that page, so Confirm won't list "
            "its payments. Buyers can still use the button."
        )
    lines = []
    if result.claimed:
        n = len(result.claimed)
        lines.append(f"Found {n} Stripe checkout{'s' if n != 1 else ''} — Confirm will list their payments.")
    if result.owned_elsewhere:
        n = len(result.owned_elsewhere)
        lines.append(
            f"⚠️ {n} Stripe checkout{'s' if n != 1 else ''} on that page belong"
            f"{'s' if n == 1 else ''} to another server, so their payments won't be listed here."
        )
    if result.not_on_our_account and not lines:
        lines.append(
            "⚠️ The Stripe links on that page aren't on this bot's Stripe account, so "
            "Confirm can't list their payments."
        )
    return "\n".join(lines)


def build_product_list_embed(rows, link_counts: dict[int, int]) -> disnake.Embed:
    """Every live product: its button label, link, and owned Stripe checkouts."""
    if not rows:
        return disnake.Embed(
            title="Products",
            description="No products yet. Add the buttons /subscribe shows with `/product add`.",
            colour=EMBED_COLOUR,
        )
    embed = disnake.Embed(title="Products", colour=EMBED_COLOUR)
    for row in rows:
        count = link_counts.get(row["id"], 0)
        status = (
            f"{count} Stripe checkout{'s' if count != 1 else ''}"
            if count
            else "⚠️ no Stripe checkouts — Confirm won't list its payments"
        )
        embed.add_field(name=row["name"], value=f"{row['payment_link']}\n{status}"[:1024], inline=False)
    embed.set_footer(text="Each product is a button under /subscribe in this server.")
    return embed


class ProductCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="product",
        default_member_permissions=ADMIN_PERMS,
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def product(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Parent group; disnake never invokes this directly."""

    @product.sub_command(name="add", description="Add a button to /subscribe: a label and the page it opens.")
    async def add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        label: str = commands.Param(
            max_length=products.MAX_NAME_LENGTH, description="Button label buyers see, e.g. July Sub."
        ),
        link: str = commands.Param(description="The page the button opens, e.g. https://seventhmonth.com/sub/"),
    ) -> None:
        # Reading the page and asking Stripe can outrun the 3s deadline.
        await inter.response.defer(ephemeral=True)
        try:
            row = await products.add_product(
                self.bot.pool,
                guild_id=inter.guild.id,
                name=label,
                payment_link=link,
                description=None,
                created_by=inter.author.id,
            )
        except products.ProductError as exc:
            await inter.edit_original_response(str(exc))
            return
        logger.info("Product %r (id=%s) added by %s", row["name"], row["id"], inter.author.id)
        result = await payment_links.scan_product(self.bot.pool, inter.guild.id, row)
        await inter.edit_original_response(
            f"Added **{row['name']}** — it's now a button under `/subscribe`.\n{describe_scan(result)}"
        )

    @product.sub_command(name="remove", description="Remove a /subscribe button. Past purchases are kept.")
    async def remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        label: str = commands.Param(autocomplete=_product_name_autocomplete, description="The button label."),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        row = await products.remove_product(
            self.bot.pool, inter.guild.id, label, removed_by=inter.author.id
        )
        if row is None:
            await inter.edit_original_response(f"No product named **{label}**.")
            return
        await payment_links.release_product_links(self.bot.pool, row["id"])
        # Another product of this server may share the page; re-claim its links.
        await payment_links.refresh_guild_links(self.bot.pool, inter.guild.id)
        logger.info("Product %r (id=%s) removed by %s", row["name"], row["id"], inter.author.id)
        await inter.edit_original_response(
            f"Removed **{row['name']}**. Its past purchases stay in `/purchases list`."
        )

    @product.sub_command(name="list", description="Show this server's /subscribe buttons.")
    async def list_products(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.defer(ephemeral=True)
        # Re-scan first, so a page that gained new Stripe links shows them.
        await payment_links.refresh_guild_links(self.bot.pool, inter.guild.id)
        rows = await products.list_products(self.bot.pool, inter.guild.id)
        owned = await payment_links.owned_links(self.bot.pool, inter.guild.id)
        counts: dict[int, int] = {}
        for product_id in owned.values():
            if product_id is not None:
                counts[product_id] = counts.get(product_id, 0) + 1
        await inter.edit_original_response(embed=build_product_list_embed(rows, counts))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(ProductCommands(bot))
