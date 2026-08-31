"""Unit tests for the agreements module: rendering and storage.

Two row shapes must both render: historical rows from the retired
moderator-driven /agreement send flow (payer_name / payment_method /
payment_contact populated), and self-serve rows written by /subscribe (those
columns NULL). The dual support is the thing most likely to regress, so both
are covered here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.agreements import storage, validation


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


SIGNED_AT = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _self_serve_row(**overrides):
    """A row as written by /subscribe: no payment fields, no sender."""
    row = {
        "id": 7,
        "guild_id": 1,
        "channel_id": None,
        "message_id": None,
        "buyer_id": 4242,
        "sent_by": None,
        "payer_name": None,
        "payment_method": None,
        "payment_contact": None,
        "order_ref": None,
        "agreement_text": "TERMS AND CONDITIONS\nAll sales are final.",
        "signed_at": SIGNED_AT,
        "voided_at": None,
        "voided_by": None,
        "void_reason": None,
    }
    row.update(overrides)
    return row


def _moderator_row(**overrides):
    """A historical row from the retired /agreement send flow."""
    row = _self_serve_row(
        channel_id=99,
        message_id=100,
        sent_by=555,
        payer_name="Jane Doe",
        payment_method="PayPal",
        payment_contact="jane@example.com",
        order_ref="ORDER-1",
    )
    row.update(overrides)
    return row


# --- terms embed --------------------------------------------------------------


def test_terms_embed_carries_the_summary() -> None:
    from modules.agreements.document import AGREEMENT_SUMMARY

    embed = validation.terms_embed()
    assert embed.title == "Purchase Agreement"
    assert embed.description == AGREEMENT_SUMMARY


# --- receipt_text -------------------------------------------------------------


def test_receipt_text_includes_core_fields_for_a_self_serve_row() -> None:
    text = validation.receipt_text(_self_serve_row(), buyer_label="buyer#1")

    assert "Agreement ID: #7" in text
    assert "buyer#1" in text
    assert "discord id 4242" in text
    assert "Status: SIGNED" in text
    assert "2026-08-30 12:00:00 UTC" in text


def test_receipt_text_omits_null_payment_lines() -> None:
    """A self-serve row has no payer/method/contact — those lines must be
    absent entirely rather than rendered as "None"."""
    text = validation.receipt_text(_self_serve_row(), buyer_label="buyer#1")

    assert "Payer Name:" not in text
    assert "Payment Method:" not in text
    assert "Payment Contact:" not in text
    assert "Order Ref:" not in text
    assert "Sent By:" not in text
    assert "None" not in text


def test_receipt_text_keeps_payment_lines_for_a_historical_row() -> None:
    text = validation.receipt_text(
        _moderator_row(), buyer_label="buyer#1", sender_label="mod#2"
    )

    assert "Payer Name: Jane Doe" in text
    assert "Payment Method: PayPal" in text
    assert "Payment Contact: jane@example.com" in text
    assert "Order Ref: ORDER-1" in text
    assert "Sent By: mod#2" in text


def test_receipt_text_carries_the_full_agreement_text() -> None:
    row = _self_serve_row(agreement_text="CLAUSE ONE\nCLAUSE TWO")
    text = validation.receipt_text(row, buyer_label="buyer#1")

    assert "--- AGREEMENT TEXT AS SIGNED ---" in text
    assert "CLAUSE ONE" in text
    assert "CLAUSE TWO" in text


def test_receipt_text_marks_unsigned() -> None:
    text = validation.receipt_text(_self_serve_row(signed_at=None), buyer_label="buyer#1")
    assert "Status: NOT YET SIGNED" in text
    assert "Status: SIGNED" not in text


def test_receipt_text_includes_void_details() -> None:
    row = _self_serve_row(
        voided_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        voided_by=555,
        void_reason="refunded",
    )
    text = validation.receipt_text(row, buyer_label="buyer#1", voided_by_label="mod#2")

    assert "VOIDED: 2026-08-31 00:00:00 UTC" in text
    assert "Voided By: mod#2" in text
    assert "Void Reason: refunded" in text


def test_receipt_text_treats_naive_timestamp_as_utc() -> None:
    """signed_at is a naive TIMESTAMP written by NOW(); Supabase runs UTC."""
    row = _self_serve_row(signed_at=datetime(2026, 8, 30, 12, 0, 0))
    text = validation.receipt_text(row, buyer_label="buyer#1")
    assert "2026-08-30 12:00:00 UTC" in text


# --- lookup_embed -------------------------------------------------------------


def test_lookup_embed_handles_empty() -> None:
    embed = validation.lookup_embed(4242, [])
    assert "No agreements found" in embed.description


def test_lookup_embed_omits_payment_line_for_self_serve_rows() -> None:
    embed = validation.lookup_embed(4242, [_self_serve_row()])

    assert "**#7**" in embed.description
    assert "Signed" in embed.description
    assert "None" not in embed.description
    assert "PayPal" not in embed.description


def test_lookup_embed_shows_payment_line_for_historical_rows() -> None:
    embed = validation.lookup_embed(4242, [_moderator_row()])

    assert "PayPal: Jane Doe" in embed.description
    assert "jane@example.com" in embed.description
    assert "(ORDER-1)" in embed.description


def test_lookup_embed_lists_status_per_row() -> None:
    rows = [
        _self_serve_row(id=1),
        _self_serve_row(id=2, signed_at=None),
        _self_serve_row(
            id=3,
            voided_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
            void_reason="chargeback",
        ),
    ]
    embed = validation.lookup_embed(4242, rows)

    assert "✅ Signed" in embed.description
    assert "⌛ Pending" in embed.description
    assert "⚠️ VOIDED — chargeback" in embed.description
    assert embed.footer.text == "3 agreement(s) for this buyer"


# --- storage ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_signed_agreement_inserts_already_signed() -> None:
    """Self-serve has no pending window — the row only exists because the
    buyer just clicked I Agree, so signed_at is stamped in the same insert."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_self_serve_row())

    result = await storage.create_signed_agreement(
        _fake_pool(conn), guild_id=1, buyer_id=4242, agreement_text="TERMS"
    )

    assert result["id"] == 7
    sql, *args = conn.fetchrow.await_args.args
    assert "INSERT INTO agreements" in sql
    assert "signed_at" in sql
    assert "NOW()" in sql
    assert args == [1, 4242, "TERMS"]


@pytest.mark.asyncio
async def test_create_signed_agreement_stores_the_verbatim_terms() -> None:
    """The text is copied per-row, not referenced, so a later wording change
    never alters what a past buyer is shown to have agreed to."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_self_serve_row())

    await storage.create_signed_agreement(
        _fake_pool(conn), guild_id=1, buyer_id=4242, agreement_text="EXACT TEXT v1"
    )

    assert conn.fetchrow.await_args.args[3] == "EXACT TEXT v1"


@pytest.mark.asyncio
async def test_get_agreement_returns_none_when_missing() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    assert await storage.get_agreement(_fake_pool(conn), 999) is None


@pytest.mark.asyncio
async def test_list_agreements_for_buyer_orders_newest_first() -> None:
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[_self_serve_row(id=2), _self_serve_row(id=1)])

    rows = await storage.list_agreements_for_buyer(_fake_pool(conn), 4242)

    assert [r["id"] for r in rows] == [2, 1]
    sql, *args = conn.fetch.await_args.args
    assert "ORDER BY created_at DESC" in sql
    assert args == [4242]


@pytest.mark.asyncio
async def test_sign_agreement_requires_matching_buyer_and_unsigned() -> None:
    """Retained for the historical flow: the buyer_id match stops anyone but
    the addressed buyer signing, and the signed_at IS NULL guard makes a
    double-click a no-op."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_moderator_row())

    await storage.sign_agreement(_fake_pool(conn), 7, 4242)

    sql, *args = conn.fetchrow.await_args.args
    assert "signed_at IS NULL" in sql
    assert "buyer_id = $2" in sql
    assert args == [7, 4242]


@pytest.mark.asyncio
async def test_void_agreement_never_touches_signed_at() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=_moderator_row())

    await storage.void_agreement(_fake_pool(conn), 7, voided_by=555, reason="refunded")

    sql = conn.fetchrow.await_args.args[0]
    assert "voided_at = NOW()" in sql
    assert "signed_at" not in sql.split("WHERE")[0]
