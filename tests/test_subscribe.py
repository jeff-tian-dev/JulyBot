"""Unit tests for /subscribe: product buttons and the ticket purchase flow."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import subscribe_commands
from modules.agreements.validation import status_embed
from modules.subscriptions.stripe_api import (
    StripeApiError,
    StripeNotConfiguredError,
    SubscriptionSummary,
)

SIGNED = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
LINKS = [
    ("July Sub", "https://seventhmonth.com/sub/"),
    ("CWL packs", "https://seventhmonth.com/cwl-pack/"),
]


# --- the ticket purchase flow -------------------------------------------------


def _row(**overrides):
    row = {
        "id": 7,
        "guild_id": 1,
        "channel_id": 99,
        "message_id": 100,
        "buyer_id": 4242,
        "sent_by": 555,
        "payer_name": None,
        "payment_method": None,
        "amount_cents": None,
        "signed_at": None,
        "confirmed_at": None,
        "confirmed_by": None,
        "voided_at": None,
        "void_reason": None,
        "product_id": None,
        "product_name": None,
    }
    row.update(overrides)
    return row


def _button_interaction(*, custom_id: str, author_id: int, admin: bool = False):
    inter = MagicMock()
    inter.author.id = author_id
    inter.author.guild_permissions = disnake.Permissions(administrator=admin)
    inter.data.custom_id = custom_id
    inter.guild.id = 1
    inter.bot.pool = MagicMock()
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


@pytest.mark.asyncio
async def test_pending_view_shows_one_button_per_product_immediately() -> None:
    view = subscribe_commands.PurchaseView(_row(signed_at=None), links=LINKS)

    links = [(i.label, i.url) for i in view.children if getattr(i, "url", None)]
    assert links == LINKS
    for button in view.children:
        if getattr(button, "url", None):
            # A link button has no custom_id, so nothing to dispatch or restore.
            assert button.style is disnake.ButtonStyle.link
            assert button.custom_id is None
    confirm = next(i for i in view.children if i.label == "Confirm Payment")
    assert confirm.disabled is False


@pytest.mark.asyncio
async def test_restored_view_without_links_still_has_working_buttons() -> None:
    """After a restart the links aren't known; the message already shows them."""
    view = subscribe_commands.PurchaseView(_row(signed_at=None))
    labels = [i.label for i in view.children]
    assert labels == ["Confirm Payment", "Cancel"]


@pytest.mark.asyncio
async def test_the_most_products_still_fit_on_one_message() -> None:
    from modules.subscriptions.products import MAX_PRODUCTS_PER_GUILD

    links = [(f"P{i}", f"https://example.com/{i}") for i in range(MAX_PRODUCTS_PER_GUILD)]
    view = subscribe_commands.PurchaseView(_row(), links=links)
    assert len(view.children) == MAX_PRODUCTS_PER_GUILD + 2  # + Confirm, Cancel


def test_pending_copy_discloses_that_the_purchase_does_not_renew() -> None:
    """The billing disclosure is load-bearing, not decoration, and it cuts both
    ways: an unexpected second charge produces chargebacks, and so does a buyer
    who expected access to continue and finds it lapsed. A dispute cites what
    the buyer was shown. If the billing model changes back to recurring, this
    assertion should fail and force the copy to change with it."""
    description = status_embed(_row()).description.lower()
    assert "one-time" in description
    assert "not** auto-renew" in description
    assert "billed automatically" not in description
    # What's for sale is per server now; the copy must not name a tier.
    assert "tier" not in description


@pytest.mark.asyncio
async def test_no_agree_button_remains() -> None:
    """The in-Discord agree step is gone; a leftover button would ask buyers to
    accept the same terms twice, and its callback no longer exists."""
    view = subscribe_commands.PurchaseView(_row(signed_at=None), links=LINKS)

    labels = [i.label for i in view.children]
    assert "I Agree" not in labels
    assert "Agreed" not in labels
    assert not hasattr(subscribe_commands.PurchaseView, "_on_agree")


@pytest.mark.asyncio
async def test_historical_signed_row_still_renders_a_usable_view() -> None:
    """Purchases started before the agree step was removed are still open in
    real tickets; their buttons must keep working."""
    view = subscribe_commands.PurchaseView(_row(signed_at=SIGNED), links=LINKS)

    labels = [i.label for i in view.children]
    assert "Confirm Payment" in labels
    assert "Cancel" in labels
    assert any(getattr(i, "url", None) for i in view.children)


@pytest.mark.asyncio
async def test_confirmed_view_is_terminal() -> None:
    view = subscribe_commands.PurchaseView(
        _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555), links=LINKS
    )

    assert view.children == []
    assert view.is_finished()


@pytest.mark.asyncio
async def test_non_admin_cannot_confirm_payment() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=4242, admin=False)

    with patch.object(
        subscribe_commands.payment_links, "list_guild_checkouts", new=AsyncMock()
    ) as lookup:
        view = subscribe_commands.PurchaseView(_row(signed_at=SIGNED))
        button = next(i for i in view.children if i.label == "Confirm Payment")
        await button.callback(inter)

    lookup.assert_not_awaited()  # no buyer details fetched for a non-admin
    assert "Only a moderator" in inter.response.send_message.call_args.args[0]


def _summary(name="Jane Doe", subscription_id="pi_123", payment_link_id="plink_sub"):
    return SubscriptionSummary(
        subscription_id=subscription_id,
        customer_id="",
        name=name,
        email="jane@example.com",
        status="succeeded",
        amount_cents=3500,
        currency="usd",
        created=SIGNED,
        current_period_end=None,
        kind="payment",
        payment_link_id=payment_link_id,
    )


async def _click_confirm(inter, *, subscriptions=None, side_effect=None, row=None):
    """Drive the Confirm Payment button with a mocked per-server Stripe lookup."""
    row = row or _row(signed_at=SIGNED)
    lookup = AsyncMock(side_effect=side_effect) if side_effect else AsyncMock(
        return_value=subscriptions if subscriptions is not None else [_summary()]
    )
    with patch.object(subscribe_commands.payment_links, "list_guild_checkouts", new=lookup), \
         patch.object(subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=row)):
        view = subscribe_commands.PurchaseView(row)
        button = next(i for i in view.children if i.label == "Confirm Payment")
        await button.callback(inter)
    return lookup


@pytest.mark.asyncio
async def test_confirm_opens_a_picker_of_this_servers_checkouts() -> None:
    """Confirming requires linking a real payment, so the click opens a picker
    instead of stamping the row — listing only this server's checkouts."""
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        lookup = await _click_confirm(inter)

    confirm.assert_not_awaited()
    assert lookup.await_args.args[1] == 1  # the guild the button was clicked in
    assert lookup.await_args.kwargs["product_ids"] is None  # every product
    inter.response.defer.assert_awaited_once_with(ephemeral=True)
    assert isinstance(
        inter.edit_original_response.call_args.kwargs["view"], subscribe_commands.StripePickerView
    )


@pytest.mark.asyncio
async def test_confirm_reports_stripe_not_configured() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter, side_effect=StripeNotConfiguredError())

    confirm.assert_not_awaited()
    assert "isn't configured" in inter.edit_original_response.call_args.args[0]


@pytest.mark.asyncio
async def test_confirm_reports_a_stripe_outage_without_confirming() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter, side_effect=StripeApiError("down"))

    confirm.assert_not_awaited()
    assert "Couldn't reach Stripe" in inter.edit_original_response.call_args.args[0]


@pytest.mark.asyncio
async def test_confirm_explains_an_empty_list() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter, subscriptions=[])

    confirm.assert_not_awaited()
    message = inter.edit_original_response.call_args.args[0]
    assert "this server's products" in message and "/product list" in message


@pytest.mark.asyncio
async def test_confirm_lists_only_the_products_the_purchase_was_started_for() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    lookup = await _click_confirm(inter, row=_row(offered_product_ids=[3, 4]))

    assert lookup.await_args.kwargs["product_ids"] == [3, 4]


@pytest.mark.asyncio
async def test_confirm_explains_an_empty_list_for_a_limited_purchase() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    await _click_confirm(inter, subscriptions=[], row=_row(offered_product_ids=[3]))

    message = inter.edit_original_response.call_args.args[0]
    assert "product(s) this purchase was started for" in message


def _confirm_patches(confirmed, *, product=None):
    return (
        patch.object(
            subscribe_commands.storage, "confirm_agreement", new=AsyncMock(return_value=confirmed)
        ),
        patch.object(subscribe_commands.agreement_storage, "set_payer_name", new=AsyncMock()),
        patch.object(subscribe_commands.agreement_storage, "set_product", new=AsyncMock()),
        patch.object(
            subscribe_commands.payment_links, "product_for_link", new=AsyncMock(return_value=product)
        ),
        patch.object(subscribe_commands.subscriber_storage, "create_subscriber", new=AsyncMock()),
        patch.object(
            subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=confirmed)
        ),
    )


@pytest.mark.asyncio
async def test_picking_a_payment_confirms_links_names_and_records_the_product() -> None:
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["pi_123"]
    confirmed = _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
    message = MagicMock()
    message.edit = AsyncMock()
    patches = _confirm_patches(confirmed, product={"id": 3, "name": "July Sub"})

    with patches[0] as confirm, patches[1] as set_name, patches[2] as set_product, \
         patches[3] as product_for_link, patches[4] as create, patches[5]:
        view = subscribe_commands.StripePickerView(7, [_summary()], message=message)
        await view.children[0].callback(inter)

    assert confirm.await_args.kwargs["confirmed_by"] == 555
    assert set_name.await_args.args[2] == "Jane Doe"
    # The payment's Payment Link says what was bought, in this server only.
    assert product_for_link.await_args.args[1:] == (1, "plink_sub")
    assert set_product.await_args.args[1:] == (7, 3, "July Sub")
    assert create.await_args.kwargs["product_id"] == 3
    assert create.await_args.kwargs["stripe_subscription_id"] == "pi_123"
    assert create.await_args.kwargs["linked_by"] == 555
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_payment_with_no_known_product_still_confirms() -> None:
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["pi_123"]
    confirmed = _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
    patches = _confirm_patches(confirmed, product=None)

    with patches[0], patches[1], patches[2] as set_product, patches[3], \
         patches[4] as create, patches[5]:
        view = subscribe_commands.StripePickerView(7, [_summary()], message=None)
        await view.children[0].callback(inter)

    set_product.assert_not_awaited()
    assert create.await_args.kwargs["product_id"] is None


@pytest.mark.asyncio
async def test_picking_asks_for_a_name_when_stripe_has_none() -> None:
    """Stripe's name is preferred because it's the one on the payment; the
    admin is only asked when Stripe has nothing."""
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["pi_123"]

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        view = subscribe_commands.StripePickerView(7, [_summary(name=None)], message=MagicMock())
        await view.children[0].callback(inter)

    confirm.assert_not_awaited()
    modal = inter.response.send_modal.call_args.args[0]
    assert isinstance(modal, subscribe_commands.PayerNameModal)


@pytest.mark.asyncio
async def test_picking_reports_an_ineligible_purchase() -> None:
    """The SQL guards refuse an already-confirmed/cancelled row; the handler has
    to say so rather than silently recording a subscriber."""
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["pi_123"]

    with patch.object(
             subscribe_commands.storage, "confirm_agreement", new=AsyncMock(return_value=None)
         ), \
         patch.object(
             subscribe_commands.subscriber_storage, "create_subscriber", new=AsyncMock()
         ) as create:
        view = subscribe_commands.StripePickerView(7, [_summary()], message=MagicMock())
        await view.children[0].callback(inter)

    create.assert_not_awaited()
    assert "can't be confirmed" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_already_linked_subscription_names_the_owner() -> None:
    """Re-pointing one payment at a second Discord user would be worse than
    failing, so the unique violation surfaces with who already has it."""
    import asyncpg

    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["pi_123"]
    confirmed = _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
    patches = _confirm_patches(confirmed)

    with patches[0], patches[1], patches[2], patches[3], patches[5], \
         patch.object(
             subscribe_commands.subscriber_storage,
             "create_subscriber",
             new=AsyncMock(side_effect=asyncpg.UniqueViolationError("dup")),
         ), \
         patch.object(
             subscribe_commands.subscriber_storage,
             "get_subscriber_by_stripe_id",
             new=AsyncMock(return_value={"discord_id": 9999}),
         ):
        view = subscribe_commands.StripePickerView(7, [_summary()], message=MagicMock())
        await view.children[0].callback(inter)

    message = inter.response.send_message.call_args.args[0]
    assert "already linked" in message
    assert "9999" in message


@pytest.mark.asyncio
async def test_non_admin_cannot_cancel() -> None:
    inter = _button_interaction(custom_id="purchase:cancel:7", author_id=4242, admin=False)

    view = subscribe_commands.PurchaseView(_row(signed_at=SIGNED))
    button = next(i for i in view.children if i.label == "Cancel")
    await button.callback(inter)

    inter.response.send_modal.assert_not_awaited()
    assert "Only a moderator" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_id_comes_from_the_clicked_button_not_the_instance() -> None:
    """A persistent view dispatches to whichever registered instance disnake
    picks, so its self.agreement_id is often a different purchase's. Reading it
    instead of the custom_id is exactly the bug /postbase hit."""
    inter = _button_interaction(custom_id="purchase:agree:99", author_id=4242)

    view = subscribe_commands.PurchaseView(_row(id=7))
    assert view._id_from(inter) == 99


@pytest.mark.asyncio
async def test_unparsable_custom_id_falls_back_to_the_instance() -> None:
    inter = _button_interaction(custom_id="garbage", author_id=4242)

    view = subscribe_commands.PurchaseView(_row(id=7))
    assert view._id_from(inter) == 7


def _subscribe_interaction():
    inter = MagicMock()
    inter.guild.id = 1
    inter.channel.id = 99
    inter.author.id = 555
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.channel.send = AsyncMock(return_value=MagicMock(id=100, jump_url="url"))
    return inter


PRODUCT_ROWS = [
    {"id": 3, "name": "July Sub", "payment_link": "https://seventhmonth.com/sub/"},
    {"id": 4, "name": "CWL packs", "payment_link": "https://seventhmonth.com/cwl-pack/"},
]


@pytest.mark.asyncio
async def test_subscribe_posts_this_servers_products_as_buttons() -> None:
    cog = subscribe_commands.SubscribeCommands(MagicMock())
    inter = _subscribe_interaction()
    member = MagicMock(id=4242, mention="<@4242>")

    with patch.object(
             subscribe_commands.products, "list_products", new=AsyncMock(return_value=PRODUCT_ROWS)
         ) as listing, \
         patch.object(
             subscribe_commands.storage, "create_pending_agreement",
             new=AsyncMock(return_value=_row()),
         ), \
         patch.object(subscribe_commands.storage, "attach_message", new=AsyncMock()):
        await cog.subscribe.callback(cog, inter, member, None)

    assert listing.await_args.args[1] == 1  # this server's products only
    view = inter.channel.send.await_args.kwargs["view"]
    links = [(i.label, i.url) for i in view.children if getattr(i, "url", None)]
    assert links == LINKS


@pytest.mark.asyncio
async def test_subscribe_with_product_shows_only_those_and_records_them() -> None:
    cog = subscribe_commands.SubscribeCommands(MagicMock())
    inter = _subscribe_interaction()
    member = MagicMock(id=4242, mention="<@4242>")

    with patch.object(
             subscribe_commands.products, "list_products", new=AsyncMock(return_value=PRODUCT_ROWS)
         ), \
         patch.object(
             subscribe_commands.storage, "create_pending_agreement",
             new=AsyncMock(return_value=_row()),
         ) as create, \
         patch.object(subscribe_commands.storage, "attach_message", new=AsyncMock()):
        await cog.subscribe.callback(cog, inter, member, "cwl packs")

    assert create.await_args.kwargs["offered_products"] == [PRODUCT_ROWS[1]]
    view = inter.channel.send.await_args.kwargs["view"]
    links = [(i.label, i.url) for i in view.children if getattr(i, "url", None)]
    assert links == [LINKS[1]]


@pytest.mark.asyncio
async def test_subscribe_without_product_offers_everything_unlimited() -> None:
    cog = subscribe_commands.SubscribeCommands(MagicMock())
    inter = _subscribe_interaction()

    with patch.object(
             subscribe_commands.products, "list_products", new=AsyncMock(return_value=PRODUCT_ROWS)
         ), \
         patch.object(
             subscribe_commands.storage, "create_pending_agreement",
             new=AsyncMock(return_value=_row()),
         ) as create, \
         patch.object(subscribe_commands.storage, "attach_message", new=AsyncMock()):
        await cog.subscribe.callback(cog, inter, MagicMock(id=4242, mention="<@4242>"), None)

    assert create.await_args.kwargs["offered_products"] is None


@pytest.mark.asyncio
async def test_subscribe_refuses_an_unknown_product() -> None:
    cog = subscribe_commands.SubscribeCommands(MagicMock())
    inter = _subscribe_interaction()

    with patch.object(
             subscribe_commands.products, "list_products", new=AsyncMock(return_value=PRODUCT_ROWS)
         ), \
         patch.object(
             subscribe_commands.storage, "create_pending_agreement", new=AsyncMock()
         ) as create:
        await cog.subscribe.callback(cog, inter, MagicMock(id=4242), "July Sub, Nope")

    create.assert_not_awaited()
    inter.channel.send.assert_not_awaited()
    assert "**Nope**" in inter.edit_original_response.await_args.args[0]


@pytest.mark.asyncio
async def test_subscribe_rolls_back_when_the_send_fails() -> None:
    """No orphan row should point at a message that never existed."""
    cog = subscribe_commands.SubscribeCommands(MagicMock())
    inter = _subscribe_interaction()
    inter.channel.send = AsyncMock(side_effect=RuntimeError("no perms"))
    member = MagicMock(id=4242, mention="<@4242>")

    with patch.object(
             subscribe_commands.products, "list_products", new=AsyncMock(return_value=PRODUCT_ROWS)
         ), \
         patch.object(
             subscribe_commands.storage,
             "create_pending_agreement",
             new=AsyncMock(return_value=_row()),
         ), \
         patch.object(subscribe_commands.storage, "delete_agreement", new=AsyncMock()) as delete, \
         patch.object(subscribe_commands.storage, "attach_message", new=AsyncMock()) as attach:
        await cog.subscribe.callback(cog, inter, member, None)

    delete.assert_awaited_once()
    attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscribe_refuses_when_the_server_has_no_products() -> None:
    """No purchase is started when there's nothing for the buyer to click."""
    cog = subscribe_commands.SubscribeCommands(MagicMock())
    inter = _subscribe_interaction()

    with patch.object(
             subscribe_commands.products, "list_products", new=AsyncMock(return_value=[])
         ), \
         patch.object(
             subscribe_commands.storage, "create_pending_agreement", new=AsyncMock()
         ) as create:
        await cog.subscribe.callback(cog, inter, MagicMock(id=4242), None)

    create.assert_not_awaited()
    inter.channel.send.assert_not_awaited()
    assert "/product add" in inter.edit_original_response.await_args.args[0]
