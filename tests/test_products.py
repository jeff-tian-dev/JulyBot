"""Unit tests for one-time products: storage, and their path through the purchase flow."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import disnake
import pytest

from discord_bot.commands import product_commands, purchase_commands, subscribe_commands
from modules.agreements.validation import receipt_text, status_embed
from modules.subscriptions import products
from modules.subscriptions import storage as subscriber_storage
from modules.subscriptions.stripe_api import KIND_PAYMENT, SubscriptionSummary

NOW = datetime(2026, 9, 26, 12, 0, 0, tzinfo=timezone.utc)
LINK = "https://buy.stripe.com/basepack"


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
        "name": "Base Pack",
        "payment_link": LINK,
        "description": "Ten war bases.",
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
        "product_name": "Base Pack",
    }
    row.update(overrides)
    return row


def _summary(subscription_id: str, *, kind: str = KIND_PAYMENT) -> SubscriptionSummary:
    return SubscriptionSummary(
        subscription_id=subscription_id,
        customer_id="cus_1",
        name="Jane Doe",
        email="jane@example.com",
        status="succeeded" if kind == KIND_PAYMENT else "active",
        amount_cents=1500,
        currency="usd",
        created=NOW,
        current_period_end=None,
        kind=kind,
    )


# --- validation ---------------------------------------------------------------


def test_validate_name_collapses_whitespace_and_rejects_blank() -> None:
    assert products.validate_name("  Base   Pack ") == "Base Pack"
    with pytest.raises(products.ProductError):
        products.validate_name("   ")


@pytest.mark.parametrize(
    "link",
    ["http://buy.stripe.com/x", "javascript:alert(1)", "buy.stripe.com/x", ""],
)
def test_validate_payment_link_requires_full_https_url(link) -> None:
    with pytest.raises(products.ProductError):
        products.validate_payment_link(link)


def test_validate_payment_link_accepts_custom_domain() -> None:
    """Stripe Payment Links can run on a custom domain, so the host isn't pinned."""
    assert products.validate_payment_link(" https://pay.example.com/abc ") == "https://pay.example.com/abc"


def test_blank_description_is_stored_as_null() -> None:
    assert products.validate_description("   ") is None


# --- storage ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_product_inserts_cleaned_values() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_product())

    await products.add_product(
        _fake_pool(conn), guild_id=1, name=" Base Pack ", payment_link=LINK,
        description="", created_by=555,
    )

    sql, *args = conn.fetchrow.await_args.args
    assert "INSERT INTO products" in sql
    assert args == [1, "Base Pack", LINK, None, 555]


@pytest.mark.asyncio
async def test_add_product_duplicate_name_is_a_friendly_error() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=asyncpg.UniqueViolationError("dup"))

    with pytest.raises(products.DuplicateProductError, match="already exists"):
        await products.add_product(
            _fake_pool(conn), guild_id=1, name="Base Pack", payment_link=LINK,
            description=None, created_by=555,
        )


@pytest.mark.asyncio
async def test_bad_input_never_reaches_the_database() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock()

    with pytest.raises(products.ProductError):
        await products.add_product(
            _fake_pool(conn), guild_id=1, name="Base Pack", payment_link="http://x.com",
            description=None, created_by=555,
        )
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_product_is_a_soft_delete() -> None:
    """Past purchases reference the row, so it is never DELETEd."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_product(removed_at=NOW))

    await products.remove_product(_fake_pool(conn), 1, "base pack", removed_by=555)

    sql = conn.fetchrow.await_args.args[0]
    assert "UPDATE products" in sql
    assert "removed_at = NOW()" in sql
    assert "DELETE" not in sql
    assert "lower(name) = lower($2)" in sql


@pytest.mark.asyncio
async def test_lookups_only_see_live_products() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=[])
    pool = _fake_pool(conn)

    await products.get_product_by_name(pool, 1, "Base Pack")
    await products.list_products(pool, 1)

    assert "removed_at IS NULL" in conn.fetchrow.await_args.args[0]
    assert "removed_at IS NULL" in conn.fetch.await_args.args[0]


def test_matching_names_filters_case_insensitively() -> None:
    rows = [_product(name="Base Pack"), _product(name="War Pack"), _product(name="Other")]
    assert products.matching_names(rows, "pack") == ["Base Pack", "War Pack"]


# --- products are not subscription access --------------------------------------


@pytest.mark.asyncio
async def test_products_never_count_as_active_access() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await subscriber_storage.list_active_subscribers(_fake_pool(conn), 1)

    assert "product_id IS NULL" in conn.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_products_are_never_archived() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await subscriber_storage.archive_subscribers(_fake_pool(conn), 1, before=NOW, archived_by=555)

    assert "product_id IS NULL" in conn.fetch.await_args.args[0]


@pytest.mark.asyncio
async def test_purchase_log_joins_the_product_name() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])

    await subscriber_storage.list_recent_subscribers(_fake_pool(conn), 1)

    sql = conn.fetch.await_args.args[0]
    assert "LEFT JOIN products" in sql
    assert "product_name" in sql


# --- rendering ------------------------------------------------------------------


def test_pending_product_embed_names_it_and_never_says_one_month() -> None:
    embed = status_embed(_agreement())

    assert "Base Pack" in embed.title
    assert "one-time" in embed.description
    assert "month" not in embed.description


def test_confirmed_product_embed_does_not_claim_a_subscription() -> None:
    embed = status_embed(_agreement(confirmed_at=NOW, confirmed_by=555, payer_name="Jane Doe"))

    assert "Base Pack" in embed.title
    assert "subscription" not in embed.description
    assert "Access" not in embed.description


def test_receipt_names_the_product_and_the_stripe_payment() -> None:
    text = receipt_text(
        _agreement(confirmed_at=NOW, confirmed_by=555, payer_name="Jane Doe"),
        buyer_label="buyer",
    )

    assert "Product: Base Pack (one-time purchase)" in text
    assert "successful Stripe payment" in text
    assert "Stripe subscription" not in text


def test_purchase_list_line_shows_the_product() -> None:
    record = {
        "id": 12, "discord_id": 4242, "stripe_subscription_id": "pi_1",
        "payment_method": None, "status": "succeeded", "amount_cents": 1500,
        "payer_name": "Jane Doe", "created_at": NOW, "relinked_at": None,
        "product_name": "Base Pack",
    }
    assert "Base Pack" in purchase_commands.purchase_line(record)


def test_product_list_embed_shows_each_product() -> None:
    embed = product_commands.build_product_list_embed([_product()])
    assert [f.name for f in embed.fields] == ["Base Pack"]
    assert LINK in embed.fields[0].value


def test_empty_product_list_says_how_to_add_one() -> None:
    embed = product_commands.build_product_list_embed([])
    assert "/product add" in embed.description


# --- the purchase flow ------------------------------------------------------------


@pytest.mark.asyncio
async def test_product_view_shows_only_that_products_link() -> None:
    view = subscribe_commands.PurchaseView(_agreement(), product_link=LINK)

    links = [c for c in view.children if c.style == disnake.ButtonStyle.link]
    assert [b.url for b in links] == [LINK]
    labels = [c.label for c in view.children]
    assert "Confirm Payment" in labels and "Cancel" in labels


@pytest.mark.asyncio
async def test_restored_product_view_keeps_its_callback_buttons() -> None:
    """After a restart the link is unknown; only custom_id buttons matter for dispatch."""
    view = subscribe_commands.PurchaseView(_agreement())

    assert not [c for c in view.children if c.style == disnake.ButtonStyle.link]
    assert {c.custom_id for c in view.children} == {"purchase:confirm:7", "purchase:cancel:7"}


@pytest.mark.asyncio
async def test_confirming_a_product_offers_only_one_time_payments() -> None:
    inter = MagicMock()
    inter.author.id = 555
    inter.author.guild_permissions = disnake.Permissions(administrator=True)
    inter.data.custom_id = "purchase:confirm:7"
    inter.bot.pool = MagicMock()
    inter.response.send_message = AsyncMock()
    lookup = AsyncMock(return_value=[
        _summary("sub_1", kind="subscription"),
        _summary("pi_1"),
    ])

    with patch.object(subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=_agreement())), \
         patch.object(subscribe_commands.stripe_api, "list_recent_subscriptions", new=lookup):
        view = subscribe_commands.PurchaseView(_agreement())
        button = next(c for c in view.children if c.label == "Confirm Payment")
        await button.callback(inter)

    picker = inter.response.send_message.call_args.kwargs["view"]
    assert isinstance(picker, subscribe_commands.StripePickerView)
    assert list(picker._by_id) == ["pi_1"]


@pytest.mark.asyncio
async def test_confirming_a_product_records_it_in_the_purchase_log() -> None:
    inter = MagicMock()
    inter.author.id = 555
    inter.bot.pool = MagicMock()
    inter.response.send_message = AsyncMock()
    confirmed = _agreement(confirmed_at=NOW, confirmed_by=555)
    message = MagicMock()
    message.edit = AsyncMock()

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock(return_value=confirmed)), \
         patch.object(subscribe_commands.agreement_storage, "set_payer_name", new=AsyncMock()), \
         patch.object(subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=confirmed)), \
         patch.object(subscribe_commands.subscriber_storage, "create_subscriber", new=AsyncMock()) as create:
        await subscribe_commands.finalize_confirmation(
            inter, agreement_id=7, subscription=_summary("pi_1"),
            payer_name="Jane Doe", message=message,
        )

    kwargs = create.await_args.kwargs
    assert kwargs["product_id"] == 3
    assert kwargs["amount_cents"] == 1500
    assert kwargs["stripe_subscription_id"] == "pi_1"


@pytest.mark.asyncio
async def test_start_purchase_snapshots_the_product_onto_the_agreement() -> None:
    inter = MagicMock()
    inter.guild.id = 1
    inter.channel.id = 99
    inter.author.id = 555
    inter.bot.pool = MagicMock()
    sent = MagicMock(id=100, jump_url="https://discord.com/x")
    inter.channel.send = AsyncMock(return_value=sent)
    inter.edit_original_response = AsyncMock()
    member = MagicMock(id=4242, mention="<@4242>")

    with patch.object(subscribe_commands.storage, "create_pending_agreement",
                      new=AsyncMock(return_value=_agreement())) as create, \
         patch.object(subscribe_commands.storage, "attach_message", new=AsyncMock()):
        await subscribe_commands.start_purchase(inter, member, product=_product())

    assert create.await_args.kwargs["product_id"] == 3
    assert create.await_args.kwargs["product_name"] == "Base Pack"
    view = inter.channel.send.await_args.kwargs["view"]
    assert [c.url for c in view.children if c.style == disnake.ButtonStyle.link] == [LINK]


@pytest.mark.asyncio
async def test_start_purchase_rolls_back_when_the_message_cant_be_posted() -> None:
    inter = MagicMock()
    inter.bot.pool = MagicMock()
    inter.channel.send = AsyncMock(side_effect=disnake.HTTPException(MagicMock(status=403), "no"))
    inter.edit_original_response = AsyncMock()

    with patch.object(subscribe_commands.storage, "create_pending_agreement",
                      new=AsyncMock(return_value=_agreement())), \
         patch.object(subscribe_commands.storage, "delete_agreement", new=AsyncMock()) as delete:
        await subscribe_commands.start_purchase(inter, MagicMock(id=4242), product=_product())

    delete.assert_awaited_once_with(inter.bot.pool, 7)
