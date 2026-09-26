"""/product — admin-managed one-time products sold alongside the L1/L2 tiers.

    /product add <name> <payment_link> [description]
    /product remove <name>
    /product list
    /product sell <member> <name>

A product is any name plus a Stripe Payment Link — admins define them freely,
nothing in the code knows about any particular product. `sell` runs
exactly the same ticket flow as /subscribe — see `start_purchase` in
subscribe_commands.py — so the sale gets the same status message, admin
confirmation against Stripe, receipt and entry in /purchases list. It never
counts as subscription access and is never archived. See
modules/subscriptions/products.py.

All subcommands are admin-only via the parent group's permissions.
"""
from __future__ import annotations

import logging

import disnake
from disnake.ext import commands

from discord_bot.commands.subscribe_commands import start_purchase
from modules.subscriptions import products

logger = logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
EMBED_COLOUR = 0x5865F2


async def _product_name_autocomplete(
    inter: disnake.ApplicationCommandInteraction, string: str
) -> list[str]:
    """Suggest live product names in this guild, filtered by what's typed."""
    rows = await products.list_products(inter.bot.pool, inter.guild.id)
    return products.matching_names(rows, string)


def build_product_list_embed(rows) -> disnake.Embed:
    """Every live product. The link is shown, since only admins can run this."""
    if not rows:
        return disnake.Embed(
            title="Products",
            description="No products yet. Add one with `/product add`.",
            colour=EMBED_COLOUR,
        )
    embed = disnake.Embed(title="Products", colour=EMBED_COLOUR)
    for row in rows:
        value = row["payment_link"]
        if row["description"]:
            value = f"{row['description']}\n{value}"
        embed.add_field(name=row["name"], value=value[:1024], inline=False)
    embed.set_footer(text="One-time purchases · /product sell <member> <name> to start one")
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

    @product.sub_command(name="add", description="Add a one-time product with its Stripe Payment Link.")
    async def add(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(max_length=products.MAX_NAME_LENGTH, description="Product name buyers will see."),
        payment_link: str = commands.Param(description="The product's Stripe Payment Link (https://…)."),
        description: str = commands.Param(
            default=None,
            max_length=products.MAX_DESCRIPTION_LENGTH,
            description="Optional short description.",
        ),
    ) -> None:
        try:
            row = await products.add_product(
                self.bot.pool,
                guild_id=inter.guild.id,
                name=name,
                payment_link=payment_link,
                description=description,
                created_by=inter.author.id,
            )
        except products.ProductError as exc:
            await inter.response.send_message(str(exc), ephemeral=True)
            return
        logger.info("Product %r (id=%s) added by %s", row["name"], row["id"], inter.author.id)
        await inter.response.send_message(
            f"Added **{row['name']}**. Sell it with `/product sell`.", ephemeral=True
        )

    @product.sub_command(name="remove", description="Remove a product. Past purchases of it are kept.")
    async def remove(
        self,
        inter: disnake.ApplicationCommandInteraction,
        name: str = commands.Param(autocomplete=_product_name_autocomplete),
    ) -> None:
        row = await products.remove_product(
            self.bot.pool, inter.guild.id, name, removed_by=inter.author.id
        )
        if row is None:
            await inter.response.send_message(f"No product named **{name}**.", ephemeral=True)
            return
        logger.info("Product %r (id=%s) removed by %s", row["name"], row["id"], inter.author.id)
        await inter.response.send_message(
            f"Removed **{row['name']}**. Its past purchases stay in `/purchases list`.",
            ephemeral=True,
        )

    @product.sub_command(name="list", description="Show every product.")
    async def list_products(self, inter: disnake.ApplicationCommandInteraction) -> None:
        rows = await products.list_products(self.bot.pool, inter.guild.id)
        await inter.response.send_message(embed=build_product_list_embed(rows), ephemeral=True)

    @product.sub_command(name="sell", description="Start a product purchase for a member in this channel.")
    async def sell(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(description="The buyer."),
        name: str = commands.Param(autocomplete=_product_name_autocomplete, description="The product."),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        product = await products.get_product_by_name(self.bot.pool, inter.guild.id, name)
        if product is None:
            await inter.edit_original_response(
                f"No product named **{name}**. See `/product list`.", allowed_mentions=NO_PINGS
            )
            return
        await start_purchase(inter, member, product=product)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(ProductCommands(bot))
