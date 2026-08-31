"""Unit tests for /subscribe: tier rendering and the ticket purchase flow."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import subscribe_commands
from modules.subscriptions.stripe_api import (
    StripeApiError,
    StripeNotConfiguredError,
    SubscriptionSummary,
)
from modules.subscriptions.tiers import TierConfig

SIGNED = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _tiers(l2_link: str = "https://buy.stripe.com/l2", l1_link: str = "https://buy.stripe.com/l1"):
    return {
        "l2_l3": TierConfig(
            key="l2_l3",
            name="L2/L3",
            price_usd=20,
            description="L2/L3 base access for one month.",
            payment_link=l2_link,
        ),
        "l1": TierConfig(
            key="l1",
            name="L1",
            price_usd=30,
            description="L1 base access for one month.",
            payment_link=l1_link,
        ),
    }


def test_tier_available_reflects_payment_link() -> None:
    tiers = _tiers(l1_link="")
    assert tiers["l2_l3"].available is True
    assert tiers["l1"].available is False


def test_embed_lists_every_tier_with_price() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        embed = subscribe_commands.build_subscribe_embed()

    names = [f.name for f in embed.fields]
    values = " ".join(f.value for f in embed.fields)
    assert names == ["L2/L3", "L1"]
    assert "$20/month" in values
    assert "$30/month" in values


def test_embed_marks_unavailable_tier() -> None:
    """A tier with no payment link is still listed, but flagged — dropping it
    silently would look like the tier no longer exists."""
    with patch.object(subscribe_commands, "TIERS", _tiers(l1_link="")):
        embed = subscribe_commands.build_subscribe_embed()

    by_name = {f.name: f.value for f in embed.fields}
    assert "unavailable" in by_name["L1"].lower()
    assert "unavailable" not in by_name["L2/L3"].lower()


@pytest.mark.asyncio
async def test_view_builds_one_link_button_per_available_tier() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.build_subscribe_view()

    assert view is not None
    assert len(view.children) == 2
    for button in view.children:
        assert button.style is disnake.ButtonStyle.link
        # A link button carries a URL and has no custom_id, so there's no
        # callback to dispatch and nothing to restore after a restart.
        assert button.url.startswith("https://buy.stripe.com/")
        assert button.custom_id is None


@pytest.mark.asyncio
async def test_view_omits_unavailable_tier() -> None:
    """Discord rejects a link button with an empty URL, so an unconfigured
    tier must be left out of the view entirely."""
    with patch.object(subscribe_commands, "TIERS", _tiers(l1_link="")):
        view = subscribe_commands.build_subscribe_view()

    assert view is not None
    assert len(view.children) == 1
    assert view.children[0].url == "https://buy.stripe.com/l2"


@pytest.mark.asyncio
async def test_view_is_none_when_nothing_configured() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers(l2_link="", l1_link="")):
        assert subscribe_commands.build_subscribe_view() is None


def test_embed_discloses_automatic_monthly_billing() -> None:
    """The auto-renewal disclosure is load-bearing, not decoration: an
    unexpected second charge is what produces chargebacks, and a dispute
    cites what the buyer was shown. If the billing model changes, this
    assertion should fail and force the copy to change with it."""
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        embed = subscribe_commands.build_subscribe_embed()

    description = embed.description.lower()
    assert "monthly subscription" in description
    assert "billed automatically" in description
    # Cancellation is manual — no Stripe customer portal is wired up.
    assert "cancel" in description


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
        "signed_at": None,
        "confirmed_at": None,
        "confirmed_by": None,
        "voided_at": None,
        "void_reason": None,
    }
    row.update(overrides)
    return row


def _button_interaction(*, custom_id: str, author_id: int, admin: bool = False):
    inter = MagicMock()
    inter.author.id = author_id
    inter.author.guild_permissions = disnake.Permissions(administrator=admin)
    inter.data.custom_id = custom_id
    inter.bot.pool = MagicMock()
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.response.send_modal = AsyncMock()
    return inter


@pytest.mark.asyncio
async def test_pending_view_hides_payment_links_until_signed() -> None:
    """The payment links must not be reachable before the buyer agrees."""
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.PurchaseView(_row())

    labels = [item.label for item in view.children]
    assert "I Agree" in labels
    assert not any(getattr(item, "url", None) for item in view.children)
    confirm = next(i for i in view.children if i.label == "Confirm Payment")
    assert confirm.disabled is True


@pytest.mark.asyncio
async def test_signed_view_reveals_payment_links_and_enables_confirm() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.PurchaseView(_row(signed_at=SIGNED))

    urls = [i.url for i in view.children if getattr(i, "url", None)]
    assert urls == ["https://buy.stripe.com/l2", "https://buy.stripe.com/l1"]
    confirm = next(i for i in view.children if i.label == "Confirm Payment")
    assert confirm.disabled is False
    agree = next(i for i in view.children if i.label == "Agreed")
    assert agree.disabled is True


@pytest.mark.asyncio
async def test_confirmed_view_is_terminal() -> None:
    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.PurchaseView(
            _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
        )

    labels = [i.label for i in view.children]
    assert "Confirm Payment" not in labels
    assert "Cancel" not in labels
    assert view.is_finished()


@pytest.mark.asyncio
async def test_only_the_named_buyer_can_agree() -> None:
    inter = _button_interaction(custom_id="purchase:agree:7", author_id=9999)

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=_row())
         ), \
         patch.object(subscribe_commands.storage, "sign_agreement", new=AsyncMock()) as sign:
        view = subscribe_commands.PurchaseView(_row())
        await view.children[0].callback(inter)

    sign.assert_not_awaited()
    assert "addressed to you" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_buyer_agreeing_advances_the_message() -> None:
    inter = _button_interaction(custom_id="purchase:agree:7", author_id=4242)
    signed = _row(signed_at=SIGNED)

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=_row())
         ), \
         patch.object(
             subscribe_commands.storage, "sign_agreement", new=AsyncMock(return_value=signed)
         ) as sign:
        view = subscribe_commands.PurchaseView(_row())
        await view.children[0].callback(inter)

    sign.assert_awaited_once()
    kwargs = inter.response.edit_message.call_args.kwargs
    assert "Signed" in kwargs["embed"].title
    assert any(getattr(i, "url", None) for i in kwargs["view"].children)


@pytest.mark.asyncio
async def test_non_admin_cannot_confirm_payment() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=4242, admin=False)

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        view = subscribe_commands.PurchaseView(_row(signed_at=SIGNED))
        button = next(i for i in view.children if i.label == "Confirm Payment")
        await button.callback(inter)

    confirm.assert_not_awaited()
    assert "Only a moderator" in inter.response.send_message.call_args.args[0]


def _summary(name="Jane Doe", subscription_id="sub_123"):
    return SubscriptionSummary(
        subscription_id=subscription_id,
        customer_id="cus_1",
        name=name,
        email="jane@example.com",
        status="active",
        amount_cents=3000,
        currency="usd",
        created=SIGNED,
        current_period_end=SIGNED,
    )


async def _click_confirm(inter, *, subscriptions=None, side_effect=None):
    """Drive the Confirm Payment button with a mocked Stripe lookup."""
    lookup = AsyncMock(side_effect=side_effect) if side_effect else AsyncMock(
        return_value=subscriptions if subscriptions is not None else [_summary()]
    )
    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(subscribe_commands.stripe_api, "list_recent_subscriptions", new=lookup):
        view = subscribe_commands.PurchaseView(_row(signed_at=SIGNED))
        button = next(i for i in view.children if i.label == "Confirm Payment")
        await button.callback(inter)


@pytest.mark.asyncio
async def test_confirm_opens_the_stripe_picker_rather_than_confirming() -> None:
    """Confirming requires linking a real subscription, so the click opens a
    picker instead of stamping the row."""
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter)

    confirm.assert_not_awaited()
    kwargs = inter.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert isinstance(kwargs["view"], subscribe_commands.StripePickerView)


@pytest.mark.asyncio
async def test_confirm_reports_stripe_not_configured() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter, side_effect=StripeNotConfiguredError())

    confirm.assert_not_awaited()
    assert "isn't configured" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_confirm_reports_a_stripe_outage_without_confirming() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter, side_effect=StripeApiError("down"))

    confirm.assert_not_awaited()
    assert "Couldn't reach Stripe" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_confirm_reports_when_stripe_has_no_subscriptions() -> None:
    inter = _button_interaction(custom_id="purchase:confirm:7", author_id=555, admin=True)

    with patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        await _click_confirm(inter, subscriptions=[])

    confirm.assert_not_awaited()
    assert "no active subscriptions" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_picking_a_subscription_confirms_links_and_names() -> None:
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["sub_123"]
    confirmed = _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
    message = MagicMock()
    message.edit = AsyncMock()

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage, "confirm_agreement", new=AsyncMock(return_value=confirmed)
         ) as confirm, \
         patch.object(
             subscribe_commands.agreement_storage, "set_payer_name", new=AsyncMock()
         ) as set_name, \
         patch.object(
             subscribe_commands.subscriber_storage, "create_subscriber", new=AsyncMock()
         ) as create, \
         patch.object(
             subscribe_commands.storage, "get_agreement", new=AsyncMock(return_value=confirmed)
         ):
        view = subscribe_commands.StripePickerView(7, [_summary()], message=message)
        await view.children[0].callback(inter)

    assert confirm.await_args.kwargs["confirmed_by"] == 555
    assert set_name.await_args.args[2] == "Jane Doe"
    assert create.await_args.kwargs["stripe_subscription_id"] == "sub_123"
    assert create.await_args.kwargs["linked_by"] == 555
    message.edit.assert_awaited_once()


@pytest.mark.asyncio
async def test_picking_asks_for_a_name_when_stripe_has_none() -> None:
    """Stripe's name is preferred because it's the one on the payment; the
    admin is only asked when Stripe has nothing."""
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["sub_123"]

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(subscribe_commands.storage, "confirm_agreement", new=AsyncMock()) as confirm:
        view = subscribe_commands.StripePickerView(7, [_summary(name=None)], message=MagicMock())
        await view.children[0].callback(inter)

    confirm.assert_not_awaited()
    modal = inter.response.send_modal.call_args.args[0]
    assert isinstance(modal, subscribe_commands.PayerNameModal)


@pytest.mark.asyncio
async def test_picking_reports_an_ineligible_purchase() -> None:
    """The SQL guards refuse an unsigned/already-confirmed/cancelled row; the
    handler has to say so rather than silently recording a subscriber."""
    inter = _button_interaction(custom_id="", author_id=555, admin=True)
    inter.data.values = ["sub_123"]

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
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
    inter.data.values = ["sub_123"]
    confirmed = _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage, "confirm_agreement", new=AsyncMock(return_value=confirmed)
         ), \
         patch.object(subscribe_commands.agreement_storage, "set_payer_name", new=AsyncMock()), \
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

    with patch.object(subscribe_commands, "TIERS", _tiers()):
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

    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.PurchaseView(_row(id=7))
        assert view._id_from(inter) == 99


@pytest.mark.asyncio
async def test_unparsable_custom_id_falls_back_to_the_instance() -> None:
    inter = _button_interaction(custom_id="garbage", author_id=4242)

    with patch.object(subscribe_commands, "TIERS", _tiers()):
        view = subscribe_commands.PurchaseView(_row(id=7))
        assert view._id_from(inter) == 7


@pytest.mark.asyncio
async def test_subscribe_rolls_back_when_the_send_fails() -> None:
    """No orphan row should point at a message that never existed."""
    bot = MagicMock()
    bot.pool = MagicMock()
    cog = subscribe_commands.SubscribeCommands(bot)

    inter = MagicMock()
    inter.guild.id = 1
    inter.channel.id = 99
    inter.author.id = 555
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.channel.send = AsyncMock(side_effect=RuntimeError("no perms"))
    member = MagicMock(id=4242, mention="<@4242>")

    with patch.object(subscribe_commands, "TIERS", _tiers()), \
         patch.object(
             subscribe_commands.storage,
             "create_pending_agreement",
             new=AsyncMock(return_value=_row()),
         ), \
         patch.object(subscribe_commands.storage, "delete_agreement", new=AsyncMock()) as delete, \
         patch.object(subscribe_commands.storage, "attach_message", new=AsyncMock()) as attach:
        await cog.subscribe.callback(cog, inter, member)

    delete.assert_awaited_once()
    attach.assert_not_awaited()


@pytest.mark.asyncio
async def test_subscribe_refuses_when_no_tiers_are_purchasable() -> None:
    """Nobody should be asked to sign for something they cannot buy."""
    bot = MagicMock()
    cog = subscribe_commands.SubscribeCommands(bot)

    inter = MagicMock()
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.channel.send = AsyncMock()
    member = MagicMock(id=4242)

    with patch.object(subscribe_commands, "TIERS", _tiers(l2_link="", l1_link="")), \
         patch.object(
             subscribe_commands.storage, "create_pending_agreement", new=AsyncMock()
         ) as create:
        await cog.subscribe.callback(cog, inter, member)

    create.assert_not_awaited()
    inter.channel.send.assert_not_awaited()


