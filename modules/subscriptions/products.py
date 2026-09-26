"""Admin-managed one-time products — anything sold besides the L1/L2 tiers.

A product is just a name and a Stripe Payment Link (plus an optional
description), per guild. Selling one runs through the same purchase pipeline as
a tier — an `agreements` row, the status message with Confirm/Cancel, then a
`subscribers` row once an admin links the Stripe payment — so it gets the same
receipts and purchase history. `product_id` on those rows is the only thing
that marks them as a product sale.

**Products are always one-time purchases**, never access for a month: product
rows are excluded from `list_active_subscribers` and from the monthly archive.
That is decided here, not by Stripe — the bot cannot see a Payment Link's
billing model, so a product link set to recurring in the Dashboard would still
be treated as one-time. Configure product links as one-time.

Removal is a soft delete: past purchases reference the row. See the products
table comment in database/models.py.

Raw asyncpg, pool-first, no Discord imports, so it stays testable with a mocked
pool.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

import asyncpg

MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 300
MAX_LINK_LENGTH = 500
ALLOWED_LINK_SCHEMES = ("https",)
# Autocomplete can return at most 25 choices (Discord's limit).
MAX_AUTOCOMPLETE_CHOICES = 25

_WHITESPACE = re.compile(r"\s+")


class ProductError(ValueError):
    """Bad product input the admin can fix; the message is shown to them."""


class DuplicateProductError(ProductError):
    """A live product in this guild already has that name."""


def validate_name(name: str) -> str:
    cleaned = " ".join((name or "").split())
    if not cleaned:
        raise ProductError("The product name can't be empty.")
    if len(cleaned) > MAX_NAME_LENGTH:
        raise ProductError(f"That name is too long (limit {MAX_NAME_LENGTH} characters).")
    return cleaned


def validate_payment_link(link: str) -> str:
    """Return the cleaned link, or raise ProductError.

    https only: it becomes a Discord link button a buyer pays through, so a
    plain-http or `javascript:` URL has no business there. The host is not
    pinned to buy.stripe.com, because Stripe lets a Payment Link run on a
    custom domain.
    """
    cleaned = _WHITESPACE.sub("", link or "")
    if not cleaned:
        raise ProductError("The payment link can't be empty.")
    if len(cleaned) > MAX_LINK_LENGTH:
        raise ProductError(f"That link is too long (limit {MAX_LINK_LENGTH} characters).")
    parsed = urlparse(cleaned)
    if parsed.scheme.lower() not in ALLOWED_LINK_SCHEMES or not parsed.netloc:
        raise ProductError(
            "The payment link must be a full https:// URL "
            "(e.g. a https://buy.stripe.com/... Payment Link)."
        )
    return cleaned


def validate_description(description: str | None) -> str | None:
    cleaned = (description or "").strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_DESCRIPTION_LENGTH:
        raise ProductError(
            f"That description is too long (limit {MAX_DESCRIPTION_LENGTH} characters)."
        )
    return cleaned


async def add_product(
    pool: asyncpg.Pool,
    *,
    guild_id: int,
    name: str,
    payment_link: str,
    description: str | None,
    created_by: int,
) -> asyncpg.Record:
    """Create a product. Raises ProductError on bad input or a duplicate name."""
    name = validate_name(name)
    payment_link = validate_payment_link(payment_link)
    description = validate_description(description)
    try:
        async with pool.acquire() as conn:
            return await conn.fetchrow(
                """
                INSERT INTO products (guild_id, name, payment_link, description, created_by)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *;
                """,
                guild_id,
                name,
                payment_link,
                description,
                created_by,
            )
    except asyncpg.UniqueViolationError:
        raise DuplicateProductError(f"A product named **{name}** already exists.") from None


async def remove_product(
    pool: asyncpg.Pool, guild_id: int, name: str, *, removed_by: int
) -> asyncpg.Record | None:
    """Soft-delete a live product by name (case-insensitive); None if there is none.

    Never a DELETE — past purchases reference the row. A purchase already open
    for this product is unaffected: its message keeps its link button and can
    still be confirmed.
    """
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE products
            SET removed_at = NOW(), removed_by = $3
            WHERE guild_id = $1 AND lower(name) = lower($2) AND removed_at IS NULL
            RETURNING *;
            """,
            guild_id,
            name.strip(),
            removed_by,
        )


async def get_product_by_name(
    pool: asyncpg.Pool, guild_id: int, name: str
) -> asyncpg.Record | None:
    """A live product by name (case-insensitive), or None."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT * FROM products
            WHERE guild_id = $1 AND lower(name) = lower($2) AND removed_at IS NULL;
            """,
            guild_id,
            name.strip(),
        )


async def list_products(pool: asyncpg.Pool, guild_id: int) -> list[asyncpg.Record]:
    """Every live product in a guild, alphabetically."""
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT * FROM products
            WHERE guild_id = $1 AND removed_at IS NULL
            ORDER BY lower(name);
            """,
            guild_id,
        )


def matching_names(products, typed: str) -> list[str]:
    """Product names containing what's been typed, for slash-command autocomplete."""
    needle = typed.lower()
    return [p["name"] for p in products if needle in p["name"].lower()][
        :MAX_AUTOCOMPLETE_CHOICES
    ]


__all__ = [
    "DuplicateProductError",
    "ProductError",
    "add_product",
    "get_product_by_name",
    "list_products",
    "matching_names",
    "remove_product",
    "validate_description",
    "validate_name",
    "validate_payment_link",
]
