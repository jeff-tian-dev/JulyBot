"""Unit tests for /agreement: validation/rendering, storage, and the sign view."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from modules.agreements import storage, validation
from modules.announce.poster import PostError


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


# --- validation ---------------------------------------------------------------


def test_validate_paypal_name_strips_and_accepts() -> None:
    assert validation.validate_paypal_name("  Jane Doe  ") == "Jane Doe"


def test_validate_paypal_name_rejects_empty() -> None:
    with pytest.raises(PostError):
        validation.validate_paypal_name("   ")


def test_validate_paypal_name_rejects_overlong() -> None:
    with pytest.raises(PostError):
        validation.validate_paypal_name("a" * (validation.MAX_PAYPAL_NAME_LENGTH + 1))


def test_validate_paypal_contact_accepts_email() -> None:
    assert validation.validate_paypal_contact(" jane@example.com ") == "jane@example.com"


def test_validate_paypal_contact_accepts_handle() -> None:
    assert validation.validate_paypal_contact(" @jane-doe1 ") == "@jane-doe1"


@pytest.mark.parametrize("bad", ["", "   ", "not-an-email", "jane@", "jane@example", "@", "no-at-sign"])
def test_validate_paypal_contact_rejects_junk(bad: str) -> None:
    with pytest.raises(PostError):
        validation.validate_paypal_contact(bad)


def test_validate_order_ref_blank_becomes_none() -> None:
    assert validation.validate_order_ref("   ") is None
    assert validation.validate_order_ref(None) is None


def test_validate_order_ref_rejects_overlong() -> None:
    with pytest.raises(PostError):
        validation.validate_order_ref("x" * (validation.MAX_ORDER_REF_LENGTH + 1))


def test_validate_void_reason_requires_text() -> None:
    with pytest.raises(PostError):
        validation.validate_void_reason("")


def test_validate_void_reason_accepts_and_strips() -> None:
    assert validation.validate_void_reason("  refunded  ") == "refunded"


# --- embeds ---------------------------------------------------------------


def test_pending_embed_shows_buyer_and_order() -> None:
    embed = validation.pending_embed(buyer_id=42, order_ref="#100")
    values = [f.value for f in embed.fields]
    assert "<@42>" in values
    assert "#100" in values


def test_pending_embed_omits_order_field_when_absent() -> None:
    embed = validation.pending_embed(buyer_id=42, order_ref=None)
    names = [f.name for f in embed.fields]
    assert "Order" not in names


def test_signed_embed_shows_relative_timestamp() -> None:
    when = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    record = {"buyer_id": 1, "order_ref": None, "signed_at": when}
    embed = validation.signed_embed(record)
    status = next(f.value for f in embed.fields if f.name == "Status")
    assert f"<t:{int(when.timestamp())}:R>" in status
    assert "✅" in status


def test_signed_embed_treats_naive_timestamp_as_utc() -> None:
    naive = datetime(2026, 8, 17, 12, 0)
    aware = naive.replace(tzinfo=timezone.utc)
    record = {"buyer_id": 1, "order_ref": None, "signed_at": naive}
    embed = validation.signed_embed(record)
    status = next(f.value for f in embed.fields if f.name == "Status")
    assert f"<t:{int(aware.timestamp())}:R>" in status


def test_voided_embed_includes_reason_and_keeps_signed_status() -> None:
    when = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    record = {
        "buyer_id": 1,
        "order_ref": None,
        "signed_at": when,
        "voided_at": when,
        "void_reason": "refunded, wrong buyer",
    }
    embed = validation.voided_embed(record)
    voided_field = next(f.value for f in embed.fields if f.name == "⚠️ Voided")
    assert "refunded, wrong buyer" in voided_field
    status = next(f.value for f in embed.fields if f.name == "Status")
    assert "✅ Signed" in status


def test_voided_embed_shows_not_signed_when_never_signed() -> None:
    when = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    record = {
        "buyer_id": 1,
        "order_ref": None,
        "signed_at": None,
        "voided_at": when,
        "void_reason": "mistake",
    }
    embed = validation.voided_embed(record)
    status = next(f.value for f in embed.fields if f.name == "Status")
    assert "Not signed" in status


def test_embed_for_record_dispatches_by_state() -> None:
    pending = {"buyer_id": 1, "order_ref": None, "signed_at": None, "voided_at": None}
    assert "Status" not in [f.name for f in validation.embed_for_record(pending).fields]

    when = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    signed = {"buyer_id": 1, "order_ref": None, "signed_at": when, "voided_at": None}
    assert "✅" in validation.embed_for_record(signed).fields[-1].value

    voided = {
        "buyer_id": 1,
        "order_ref": None,
        "signed_at": None,
        "voided_at": when,
        "void_reason": "x",
    }
    assert any(f.name == "⚠️ Voided" for f in validation.embed_for_record(voided).fields)


def test_lookup_embed_handles_empty() -> None:
    embed = validation.lookup_embed(99, [])
    assert "No agreements found" in embed.description


def test_lookup_embed_lists_status_per_row() -> None:
    when = datetime(2026, 8, 17, 12, 0, tzinfo=timezone.utc)
    rows = [
        {
            "id": 1,
            "order_ref": "#1",
            "signed_at": when,
            "voided_at": None,
            "void_reason": None,
            "paypal_name": "Jane Doe",
            "paypal_contact": "jane@example.com",
        },
        {
            "id": 2,
            "order_ref": None,
            "signed_at": None,
            "voided_at": None,
            "void_reason": None,
            "paypal_name": "John Roe",
            "paypal_contact": "@john-roe",
        },
    ]
    embed = validation.lookup_embed(99, rows)
    assert "#1" in embed.description
    assert "Signed" in embed.description
    assert "Pending" in embed.description
    assert "jane@example.com" in embed.description
    assert "2 agreement(s)" in embed.footer.text


# --- receipt document -------------------------------------------------------


def _signed_record(**overrides):
    base = {
        "id": 14,
        "buyer_id": 111,
        "sent_by": 222,
        "paypal_name": "Jane Doe",
        "paypal_contact": "jane@example.com",
        "order_ref": "#1042",
        "agreement_text": "TERMS...",
        "signed_at": datetime(2026, 8, 17, 14, 32, 1, tzinfo=timezone.utc),
        "voided_at": None,
        "voided_by": None,
        "void_reason": None,
    }
    base.update(overrides)
    return base


def test_receipt_text_includes_core_fields() -> None:
    text = validation.receipt_text(
        _signed_record(), buyer_label="janedoe#0", sender_label="mod#0"
    )
    assert "Agreement ID: #14" in text
    assert "Buyer: janedoe#0 (discord id 111)" in text
    assert "PayPal Name: Jane Doe" in text
    assert "PayPal Contact: jane@example.com" in text
    assert "Order Ref: #1042" in text
    assert "Sent By: mod#0 (discord id 222)" in text
    assert "Status: SIGNED" in text
    assert "2026-08-17 14:32:01 UTC" in text


def test_receipt_text_carries_the_full_agreement_text() -> None:
    text = validation.receipt_text(
        _signed_record(agreement_text="EXACT TERMS BUYER SAW"),
        buyer_label="janedoe#0",
        sender_label="mod#0",
    )
    assert "EXACT TERMS BUYER SAW" in text


def test_receipt_text_marks_unsigned() -> None:
    text = validation.receipt_text(
        _signed_record(signed_at=None), buyer_label="janedoe#0", sender_label="mod#0"
    )
    assert "Status: NOT YET SIGNED" in text
    assert "Signed At:" not in text


def test_receipt_text_includes_void_details() -> None:
    when = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
    text = validation.receipt_text(
        _signed_record(voided_at=when, voided_by=333, void_reason="refunded"),
        buyer_label="janedoe#0",
        sender_label="mod#0",
        voided_by_label="mod2#0",
    )
    assert "VOIDED: 2026-08-18 09:00:00 UTC" in text
    assert "Voided By: mod2#0" in text
    assert "Void Reason: refunded" in text


def test_receipt_text_omits_order_ref_placeholder_when_missing() -> None:
    text = validation.receipt_text(
        _signed_record(order_ref=None), buyer_label="janedoe#0", sender_label="mod#0"
    )
    assert "Order Ref: (none)" in text


# --- storage --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_agreement_inserts_all_fields() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1})
    pool = _fake_pool(conn)

    await storage.create_agreement(
        pool,
        guild_id=1,
        channel_id=2,
        buyer_id=3,
        sent_by=4,
        paypal_name="Jane Doe",
        paypal_contact="jane@example.com",
        order_ref="#1",
        agreement_text="terms",
    )

    sql, *params = conn.fetchrow.await_args.args
    assert "INSERT INTO agreements" in sql
    assert params == [1, 2, 3, 4, "Jane Doe", "jane@example.com", "#1", "terms"]


@pytest.mark.asyncio
async def test_sign_agreement_requires_matching_buyer_and_unsigned() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1, "signed_at": "now"})
    pool = _fake_pool(conn)

    result = await storage.sign_agreement(pool, 1, buyer_id=42)

    sql, agreement_id, buyer_id = conn.fetchrow.await_args.args
    assert "buyer_id = $2" in sql
    assert "signed_at IS NULL" in sql
    assert (agreement_id, buyer_id) == (1, 42)
    assert result == {"id": 1, "signed_at": "now"}


@pytest.mark.asyncio
async def test_sign_agreement_returns_none_when_already_signed() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)  # WHERE signed_at IS NULL excluded the row
    pool = _fake_pool(conn)

    result = await storage.sign_agreement(pool, 1, buyer_id=42)

    assert result is None


@pytest.mark.asyncio
async def test_void_agreement_never_touches_signed_at() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"id": 1, "voided_at": "now"})
    pool = _fake_pool(conn)

    await storage.void_agreement(pool, 1, voided_by=9, reason="refunded")

    sql = conn.fetchrow.await_args.args[0]
    assert "signed_at" not in sql
    assert "voided_at = NOW()" in sql


@pytest.mark.asyncio
async def test_delete_agreement_removes_row() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock()
    pool = _fake_pool(conn)

    await storage.delete_agreement(pool, 7)

    sql, agreement_id = conn.execute.await_args.args
    assert "DELETE FROM agreements" in sql
    assert agreement_id == 7


@pytest.mark.asyncio
async def test_list_views_to_restore_only_pending_unsigned() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1, "buyer_id": 5}])
    pool = _fake_pool(conn)

    rows = await storage.list_views_to_restore(pool)

    sql = conn.fetch.await_args.args[0]
    assert "signed_at IS NULL" in sql
    assert "voided_at IS NULL" in sql
    assert "message_id IS NOT NULL" in sql
    assert rows == [{"id": 1, "buyer_id": 5}]


# --- sign view --------------------------------------------------------------


def test_id_comes_from_the_clicked_button_not_the_instance() -> None:
    """Same lesson as base_post_commands: a persistent view is matched by
    custom_id, but the callback runs on whichever registered instance disnake
    dispatches to, so self.agreement_id may not match the clicked button."""
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        view = ac.AgreementView(1)
        inter = MagicMock()
        inter.data.custom_id = "agreement:sign:57"
        assert view._id_from(inter) == 57

    asyncio.run(check())


def test_id_falls_back_when_custom_id_is_unparsable() -> None:
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        view = ac.AgreementView(9)
        inter = MagicMock()
        inter.data.custom_id = "garbage"
        assert view._id_from(inter) == 9

    asyncio.run(check())


def test_signed_button_is_disabled_with_signed_label() -> None:
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        view = ac.AgreementView(1, signed=True)
        button = view.children[0]
        assert button.disabled is True
        assert button.label == "Signed"

    asyncio.run(check())


def test_pending_button_is_enabled_with_agree_label() -> None:
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        view = ac.AgreementView(1, signed=False)
        button = view.children[0]
        assert button.disabled is False
        assert button.label == "I Agree"

    asyncio.run(check())


def test_only_addressed_buyer_can_sign() -> None:
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        view = ac.AgreementView(1)
        inter = MagicMock()
        inter.data.custom_id = "agreement:sign:1"
        inter.author.id = 999  # not the buyer
        inter.bot.pool = MagicMock()
        inter.response.send_message = AsyncMock()

        record = {"id": 1, "buyer_id": 42, "voided_at": None}
        with patch.object(ac.storage, "get_agreement", AsyncMock(return_value=record)):
            await view._on_sign(inter)

        inter.response.send_message.assert_awaited_once()
        assert "isn't addressed to you" in inter.response.send_message.await_args.args[0]

    asyncio.run(check())


def test_voided_agreement_cannot_be_signed() -> None:
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        view = ac.AgreementView(1)
        inter = MagicMock()
        inter.data.custom_id = "agreement:sign:1"
        inter.author.id = 42
        inter.bot.pool = MagicMock()
        inter.response.send_message = AsyncMock()

        record = {"id": 1, "buyer_id": 42, "voided_at": "now"}
        with patch.object(ac.storage, "get_agreement", AsyncMock(return_value=record)):
            await view._on_sign(inter)

        inter.response.send_message.assert_awaited_once()
        assert "voided" in inter.response.send_message.await_args.args[0]

    asyncio.run(check())


# --- receipt command ---------------------------------------------------------


def test_user_label_prefers_the_cache() -> None:
    import asyncio

    from discord_bot.commands import agreement_commands as ac

    async def check():
        cog = ac.AgreementCommands.__new__(ac.AgreementCommands)
        cog.bot = MagicMock()
        cog.bot.get_user = MagicMock(return_value=MagicMock(__str__=lambda self: "janedoe"))
        cog.bot.fetch_user = AsyncMock()

        label = await cog._user_label(111)

        assert label == "janedoe"
        cog.bot.fetch_user.assert_not_called()

    asyncio.run(check())


def test_user_label_falls_back_to_fetch_then_unknown() -> None:
    """A buyer who left the guild (or was never cached) still needs a
    resolvable label on the receipt rather than the command failing."""
    import asyncio

    import disnake

    from discord_bot.commands import agreement_commands as ac

    async def check():
        cog = ac.AgreementCommands.__new__(ac.AgreementCommands)
        cog.bot = MagicMock()
        cog.bot.get_user = MagicMock(return_value=None)
        cog.bot.fetch_user = AsyncMock(side_effect=disnake.NotFound(MagicMock(status=404), "x"))

        label = await cog._user_label(999)

        assert label == "Unknown user (999)"

    asyncio.run(check())
