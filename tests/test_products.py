"""Unit tests for products (the /subscribe buttons): storage, rendering and /product."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import disnake
import pytest

from discord_bot.commands import product_commands, purchase_commands
from modules.agreements.validation import receipt_text, status_embed
from modules.subscriptions import payment_links, products
from modules.subscriptions import storage as subscriber_storage

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
LINK = "https://seventhmonth.com/sub/"


class _FakePoolAcquireCtx:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolAcquireCtx(conn))
    return pool


def _product(**overrides):
    row = {
        "id": 3,
        "guild_id": 1,
        "name": "July Sub",
        "payment_link": LINK,
        "description": None,
        "created_by": 555,
        "removed_at": None,
    }
    row.update(overrides)
    return row


def _agreement(**overrides):
    row = {
        "id": 7,
        "guild_id": 1,
        "channel_id": 99,
        "message_id": 100,
        "buyer_id": 4242,
        "sent_by": 555,
        "payer_name": None,
        "payment_method": None,
        "payment_contact": None,
        "order_ref": None,
        "amount_cents": None,
        "agreement_text": "",
        "signed_at": None,
        "confirmed_at": None,
        "confirmed_by": None,
        "voided_at": None,
        "voided_by": None,
        "void_reason": None,
        "product_id": 3,
        "product_name": "July Sub",
    }
    row.update(overrides)
    return row


# --- validation ---------------------------------------------------------------


def test_validate_name_collapses_whitespace_and_rejects_blank() -> None:
    assert products.validate_name("  July   Sub ") == "July Sub"
    with pytest.raises(products.ProductError):
        products.validate_name("   ")


def test_name_fits_a_discord_button_label() -> None:
    """The name is the /subscribe button's label, which Discord caps at 80."""
    products.validate_name("x" * 80)
    with pytest.raises(products.ProductError):
        products.validate_name("x" * 81)


@pytest.mark.parametrize(
    "link",
    ["http://seventhmonth.com/sub/", "javascript:alert(1)", "seventhmonth.com/sub/", ""],
)
def test_validate_link_requires_full_https_url(link) -> None:
    with pytest.raises(products.ProductError):
        products.validate_payment_link(link)


def test_validate_link_accepts_a_website_page() -> None:
    assert products.validate_payment_link(" https://seventhmonth.com/ug-sub/ ") == "https://seventhmonth.com/ug-sub/"


# --- storage ------------------------------------------------------------------


def _add_conn(*, existing: int = 0, row=None, error=None) -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=existing)
    conn.fetchrow = AsyncMock(side_effect=error) if error else AsyncMock(return_value=row or _product())
    return conn


@pytest.mark.asyncio
async def test_add_product_inserts_cleaned_values() -> None:
    conn = _add_conn()

    await products.add_product(
        _fake_pool(conn), guild_id=1, name=" July Sub ", payment_link=LINK,
        description="", created_by=555,
    )

    sql, *args = conn.fetchrow.await_args.args
    assert "INSERT INTO products" in sql
    assert args == [1, "July Sub", LINK, None, 555]
    # The cap is counted per server.
    assert conn.fetchval.await_args.args[1] == 1


@pytest.mark.asyncio
async def test_add_product_duplicate_name_is_a_friendly_error() -> None:
    conn = _add_conn(error=asyncpg.UniqueViolationError("dup"))

    with pytest.raises(products.DuplicateProductError, match="already exists"):
        await products.add_product(
            _fake_pool(conn), guild_id=1, name="July Sub", payment_link=LINK,
            description=None, created_by=555,
        )


@pytest.mark.asyncio
async def test_add_product_refuses_past_the_per_server_cap() -> None:
    """Every product is a button on one message, and Discord caps buttons."""
    conn = _add_conn(existing=products.MAX_PRODUCTS_PER_GUILD)

    with pytest.raises(products.ProductError, match="Remove one first"):
        await products.add_product(
            _fake_pool(conn), guild_id=1, name="One more", payment_link=LINK,
            description=None, created_by=555,
        )
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_bad_input_never_reaches_the_database() -> None:
    conn = _add_conn()

    with pytest.raises(products.ProductError):
        await products.add_product(
            _fake_pool(conn), guild_id=1, name="July Sub", payment_link="http://x.com",
            description=None, created_by=555,
        )
    conn.fetchval.assert_not_awaited()
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_product_is_a_soft_delete() -> None:
    """Past purchases reference the row, so it is never DELETEd."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_product(removed_at=NOW))

    await products.remove_product(_fake_pool(conn), 1, "july sub", removed_by=555)

    sql = conn.fetchrow.await_args.args[0]
    assert "UPDATE products" in sql
    assert "removed_at = NOW()" in sql
    assert "DELETE" not in sql
    assert "lower(name) = lower($2)" in sql


@pytest.mark.asyncio
async def test_lookups_only_see_live_products_of_one_server() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    pool = _fake_pool(conn)

    await products.get_product_by_name(pool, 1, "July Sub")
    await products.list_products(pool, 1)

    for sql in (conn.fetchrow.await_args.args[0], conn.fetch.await_args.args[0]):
        assert "removed_at IS NULL" in sql
        assert "guild_id = $1" in sql


def test_matching_names_filters_case_insensitively() -> None:
    rows = [_product(name="July Sub"), _product(name="UG Sub"), _product(name="CWL packs")]
    assert products.matching_names(rows, "sub") == ["July Sub", "UG Sub"]


# --- every purchase counts ---------------------------------------------------------


@pytest.mark.asyncio
async def test_every_purchase_counts_as_active_and_is_archived() -> None:
    """All purchases are products now, so none may be filtered out by product."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    pool = _fake_pool(conn)

    await subscriber_storage.list_active_subscribers(pool, 1)
    assert "product_id" not in conn.fetch.await_args.args[0]

    await subscriber_storage.archive_subscribers(pool, 1, before=NOW, archived_by=555)
    assert "product_id" not in conn.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_purchase_log_joins_the_product_name() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await subscriber_storage.list_recent_subscribers(_fake_pool(conn), 1)

    sql = conn.fetch.await_args.args[0]
    assert "LEFT JOIN products" in sql
    assert "product_name" in sql


# --- rendering ------------------------------------------------------------------


def test_pending_historical_product_embed_names_it_and_never_says_one_month() -> None:
    """A row from the retired /product sell still shows its product."""
    embed = status_embed(_agreement(product_name="Base Pack"))

    assert "Base Pack" in embed.title
    assert "one-time" in embed.description
    assert "month" not in embed.description


def test_confirmed_product_embed_does_not_claim_a_subscription() -> None:
    embed = status_embed(_agreement(confirmed_at=NOW, confirmed_by=555, payer_name="Jane Doe"))

    assert "July Sub" in embed.title
    assert "subscription" not in embed.description


def test_receipt_names_the_product_and_the_stripe_payment() -> None:
    text = receipt_text(
        _agreement(confirmed_at=NOW, confirmed_by=555, payer_name="Jane Doe"),
        buyer_label="buyer",
    )

    assert "July Sub" in text
    assert "successful Stripe payment" in text
    assert "Stripe subscription" not in text


def test_purchase_list_line_shows_the_product() -> None:
    record = {
        "id": 12, "discord_id": 4242, "stripe_subscription_id": "pi_1",
        "payment_method": None, "status": "succeeded", "amount_cents": 1500,
        "payer_name": "Jane Doe", "created_at": NOW, "relinked_at": None,
        "product_name": "July Sub",
    }
    assert "July Sub" in purchase_commands.purchase_line(record)


def test_product_list_embed_shows_each_product_and_its_checkouts() -> None:
    rows = [_product(), _product(id=4, name="CWL packs", payment_link="https://seventhmonth.com/cwl-pack/")]
    embed = product_commands.build_product_list_embed(rows, {3: 2})

    assert [f.name for f in embed.fields] == ["July Sub", "CWL packs"]
    assert LINK in embed.fields[0].value and "2 Stripe checkouts" in embed.fields[0].value
    assert "no Stripe checkouts" in embed.fields[1].value


def test_empty_product_list_says_how_to_add_one() -> None:
    embed = product_commands.build_product_list_embed([], {})
    assert "/product add" in embed.description


# --- describing a page scan ----------------------------------------------------------


def test_scan_description_reports_claimed_links() -> None:
    text = product_commands.describe_scan(
        payment_links.ScanResult(found=2, claimed=["a", "b"])
    )
    assert "Found 2 Stripe checkouts" in text


def test_scan_description_warns_about_links_owned_by_another_server() -> None:
    text = product_commands.describe_scan(
        payment_links.ScanResult(found=1, owned_elsewhere=["a"])
    )
    assert "another server" in text and "won't be listed" in text


def test_scan_description_warns_when_nothing_was_found() -> None:
    assert "No Stripe checkout links" in product_commands.describe_scan(payment_links.ScanResult())


def test_scan_description_reports_errors_and_foreign_accounts() -> None:
    assert "Couldn't load" in product_commands.describe_scan(
        payment_links.ScanResult(error="Couldn't load the page.")
    )
    assert "Stripe account" in product_commands.describe_scan(
        payment_links.ScanResult(found=1, not_on_our_account=1)
    )


# --- /product -----------------------------------------------------------------------


def _inter() -> MagicMock:
    inter = MagicMock()
    inter.guild.id = 1
    inter.author.id = 555
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


def _cog() -> product_commands.ProductCommands:
    cog = product_commands.ProductCommands(MagicMock())
    cog.bot.pool = MagicMock()
    return cog


@pytest.mark.asyncio
async def test_add_command_scans_the_page_for_this_server() -> None:
    inter, cog = _inter(), _cog()
    scan = AsyncMock(return_value=payment_links.ScanResult(found=2, claimed=["a", "b"]))

    with patch.object(product_commands.products, "add_product", new=AsyncMock(return_value=_product())) as add, \
         patch.object(product_commands.payment_links, "scan_product", new=scan):
        await cog.add.callback(cog, inter, label="July Sub", link=LINK)

    assert add.await_args.kwargs["guild_id"] == 1
    assert add.await_args.kwargs["name"] == "July Sub"
    assert scan.await_args.args[1] == 1
    reply = inter.edit_original_response.await_args.args[0]
    assert "July Sub" in reply and "/subscribe" in reply and "Found 2" in reply


@pytest.mark.asyncio
async def test_add_command_reports_bad_input_without_scanning() -> None:
    inter, cog = _inter(), _cog()

    with patch.object(
        product_commands.products, "add_product",
        new=AsyncMock(side_effect=products.ProductError("The link can't be empty.")),
    ), patch.object(product_commands.payment_links, "scan_product", new=AsyncMock()) as scan:
        await cog.add.callback(cog, inter, label="July Sub", link="")

    scan.assert_not_awaited()
    assert "can't be empty" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_remove_command_releases_the_products_stripe_links() -> None:
    inter, cog = _inter(), _cog()

    with patch.object(product_commands.products, "remove_product", new=AsyncMock(return_value=_product())), \
         patch.object(product_commands.payment_links, "release_product_links", new=AsyncMock()) as release, \
         patch.object(product_commands.payment_links, "refresh_guild_links", new=AsyncMock()) as refresh:
        await cog.remove.callback(cog, inter, label="July Sub")

    release.assert_awaited_once_with(cog.bot.pool, 3)
    # Another product of this server may share the page, so its links are re-claimed.
    refresh.assert_awaited_once_with(cog.bot.pool, 1)


@pytest.mark.asyncio
async def test_remove_command_with_unknown_label_changes_nothing() -> None:
    inter, cog = _inter(), _cog()

    with patch.object(product_commands.products, "remove_product", new=AsyncMock(return_value=None)), \
         patch.object(product_commands.payment_links, "release_product_links", new=AsyncMock()) as release:
        await cog.remove.callback(cog, inter, label="Nope")

    release.assert_not_awaited()
    assert "No product named" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_list_command_counts_owned_checkouts_per_product() -> None:
    inter, cog = _inter(), _cog()

    with patch.object(product_commands.payment_links, "refresh_guild_links", new=AsyncMock()), \
         patch.object(product_commands.products, "list_products", new=AsyncMock(return_value=[_product()])), \
         patch.object(
             product_commands.payment_links, "owned_links",
             new=AsyncMock(return_value={"plink_a": 3, "plink_b": 3, "plink_c": None}),
         ):
        await cog.list_products.callback(cog, inter)

    embed = inter.edit_original_response.await_args.kwargs["embed"]
    assert "2 Stripe checkouts" in embed.fields[0].value
