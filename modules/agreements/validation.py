"""Render purchase-agreement content.

Pool-free and Discord-send-free, so it's unit-testable without a bot or
database. Persistence lives in storage.py.

Everything here must handle BOTH row shapes in the agreements table: historical
rows from the retired moderator-driven flow (which carry payer_name /
payment_method / payment_contact for a PayPal/Venmo/Wise payment) and
self-serve rows from /subscribe (where those are NULL because Stripe knows who
paid). See the table comment in database/models.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import disnake

from modules.agreements.document import AGREEMENT_SUMMARY

AGREEMENT_EMBED_COLOUR = 0x2ECC71
VOIDED_EMBED_COLOUR = 0x95A5A6


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


def terms_embed() -> disnake.Embed:
    """Step one of /subscribe: the terms the buyer must accept before paying.

    The full text is too long for an embed, so this is the summary; the
    complete Terms and Conditions PDF is attached to the same message.
    """
    return disnake.Embed(
        title="Purchase Agreement",
        description=AGREEMENT_SUMMARY,
        colour=AGREEMENT_EMBED_COLOUR,
    )


def status_embed(record) -> disnake.Embed:
    """The purchase status message, rendered for whichever stage the row is in.

    One embed edited in place as the purchase progresses, so the ticket shows a
    single running record rather than a pile of messages: pending -> signed ->
    confirmed, or voided from any of them.
    """
    buyer = f"<@{record['buyer_id']}>"

    if record["voided_at"]:
        embed = disnake.Embed(
            title="Purchase Cancelled",
            description=f"This purchase for {buyer} was cancelled.",
            colour=VOIDED_EMBED_COLOUR,
        )
        embed.add_field(
            name="Cancelled",
            value=f"{_relative_timestamp(record['voided_at'])} — {record['void_reason']}",
            inline=False,
        )
        return embed

    if record["confirmed_at"]:
        embed = disnake.Embed(
            title="Purchase Confirmed",
            description=(
                f"{buyer}'s payment has been matched to a Stripe subscription. "
                "Access can now be set up."
            ),
            colour=AGREEMENT_EMBED_COLOUR,
        )
        if record["payer_name"]:
            embed.add_field(name="Paid by", value=record["payer_name"], inline=False)
        embed.add_field(
            name="Agreed",
            value=_relative_timestamp(record["signed_at"]),
            inline=True,
        )
        embed.add_field(
            name="Confirmed",
            value=(
                f"{_relative_timestamp(record['confirmed_at'])}"
                f" by <@{record['confirmed_by']}>"
            ),
            inline=True,
        )
        return embed

    if record["signed_at"]:
        embed = disnake.Embed(
            title="Purchase Agreement — Signed",
            description=(
                f"{buyer} has accepted the Terms and Conditions.\n\n"
                "**Next:** pick a tier below and pay through Stripe. Once the payment "
                "shows up in Stripe, a moderator will confirm it here."
            ),
            colour=AGREEMENT_EMBED_COLOUR,
        )
        embed.add_field(
            name="Agreed", value=_relative_timestamp(record["signed_at"]), inline=False
        )
        return embed

    embed = disnake.Embed(
        title="Purchase Agreement",
        description=AGREEMENT_SUMMARY,
        colour=AGREEMENT_EMBED_COLOUR,
    )
    embed.add_field(
        name="Waiting on",
        value=f"{buyer} to read the attached Terms and Conditions and click **I Agree**.",
        inline=False,
    )
    return embed


def receipt_text(
    record,
    *,
    buyer_label: str,
    sender_label: str | None = None,
    voided_by_label: str | None = None,
    confirmed_by_label: str | None = None,
) -> str:
    """Plain-text proof-of-signature document for a signed (or voided) agreement.

    Not tied to Discord's embed limits — meant to be forwarded to a payment
    processor as an attachment, so it carries the exact agreement text the
    buyer saw rather than a pointer to a possibly-since-edited template.

    Payment and sender lines are omitted entirely when NULL (a self-serve row)
    rather than printed as "None".
    """
    lines = [
        "PURCHASE AGREEMENT RECEIPT",
        "=" * 27,
        f"Agreement ID: #{record['id']}",
        f"Buyer: {buyer_label} (discord id {record['buyer_id']})",
    ]

    if record["payer_name"]:
        lines.append(f"Payer Name: {record['payer_name']}")
    if record["payment_method"]:
        lines.append(f"Payment Method: {record['payment_method']}")
    if record["payment_contact"]:
        lines.append(f"Payment Contact: {record['payment_contact']}")
    if record["order_ref"]:
        lines.append(f"Order Ref: {record['order_ref']}")

    if record["signed_at"]:
        lines.append(f"Signed At: {_absolute_utc(record['signed_at'])}")
        lines.append("Status: SIGNED")
    else:
        lines.append("Status: NOT YET SIGNED")

    if record["confirmed_at"]:
        lines.append(f"Payment Confirmed At: {_absolute_utc(record['confirmed_at'])}")
        lines.append(
            f"Payment Confirmed By: {confirmed_by_label or record['confirmed_by']} "
            f"(discord id {record['confirmed_by']})"
        )
        lines.append(
            "  (Matched to a live Stripe subscription at confirmation time by"
        )
        lines.append("   the moderator named above.)")

    if record["sent_by"]:
        lines.append(f"Sent By: {sender_label or record['sent_by']} (discord id {record['sent_by']})")

    if record["voided_at"]:
        lines.append("")
        lines.append(f"VOIDED: {_absolute_utc(record['voided_at'])}")
        lines.append(f"Voided By: {voided_by_label or record['voided_by']}")
        lines.append(f"Void Reason: {record['void_reason']}")

    lines.append("")
    lines.append("--- AGREEMENT TEXT AS SIGNED ---")
    lines.append(record["agreement_text"])

    return "\n".join(lines)


def lookup_embed(buyer_id: int, rows) -> disnake.Embed:
    """The /agreement lookup panel: every agreement signed by a buyer."""
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
        elif row["confirmed_at"]:
            status = f"💳 Paid — confirmed {_relative_timestamp(row['confirmed_at'])}"
        elif row["signed_at"]:
            status = f"✅ Signed {_relative_timestamp(row['signed_at'])} — payment unconfirmed"
        else:
            status = "⌛ Pending"
        order = f" ({row['order_ref']})" if row["order_ref"] else ""
        line = f"**#{row['id']}**{order} — {status}"
        # Only historical moderator-flow rows carry payment details.
        if row["payment_method"] and row["payer_name"]:
            line += f"\n{row['payment_method']}: {row['payer_name']}"
            if row["payment_contact"]:
                line += f" ({row['payment_contact']})"
        lines.append(line)

    embed = disnake.Embed(
        title="Agreements",
        description="\n\n".join(lines),
        colour=AGREEMENT_EMBED_COLOUR,
    )
    embed.set_footer(text=f"{len(rows)} agreement(s) for this buyer")
    return embed


__all__ = [
    "AGREEMENT_EMBED_COLOUR",
    "VOIDED_EMBED_COLOUR",
    "lookup_embed",
    "receipt_text",
    "status_embed",
    "terms_embed",
]
