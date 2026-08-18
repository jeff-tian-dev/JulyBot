"""Validate + render /agreement content.

Pool-free and Discord-send-free where possible, so it's unit-testable without a
bot or database. Persistence lives in storage.py; PDF attachment + sending live
in the Cog.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import disnake

from modules.announce.poster import PostError
from modules.agreements.document import AGREEMENT_SUMMARY

MAX_PAYPAL_NAME_LENGTH = 200
MAX_PAYPAL_CONTACT_LENGTH = 320
MAX_ORDER_REF_LENGTH = 200
MAX_VOID_REASON_LENGTH = 512

# Not a full RFC 5322 validator — just enough to catch obvious typos. A
# moderator can still enter a wrong-but-well-formed email; that's an accepted
# limitation rather than something worth over-engineering around.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# PayPal also identifies accounts by a $Cashtag-style @handle (e.g. @jane-doe1),
# separate from an email address — accept either.
_PAYPAL_HANDLE_RE = re.compile(r"^@[A-Za-z0-9_.-]{1,50}$")

AGREEMENT_EMBED_COLOUR = 0x2ECC71
VOIDED_EMBED_COLOUR = 0x95A5A6


def validate_paypal_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise PostError("The PayPal name can't be empty.")
    if len(cleaned) > MAX_PAYPAL_NAME_LENGTH:
        raise PostError(f"The PayPal name is limited to {MAX_PAYPAL_NAME_LENGTH} characters.")
    return cleaned


def validate_paypal_contact(contact: str) -> str:
    """Accept either a PayPal email address or a $Cashtag-style @handle."""
    cleaned = (contact or "").strip()
    if not cleaned:
        raise PostError("The PayPal email or @ can't be empty.")
    if len(cleaned) > MAX_PAYPAL_CONTACT_LENGTH:
        raise PostError(
            f"The PayPal email or @ is limited to {MAX_PAYPAL_CONTACT_LENGTH} characters."
        )
    if not (_EMAIL_RE.match(cleaned) or _PAYPAL_HANDLE_RE.match(cleaned)):
        raise PostError(
            f"`{cleaned}` doesn't look like a valid PayPal email or @handle "
            "(e.g. jane@example.com or @jane-doe)."
        )
    return cleaned


def validate_order_ref(order_ref: str | None) -> str | None:
    if order_ref is None:
        return None
    cleaned = order_ref.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_ORDER_REF_LENGTH:
        raise PostError(f"The order reference is limited to {MAX_ORDER_REF_LENGTH} characters.")
    return cleaned


def validate_void_reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not cleaned:
        raise PostError("A void reason is required.")
    if len(cleaned) > MAX_VOID_REASON_LENGTH:
        raise PostError(f"The void reason is limited to {MAX_VOID_REASON_LENGTH} characters.")
    return cleaned


def _relative_timestamp(dt: datetime) -> str:
    """A Discord `<t:…:R>` token, which renders as e.g. '3 hours ago'.

    `signed_at`/`voided_at` are naive TIMESTAMPs written by NOW(), so they're
    read as UTC (Supabase runs UTC).
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:R>"


def _absolute_utc(dt: datetime) -> str:
    """A plain UTC timestamp string for a receipt document (no Discord markup —
    a downloaded file won't render <t:…> tokens)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def receipt_text(
    record,
    *,
    buyer_label: str,
    sender_label: str,
    voided_by_label: str | None = None,
) -> str:
    """Plain-text proof-of-signature document for a signed (or voided) agreement.

    Not tied to Discord's embed limits — meant to be forwarded to a payment
    processor as an attachment, so it carries the exact agreement text the
    buyer saw rather than a pointer to a possibly-since-edited template.
    """
    lines = [
        "PURCHASE AGREEMENT RECEIPT",
        "=" * 27,
        f"Agreement ID: #{record['id']}",
        f"Buyer: {buyer_label} (discord id {record['buyer_id']})",
        f"PayPal Name: {record['paypal_name']}",
        f"PayPal Contact: {record['paypal_contact']}",
        f"Order Ref: {record['order_ref'] or '(none)'}",
    ]

    if record["signed_at"]:
        lines.append(f"Signed At: {_absolute_utc(record['signed_at'])}")
        lines.append("Status: SIGNED")
    else:
        lines.append("Status: NOT YET SIGNED")

    lines.append(f"Sent By: {sender_label} (discord id {record['sent_by']})")

    if record["voided_at"]:
        lines.append("")
        lines.append(f"VOIDED: {_absolute_utc(record['voided_at'])}")
        lines.append(f"Voided By: {voided_by_label or record['voided_by']}")
        lines.append(f"Void Reason: {record['void_reason']}")

    lines.append("")
    lines.append("--- AGREEMENT TEXT AS SIGNED ---")
    lines.append(record["agreement_text"])

    return "\n".join(lines)


def confirmation_embed(record, *, buyer_label: str) -> disnake.Embed:
    """The "please confirm your details" panel shown before a buyer can sign.

    Surfaces exactly what a mod typed in — the buyer's Discord identity, the
    PayPal name, and the PayPal contact — so a typo or wrong-buyer mistake is
    caught before it becomes a permanent signed record, not after.
    """
    embed = disnake.Embed(
        title="Confirm Your Details",
        description=(
            "Please confirm the details below are correct before signing the "
            "purchase agreement."
        ),
        colour=AGREEMENT_EMBED_COLOUR,
    )
    embed.add_field(name="Name", value=record["paypal_name"], inline=False)
    embed.add_field(
        name="Discord",
        value=f"{buyer_label} (<@{record['buyer_id']}>, id {record['buyer_id']})",
        inline=False,
    )
    embed.add_field(name="PayPal Contact", value=record["paypal_contact"], inline=False)
    return embed


def pending_embed(*, buyer_id: int, order_ref: str | None) -> disnake.Embed:
    """The embed posted alongside the PDF attachment and the I Agree button."""
    embed = disnake.Embed(
        title="Purchase Agreement",
        description=AGREEMENT_SUMMARY,
        colour=AGREEMENT_EMBED_COLOUR,
    )
    embed.add_field(name="Buyer", value=f"<@{buyer_id}>", inline=True)
    if order_ref:
        embed.add_field(name="Order", value=order_ref, inline=True)
    return embed


def signed_embed(record) -> disnake.Embed:
    """The embed shown after the buyer has clicked I Agree."""
    embed = pending_embed(buyer_id=record["buyer_id"], order_ref=record["order_ref"])
    embed.add_field(
        name="Status",
        value=f"✅ Signed {_relative_timestamp(record['signed_at'])}",
        inline=False,
    )
    return embed


def voided_embed(record) -> disnake.Embed:
    """The embed shown after a moderator has voided the agreement."""
    embed = pending_embed(buyer_id=record["buyer_id"], order_ref=record["order_ref"])
    embed.colour = VOIDED_EMBED_COLOUR
    status = "✅ Signed" if record["signed_at"] else "⌛ Not signed"
    embed.add_field(name="Status", value=status, inline=False)
    embed.add_field(
        name="⚠️ Voided",
        value=f"{_relative_timestamp(record['voided_at'])} — {record['void_reason']}",
        inline=False,
    )
    return embed


def embed_for_record(record) -> disnake.Embed:
    """Rebuild the right embed for an agreement's current state."""
    if record["voided_at"]:
        return voided_embed(record)
    if record["signed_at"]:
        return signed_embed(record)
    return pending_embed(buyer_id=record["buyer_id"], order_ref=record["order_ref"])


def lookup_embed(buyer_id: int, rows) -> disnake.Embed:
    """The /agreement lookup panel: every agreement sent to a buyer."""
    if not rows:
        return disnake.Embed(
            title="Agreements",
            description=f"No agreements found for <@{buyer_id}>.",
            colour=AGREEMENT_EMBED_COLOUR,
        )

    lines = []
    for row in rows:
        if row["voided_at"]:
            status = f"⚠️ VOIDED — {row['void_reason']}"
        elif row["signed_at"]:
            status = f"✅ Signed {_relative_timestamp(row['signed_at'])}"
        else:
            status = "⌛ Pending"
        order = f" ({row['order_ref']})" if row["order_ref"] else ""
        lines.append(
            f"**#{row['id']}**{order} — {status}\n"
            f"PayPal: {row['paypal_name']} ({row['paypal_contact']})"
        )

    embed = disnake.Embed(
        title="Agreements",
        description="\n\n".join(lines),
        colour=AGREEMENT_EMBED_COLOUR,
    )
    embed.set_footer(text=f"{len(rows)} agreement(s) for this buyer")
    return embed


__all__ = [
    "AGREEMENT_EMBED_COLOUR",
    "MAX_ORDER_REF_LENGTH",
    "MAX_PAYPAL_CONTACT_LENGTH",
    "MAX_PAYPAL_NAME_LENGTH",
    "MAX_VOID_REASON_LENGTH",
    "VOIDED_EMBED_COLOUR",
    "confirmation_embed",
    "embed_for_record",
    "lookup_embed",
    "pending_embed",
    "receipt_text",
    "signed_embed",
    "validate_order_ref",
    "validate_paypal_contact",
    "validate_paypal_name",
    "validate_void_reason",
    "voided_embed",
]
