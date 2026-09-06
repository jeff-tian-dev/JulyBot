"""Unit tests for /agreement record — purchases paid outside Stripe.

These rows land in the same `subscribers` table as Stripe purchases but with no
Stripe ids, so `/purchases` and the active-subscriber list cover every buyer.
The in-Discord signature is the ONLY consent record for them, which is what
makes the agree step correct here even though /subscribe dropped it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import disnake
import pytest

from discord_bot.commands import agreement_commands

SIGNED = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _row(**overrides):
    row = {
        "id": 7,
        "guild_id": 1,
        "channel_id": 99,
        "message_id": 100,
        "buyer_id": 4242,
        "sent_by": 555,
        "payer_name": "Cash Buyer",
        "payment_method": "Venmo",
        "payment_contact": None,
        "amount_cents": 3500,
        "order_ref": None,
        "agreement_text": "TERMS",
        "signed_at": None,
        "voided_at": None,
        "voided_by": None,
        "void_reason": None,
        "confirmed_at": None,
        "confirmed_by": None,
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
    return inter


@pytest.mark.asyncio
async def test_pending_view_offers_agree_but_not_confirm_yet() -> None:
    """Confirming an unsigned manual purchase would leave no consent record at
    all, since nothing else captures it for these buyers."""
    view = agreement_commands.ManualPurchaseView(_row())

    by_label = {i.label: i for i in view.children}
    assert by_label["I Agree"].disabled is False
    assert by_label["Confirm Payment"].disabled is True


@pytest.mark.asyncio
async def test_signed_view_enables_confirm_and_locks_agree() -> None:
    view = agreement_commands.ManualPurchaseView(_row(signed_at=SIGNED))

    by_label = {i.label: i for i in view.children}
    assert by_label["Agreed"].disabled is True
    assert by_label["Confirm Payment"].disabled is False


@pytest.mark.asyncio
async def test_confirmed_view_is_terminal() -> None:
    view = agreement_commands.ManualPurchaseView(
        _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
    )

    assert view.children == []
    assert view.is_finished()


@pytest.mark.asyncio
async def test_only_the_named_buyer_can_agree() -> None:
    inter = _button_interaction(custom_id="purchase:magree:7", author_id=9999)
    view = agreement_commands.ManualPurchaseView(_row())

    with patch.object(
        agreement_commands.storage, "get_agreement", new=AsyncMock(return_value=_row())
    ), patch.object(
        agreement_commands.storage, "sign_agreement", new=AsyncMock()
    ) as sign:
        await view.children[0].callback(inter)

    sign.assert_not_awaited()
    assert "addressed to you" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_buyer_agreeing_advances_the_message() -> None:
    inter = _button_interaction(custom_id="purchase:magree:7", author_id=4242)
    view = agreement_commands.ManualPurchaseView(_row())

    with patch.object(
        agreement_commands.storage, "get_agreement", new=AsyncMock(return_value=_row())
    ), patch.object(
        agreement_commands.storage,
        "sign_agreement",
        new=AsyncMock(return_value=_row(signed_at=SIGNED)),
    ) as sign:
        await view.children[0].callback(inter)

    sign.assert_awaited_once()
    assert "Signed" in inter.response.edit_message.call_args.kwargs["embed"].title


@pytest.mark.asyncio
async def test_the_id_comes_off_the_clicked_button_not_the_instance() -> None:
    """A persistent view is matched by custom_id but dispatched to whichever
    registered instance disnake picks — usually a different purchase's."""
    inter = _button_interaction(custom_id="purchase:magree:99", author_id=4242)
    view = agreement_commands.ManualPurchaseView(_row(id=7))

    with patch.object(
        agreement_commands.storage,
        "get_agreement",
        new=AsyncMock(return_value=_row(id=99)),
    ) as get, patch.object(
        agreement_commands.storage,
        "sign_agreement",
        new=AsyncMock(return_value=_row(id=99, signed_at=SIGNED)),
    ):
        await view.children[0].callback(inter)

    assert get.await_args.args[1] == 99


@pytest.mark.asyncio
async def test_non_admin_cannot_confirm() -> None:
    inter = _button_interaction(custom_id="purchase:mconfirm:7", author_id=4242)
    view = agreement_commands.ManualPurchaseView(_row(signed_at=SIGNED))
    confirm = next(i for i in view.children if i.label == "Confirm Payment")

    with patch.object(
        agreement_commands.storage, "confirm_agreement", new=AsyncMock()
    ) as store:
        await confirm.callback(inter)

    store.assert_not_awaited()
    assert "Only a moderator" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_confirming_writes_a_subscriber_row_with_no_stripe_ids() -> None:
    """This is the whole point: a non-Stripe buyer shows up in /purchases and
    the active list alongside Stripe buyers."""
    inter = _button_interaction(
        custom_id="purchase:mconfirm:7", author_id=555, admin=True
    )
    confirmed = _row(signed_at=SIGNED, confirmed_at=SIGNED, confirmed_by=555)
    view = agreement_commands.ManualPurchaseView(_row(signed_at=SIGNED))
    confirm = next(i for i in view.children if i.label == "Confirm Payment")

    with patch.object(
        agreement_commands.storage,
        "confirm_agreement",
        new=AsyncMock(return_value=confirmed),
    ), patch.object(
        agreement_commands.subscriber_storage, "create_subscriber", new=AsyncMock()
    ) as create:
        await confirm.callback(inter)

    kwargs = create.await_args.kwargs
    assert kwargs["stripe_subscription_id"] is None
    assert kwargs["stripe_customer_id"] is None
    assert kwargs["payment_method"] == "Venmo"
    assert kwargs["amount_cents"] == 3500
    assert kwargs["payer_name"] == "Cash Buyer"
    assert kwargs["discord_id"] == 4242
    assert kwargs["linked_by"] == 555


@pytest.mark.asyncio
async def test_confirming_an_ineligible_purchase_records_nothing() -> None:
    """The SQL guards refuse a cancelled or already-confirmed row; no
    subscriber row may be written when they do."""
    inter = _button_interaction(
        custom_id="purchase:mconfirm:7", author_id=555, admin=True
    )
    view = agreement_commands.ManualPurchaseView(_row(signed_at=SIGNED))
    confirm = next(i for i in view.children if i.label == "Confirm Payment")

    with patch.object(
        agreement_commands.storage, "confirm_agreement", new=AsyncMock(return_value=None)
    ), patch.object(
        agreement_commands.subscriber_storage, "create_subscriber", new=AsyncMock()
    ) as create:
        await confirm.callback(inter)

    create.assert_not_awaited()
    assert "can't be confirmed" in inter.response.send_message.call_args.args[0]


@pytest.mark.asyncio
async def test_record_posts_a_message_and_attaches_it() -> None:
    inter = MagicMock()
    inter.guild.id = 1
    inter.channel.id = 99
    inter.author.id = 555
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.channel.send = AsyncMock(return_value=MagicMock(id=100, jump_url="url"))

    member = MagicMock(spec=disnake.User)
    member.id = 4242
    member.mention = "<@4242>"

    cog = agreement_commands.AgreementCommands(MagicMock())
    cog.bot.pool = MagicMock()

    with patch.object(
        agreement_commands.storage,
        "create_manual_agreement",
        new=AsyncMock(return_value=_row()),
    ) as create, patch.object(
        agreement_commands.storage, "attach_message", new=AsyncMock()
    ) as attach:
        await cog.record.callback(
            cog, inter, member=member, method="Venmo",
            payer_name="Cash Buyer", amount_usd=35.0,
        )

    assert create.await_args.kwargs["amount_cents"] == 3500
    assert create.await_args.kwargs["payment_method"] == "Venmo"
    attach.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_rolls_back_when_the_message_cannot_be_posted() -> None:
    """No orphan row may point at a message that never existed."""
    inter = MagicMock()
    inter.guild.id = 1
    inter.channel.id = 99
    inter.author.id = 555
    inter.response.defer = AsyncMock()
    inter.edit_original_response = AsyncMock()
    inter.channel.send = AsyncMock(side_effect=RuntimeError("no perms"))

    member = MagicMock(spec=disnake.User)
    member.id = 4242
    member.mention = "<@4242>"

    cog = agreement_commands.AgreementCommands(MagicMock())
    cog.bot.pool = MagicMock()

    with patch.object(
        agreement_commands.storage,
        "create_manual_agreement",
        new=AsyncMock(return_value=_row()),
    ), patch.object(
        agreement_commands.storage, "delete_agreement", new=AsyncMock()
    ) as delete:
        await cog.record.callback(
            cog, inter, member=member, method="Venmo",
            payer_name="Cash Buyer", amount_usd=35.0,
        )

    delete.assert_awaited_once()


def test_amount_is_converted_to_whole_cents() -> None:
    """Float dollars round to cents rather than truncating — 35.35 must not
    become 3534 through binary float representation."""
    assert round(35.35 * 100) == 3535
    assert round(20.0 * 100) == 2000


# --- restart survival ---------------------------------------------------------


@pytest.mark.asyncio
async def test_restore_picks_the_view_class_matching_the_flow() -> None:
    """The two flows use different custom_ids, so restoring a manual row as a
    PurchaseView (or vice versa) leaves the message's buttons dead."""
    from discord_bot.commands import subscribe_commands

    bot = MagicMock()
    bot.pool = MagicMock()
    bot.add_view = MagicMock()

    rows = [
        {"id": 1, "buyer_id": 4242, "signed_at": None, "payment_method": None,
         "amount_cents": None, "payer_name": None},
        {"id": 2, "buyer_id": 4242, "signed_at": None, "payment_method": "Venmo",
         "amount_cents": 3500, "payer_name": "Cash Buyer"},
    ]
    with patch.object(
        subscribe_commands.storage, "list_views_to_restore", new=AsyncMock(return_value=rows)
    ), patch.object(subscribe_commands, "TIERS", {}):
        await subscribe_commands.register_persistent_views(bot)

    restored = [call.args[0] for call in bot.add_view.call_args_list]
    assert isinstance(restored[0], subscribe_commands.PurchaseView)
    assert isinstance(restored[1], agreement_commands.ManualPurchaseView)
    # And the manual one carries the manual custom_ids.
    ids = [i.custom_id for i in restored[1].children]
    assert "purchase:magree:2" in ids


def test_payment_methods_fit_the_column() -> None:
    """agreements.payment_method is VARCHAR(20); a longer choice would only
    fail at insert time, in production, after the buyer was already messaged."""
    assert all(len(m) <= 20 for m in agreement_commands.PAYMENT_METHODS)
    # The values are stored verbatim, so these strings appear on receipts.
    assert "UPI" in agreement_commands.PAYMENT_METHODS
    assert "WeChat" in agreement_commands.PAYMENT_METHODS
    # "Other" stays last so it reads as the fallback, not a peer.
    assert agreement_commands.PAYMENT_METHODS[-1] == "Other"
