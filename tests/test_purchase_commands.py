"""Unit tests for /purchases: listing and relinking a mislinked payment."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import disnake
import pytest

from discord_bot.commands import purchase_commands
from modules.subscriptions.stripe_api import KIND_PAYMENT, SubscriptionSummary

CREATED = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)


def _record(**overrides):
    row = {
        "id": 3,
        "discord_id": 4242,
        "guild_id": 1,
        "agreement_id": 7,
        "stripe_subscription_id": "pi_wrong",
        "stripe_customer_id": "cus_1",
        "payer_name": "Wrong Person",
        "email": "wrong@example.com",
        "tier": None,
        "status": "succeeded",
        "current_period_end": None,
        "linked_by": 555,
        "relinked_by": None,
        "relinked_at": None,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    row.update(overrides)
    return row


def _summary(subscription_id="pi_right", name="Right Person") -> SubscriptionSummary:
    return SubscriptionSummary(
        subscription_id=subscription_id,
        customer_id="cus_9",
        name=name,
        email="right@example.com",
        status="succeeded",
        amount_cents=2000,
        currency="usd",
        created=CREATED,
        current_period_end=None,
        kind=KIND_PAYMENT,
    )


def _interaction(author_id: int = 555):
    inter = MagicMock()
    inter.author.id = author_id
    inter.guild.id = 1
    inter.bot.pool = MagicMock()
    inter.response.defer = AsyncMock()
    inter.response.send_message = AsyncMock()
    inter.response.edit_message = AsyncMock()
    inter.edit_original_response = AsyncMock()
    return inter


# --- list ---------------------------------------------------------------------


def test_list_embed_shows_buyer_and_stripe_id() -> None:
    embed = purchase_commands.build_list_embed([_record()])

    assert "**#3**" in embed.description
    assert "<@4242>" in embed.description
    assert "pi_wrong" in embed.description
    assert "Wrong Person" in embed.description


def test_list_embed_flags_an_already_relinked_row() -> None:
    """Someone scanning for a mistake should see which rows were corrected."""
    embed = purchase_commands.build_list_embed(
        [_record(relinked_by=999, relinked_at=CREATED)]
    )

    assert "relinked by <@999>" in embed.description


def test_list_embed_handles_empty() -> None:
    assert "No purchases recorded" in purchase_commands.build_list_embed([]).description


@pytest.mark.asyncio
async def test_list_for_a_member_uses_their_history() -> None:
    inter = _interaction()
    member = MagicMock(spec=disnake.User)
    member.id = 4242
    member.display_name = "buyer"

    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool
    with patch.object(
        purchase_commands.subscriber_storage,
        "list_subscribers_for_discord_id",
        new=AsyncMock(return_value=[_record()]),
    ) as by_member, patch.object(
        purchase_commands.subscriber_storage, "list_recent_subscribers", new=AsyncMock()
    ) as recent:
        await cog.list_purchases.callback(cog, inter, member=member)

    by_member.assert_awaited_once()
    recent.assert_not_awaited()


# --- relink -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_relink_rejects_an_unknown_purchase_id() -> None:
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool

    with patch.object(
        purchase_commands.subscriber_storage, "get_subscriber", new=AsyncMock(return_value=None)
    ), patch.object(
        purchase_commands.stripe_api, "list_recent_subscriptions", new=AsyncMock()
    ) as listing:
        await cog.relink.callback(cog, inter, purchase_id=999)

    # Never bothers Stripe for a row that doesn't exist.
    listing.assert_not_awaited()
    assert "No purchase #999" in inter.edit_original_response.call_args.args[0]


@pytest.mark.asyncio
async def test_relink_opens_a_picker_without_asking_for_a_member() -> None:
    """Only the payment was wrong — /subscribe already named the buyer."""
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool

    with patch.object(
        purchase_commands.subscriber_storage,
        "get_subscriber",
        new=AsyncMock(return_value=_record()),
    ), patch.object(
        purchase_commands.stripe_api,
        "list_recent_subscriptions",
        new=AsyncMock(return_value=[_summary()]),
    ), patch.object(
        purchase_commands.subscriber_storage,
        "linked_stripe_ids",
        new=AsyncMock(return_value=set()),
    ):
        await cog.relink.callback(cog, inter, purchase_id=3)

    kwargs = inter.edit_original_response.call_args.kwargs
    assert isinstance(kwargs["view"], purchase_commands.RelinkPickerView)
    assert "unchanged" in kwargs["embed"].description
    assert "<@4242>" in kwargs["embed"].description


@pytest.mark.asyncio
async def test_relink_does_not_treat_the_rows_own_payment_as_a_conflict() -> None:
    """Its current id is in the linked set by definition; flagging it would
    tell the admin their own row conflicts with itself."""
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool

    with patch.object(
        purchase_commands.subscriber_storage,
        "get_subscriber",
        new=AsyncMock(return_value=_record()),
    ), patch.object(
        purchase_commands.stripe_api,
        "list_recent_subscriptions",
        new=AsyncMock(return_value=[_summary("pi_wrong"), _summary("pi_other")]),
    ), patch.object(
        purchase_commands.subscriber_storage,
        "linked_stripe_ids",
        new=AsyncMock(return_value={"pi_wrong", "pi_other"}),
    ):
        await cog.relink.callback(cog, inter, purchase_id=3)

    view = inter.edit_original_response.call_args.kwargs["view"]
    by_value = {o.value: o.description for o in view.children[0].options}
    assert "already linked" not in by_value["pi_wrong"]
    assert "already linked" in by_value["pi_other"]


@pytest.mark.asyncio
async def test_picking_a_payment_rewrites_every_stripe_field() -> None:
    """A half-updated row would carry one payment's id with another's name."""
    inter = _interaction()
    inter.data.values = ["pi_right"]
    view = purchase_commands.RelinkPickerView(3, [_summary()], already_linked=set())

    with patch.object(
        purchase_commands.subscriber_storage,
        "relink_subscriber",
        new=AsyncMock(return_value=_record(stripe_subscription_id="pi_right")),
    ) as relink:
        await view.children[0].callback(inter)

    kwargs = relink.await_args.kwargs
    assert kwargs["stripe_subscription_id"] == "pi_right"
    assert kwargs["stripe_customer_id"] == "cus_9"
    assert kwargs["payer_name"] == "Right Person"
    assert kwargs["email"] == "right@example.com"
    assert kwargs["relinked_by"] == 555
    # The buyer is never among the updated fields.
    assert "discord_id" not in kwargs


@pytest.mark.asyncio
async def test_relinking_onto_a_taken_payment_names_the_holder() -> None:
    """The unique index protects against double-attribution; the admin needs to
    know which other row to fix rather than just being refused."""
    inter = _interaction()
    inter.data.values = ["pi_right"]
    view = purchase_commands.RelinkPickerView(3, [_summary()], already_linked=set())

    with patch.object(
        purchase_commands.subscriber_storage,
        "relink_subscriber",
        new=AsyncMock(side_effect=asyncpg.UniqueViolationError("dup")),
    ), patch.object(
        purchase_commands.subscriber_storage,
        "get_subscriber_by_stripe_id",
        new=AsyncMock(return_value=_record(id=8)),
    ):
        await view.children[0].callback(inter)

    message = inter.response.send_message.call_args.args[0]
    assert "purchase #8" in message


@pytest.mark.asyncio
async def test_relinking_a_deleted_row_reports_it() -> None:
    inter = _interaction()
    inter.data.values = ["pi_right"]
    view = purchase_commands.RelinkPickerView(3, [_summary()], already_linked=set())

    with patch.object(
        purchase_commands.subscriber_storage,
        "relink_subscriber",
        new=AsyncMock(return_value=None),
    ):
        await view.children[0].callback(inter)

    assert "no longer exists" in inter.response.send_message.call_args.args[0]


# --- paging (a month can carry ~100 purchases) --------------------------------


def test_page_count_rounds_up_and_never_returns_zero() -> None:
    assert purchase_commands.page_count(0) == 1
    assert purchase_commands.page_count(1) == 1
    assert purchase_commands.page_count(purchase_commands.PAGE_SIZE) == 1
    assert purchase_commands.page_count(purchase_commands.PAGE_SIZE + 1) == 2


def test_list_embed_shows_only_one_page() -> None:
    records = [_record(id=i) for i in range(25)]

    first = purchase_commands.build_list_embed(records, page=0)
    second = purchase_commands.build_list_embed(records, page=1)

    assert "**#0**" in first.description
    assert f"**#{purchase_commands.PAGE_SIZE}**" not in first.description
    assert f"**#{purchase_commands.PAGE_SIZE}**" in second.description
    assert "25 total" in first.footer.text


def test_list_embed_clamps_an_out_of_range_page() -> None:
    """A stale button click after the list shrank must not render blank."""
    records = [_record(id=i) for i in range(3)]

    embed = purchase_commands.build_list_embed(records, page=99)

    assert "**#0**" in embed.description
    assert "Page 1/1" in embed.footer.text


@pytest.mark.asyncio
async def test_paging_view_disables_prev_on_the_first_page() -> None:
    view = purchase_commands.PurchaseListView([_record(id=i) for i in range(25)])

    assert view.prev.disabled is True
    assert view.next.disabled is False


@pytest.mark.asyncio
async def test_paging_view_disables_next_on_the_last_page() -> None:
    inter = _interaction()
    view = purchase_commands.PurchaseListView([_record(id=i) for i in range(15)])

    await view._on_next(inter)

    assert view.page == 1
    assert view.next.disabled is True
    assert view.prev.disabled is False


@pytest.mark.asyncio
async def test_short_list_gets_no_paging_buttons() -> None:
    """Two dead buttons under a three-row list is just noise."""
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool

    with patch.object(
        purchase_commands.subscriber_storage,
        "list_recent_subscribers",
        new=AsyncMock(return_value=[_record()]),
    ):
        await cog.list_purchases.callback(cog, inter, member=None)

    assert "view" not in inter.edit_original_response.call_args.kwargs


@pytest.mark.asyncio
async def test_long_list_gets_paging_buttons() -> None:
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool

    with patch.object(
        purchase_commands.subscriber_storage,
        "list_recent_subscribers",
        new=AsyncMock(return_value=[_record(id=i) for i in range(30)]),
    ):
        await cog.list_purchases.callback(cog, inter, member=None)

    view = inter.edit_original_response.call_args.kwargs["view"]
    assert isinstance(view, purchase_commands.PurchaseListView)


# --- archive ------------------------------------------------------------------


def test_month_start_is_the_first_of_the_month_at_midnight() -> None:
    start = purchase_commands.month_start(datetime(2026, 9, 17, 13, 45, tzinfo=timezone.utc))

    assert (start.year, start.month, start.day) == (2026, 9, 1)
    assert (start.hour, start.minute, start.second) == (0, 0, 0)


@pytest.mark.asyncio
async def test_archive_reports_nothing_to_do_when_all_purchases_are_current() -> None:
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool
    this_month = datetime.now(timezone.utc).replace(tzinfo=None)

    with patch.object(
        purchase_commands.subscriber_storage,
        "list_active_subscribers",
        new=AsyncMock(return_value=[_record(created_at=this_month)]),
    ), patch.object(
        purchase_commands.subscriber_storage, "archive_subscribers", new=AsyncMock()
    ) as archive:
        await cog.archive.callback(cog, inter)

    archive.assert_not_awaited()
    assert "Nothing to archive" in inter.edit_original_response.call_args.args[0]


@pytest.mark.asyncio
async def test_archive_previews_before_touching_anything() -> None:
    """Bulk and easy to fire on the wrong month, so it confirms first."""
    inter = _interaction()
    cog = purchase_commands.PurchaseCommands(MagicMock())
    cog.bot.pool = inter.bot.pool

    with patch.object(
        purchase_commands.subscriber_storage,
        "list_active_subscribers",
        new=AsyncMock(return_value=[_record(created_at=datetime(2020, 1, 1))]),
    ), patch.object(
        purchase_commands.subscriber_storage, "archive_subscribers", new=AsyncMock()
    ) as archive:
        await cog.archive.callback(cog, inter)

    archive.assert_not_awaited()
    kwargs = inter.edit_original_response.call_args.kwargs
    assert isinstance(kwargs["view"], purchase_commands.ArchiveConfirmView)
    assert "stay in the purchase log" in kwargs["embed"].description


@pytest.mark.asyncio
async def test_confirming_the_archive_runs_it() -> None:
    inter = _interaction()
    before = datetime(2026, 9, 1, tzinfo=timezone.utc)
    view = purchase_commands.ArchiveConfirmView(1, before=before, count=2)

    with patch.object(
        purchase_commands.subscriber_storage,
        "archive_subscribers",
        new=AsyncMock(return_value=[_record(), _record(id=4)]),
    ) as archive:
        await view._on_confirm(inter)

    kwargs = archive.await_args.kwargs
    assert kwargs["before"] == before
    assert kwargs["archived_by"] == 555
    assert "Archived **2**" in inter.response.edit_message.call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_cancelling_the_archive_changes_nothing() -> None:
    inter = _interaction()
    view = purchase_commands.ArchiveConfirmView(
        1, before=datetime(2026, 9, 1, tzinfo=timezone.utc), count=2
    )

    with patch.object(
        purchase_commands.subscriber_storage, "archive_subscribers", new=AsyncMock()
    ) as archive:
        await view._on_cancel(inter)

    archive.assert_not_awaited()
    assert "Nothing archived" in inter.response.edit_message.call_args.kwargs["content"]
