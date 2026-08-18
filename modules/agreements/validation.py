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
MAX_PAYPAL_EMAIL_LENGTH = 320
MAX_ORDER_REF_LENGTH = 200
MAX_VOID_REASON_LENGTH = 512

# Not a full RFC 5322 validator — just enough to catch obvious typos. A
# moderator can still enter a wrong-but-well-formed email; that's an accepted
# limitation rather than something worth over-engineering around.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

AGREEMENT_EMBED_COLOUR = 0x2ECC71
VOIDED_EMBED_COLOUR = 0x95A5A6


def validate_paypal_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise PostError("The PayPal name can't be empty.")
    if len(cleaned) > MAX_PAYPAL_NAME_LENGTH:
        raise PostError(f"The PayPal name is limited to {MAX_PAYPAL_NAME_LENGTH} characters.")
    return cleaned


def validate_paypal_email(email: str) -> str:
    cleaned = (email or "").strip()
    if not cleaned:
        raise PostError("The PayPal email can't be empty.")
    if len(cleaned) > MAX_PAYPAL_EMAIL_LENGTH:
        raise PostError(f"The PayPal email is limited to {MAX_PAYPAL_EMAIL_LENGTH} characters.")
    if not _EMAIL_RE.match(cleaned):
        raise PostError(f"`{cleaned}` doesn't look like a valid email address.")
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
            f"PayPal: {row['paypal_name']} <{row['paypal_email']}>"
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
    "MAX_PAYPAL_EMAIL_LENGTH",
    "MAX_PAYPAL_NAME_LENGTH",
    "MAX_VOID_REASON_LENGTH",
    "VOIDED_EMBED_COLOUR",
    "embed_for_record",
    "lookup_embed",
    "pending_embed",
    "signed_embed",
    "validate_order_ref",
    "validate_paypal_email",
    "validate_paypal_name",
    "validate_void_reason",
    "voided_embed",
]
