"""/purchases — review recorded purchases and fix a mislinked payment.

Two subcommands, both admin-only:

    /purchases list [member]    recent purchases (paged), or one member's history
    /purchases relink <id>      repoint a row at a different Stripe payment
    /purchases archive          close out the previous month's purchases

**What relink fixes, precisely:** an admin picking the WRONG PAYMENT out of the
Stripe dropdown when confirming. The Discord buyer is not changed — `/subscribe`
names them up front, so the buyer is the half that was already correct. Relink
therefore takes only the subscriber id and opens a fresh Stripe picker.

Before this existed a mislink was unfixable from Discord: the unique index on
stripe_subscription_id meant the correct buyer could never be linked to that
payment afterwards, and the only remedy was editing the database by hand.

The row is corrected in place rather than deleted and re-created, so `id`,
`created_at`, `agreement_id` and the original `linked_by` all survive — a
correction is exactly when the history of who touched a row matters.

**Archiving never deletes.** `subscribers` rows are the purchase log and the
evidence for a payment dispute; closing out a month only sets `archived_at`, so
the rows drop out of the active list but stay fully readable. That flag is also
what bounds a ONE-TIME purchase: its Stripe status stays `succeeded` forever, so
without archiving it would grant access permanently.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
import disnake
from disnake.ext import commands

from modules.subscriptions import stripe_api
from modules.subscriptions import storage as subscriber_storage

logger = logging.getLogger(__name__)

ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
EMBED_COLOUR = 0x5865F2
# Purchases shown per page. Discord caps an embed description at 4096 chars and
# each row is ~2 short lines, so this stays well under it while keeping a page
# scannable. The list is paged rather than truncated because a month can carry
# ~100 purchases and the row you want is often not in the newest handful.
PAGE_SIZE = 10
# How many purchases a listing loads at most. Paging happens in memory over
# this, which is cheap at this scale and avoids re-querying on every click.
LIST_LIMIT = 200
# The admin is mid-correction; if they wander off, re-running the command is cheap.
PICKER_TIMEOUT_SECONDS = 300
# Archiving touches many rows at once, so it is previewed and confirmed rather
# than fired on the first click.
CONFIRM_TIMEOUT_SECONDS = 120


def _money(record) -> str:
    return f"{record['tier'] or '—'}"


def purchase_line(record) -> str:
    """One purchase, rendered for the list embed."""
    who = f"<@{record['discord_id']}>"
    parts = [f"**#{record['id']}** — {who} — `{record['stripe_subscription_id']}`"]

    detail = [record["status"]]
    if record["payer_name"]:
        detail.append(record["payer_name"])
    if record["created_at"]:
        detail.append(f"<t:{int(record['created_at'].timestamp())}:d>")
    parts.append("  " + " · ".join(detail))

    # Surface a correction in the list itself — someone scanning for a mistake
    # should see which rows have already been touched.
    if record["relinked_at"]:
        parts.append(f"  ↻ relinked by <@{record['relinked_by']}>")
    return "\n".join(parts)


def page_count(total: int) -> int:
    """Pages needed for `total` records — at least 1, so an empty list renders."""
    return max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)


def build_list_embed(
    records, *, member: disnake.User | None = None, page: int = 0
) -> disnake.Embed:
    """One page of the /purchases list panel.

    `page` is 0-based and clamped, so a stale click after the list shrank
    renders the last page rather than an empty one.
    """
    if member is not None:
        title = f"Purchases — {member.display_name}"
        empty = f"No purchases recorded for {member.mention}."
    else:
        title = "Recent Purchases"
        empty = "No purchases recorded yet."

    if not records:
        return disnake.Embed(title=title, description=empty, colour=EMBED_COLOUR)

    pages = page_count(len(records))
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    shown = records[start : start + PAGE_SIZE]

    embed = disnake.Embed(
        title=title,
        description="\n".join(purchase_line(r) for r in shown),
        colour=EMBED_COLOUR,
    )
    embed.set_footer(
        text=(
            f"Page {page + 1}/{pages} · {len(records)} total · "
            "/purchases relink <id> fixes a wrong payment"
        )
    )
    return embed


class PurchaseListView(disnake.ui.View):
    """Prev/next paging over an already-loaded list of purchases.

    Records are held on the view rather than re-queried per click: a listing is
    capped at LIST_LIMIT rows, so paging in memory is cheap and the page cannot
    shift under the admin while they read it.

    Ephemeral and short-lived like the pickers, so it needs no persistent
    registration — if it times out, re-run the command.
    """

    def __init__(self, records, *, member: disnake.User | None = None) -> None:
        super().__init__(timeout=PICKER_TIMEOUT_SECONDS)
        self.records = records
        self.member = member
        self.page = 0

        self.prev = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary)
        self.prev.callback = self._on_prev
        self.add_item(self.prev)

        self.next = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary)
        self.next.callback = self._on_next
        self.add_item(self.next)

        self._sync_buttons()

    def _sync_buttons(self) -> None:
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= page_count(len(self.records)) - 1

    def embed(self) -> disnake.Embed:
        return build_list_embed(self.records, member=self.member, page=self.page)

    async def _turn(self, inter: disnake.MessageInteraction, delta: int) -> None:
        self.page = max(0, min(self.page + delta, page_count(len(self.records)) - 1))
        self._sync_buttons()
        await inter.response.edit_message(
            embed=self.embed(), view=self, allowed_mentions=NO_PINGS
        )

    async def _on_prev(self, inter: disnake.MessageInteraction) -> None:
        await self._turn(inter, -1)

    async def _on_next(self, inter: disnake.MessageInteraction) -> None:
        await self._turn(inter, 1)


class RelinkPickerView(disnake.ui.View):
    """Pick the Stripe payment a subscriber row should actually point at.

    Mirrors StripePickerView in subscribe_commands: short-lived and ephemeral,
    so it needs no persistent registration — if it expires, re-run the command.

    Payments already linked to another row are shown but marked, because the
    unique index would reject them anyway; saying so up front beats a confusing
    failure after the admin has already chosen.
    """

    def __init__(self, subscriber_id: int, purchases, *, already_linked: set[str]) -> None:
        super().__init__(timeout=PICKER_TIMEOUT_SECONDS)
        self.subscriber_id = subscriber_id
        self._by_id = {p.subscription_id: p for p in purchases}
        self._already_linked = already_linked

        select = disnake.ui.StringSelect(
            placeholder="Which Stripe purchase should this be?",
            options=[
                disnake.SelectOption(
                    label=p.label()[:100],
                    value=p.subscription_id,
                    description=(
                        "⚠️ already linked to another purchase"
                        if p.subscription_id in already_linked
                        else (p.email or p.subscription_id)
                    )[:100],
                )
                for p in purchases
            ],
        )
        select.callback = self._on_pick
        self.add_item(select)

    async def _on_pick(self, inter: disnake.MessageInteraction) -> None:
        purchase = self._by_id.get(inter.data.values[0])
        if purchase is None:
            await inter.response.send_message(
                "That purchase is no longer in the list — run the command again.",
                ephemeral=True,
            )
            return

        try:
            updated = await subscriber_storage.relink_subscriber(
                inter.bot.pool,
                self.subscriber_id,
                stripe_subscription_id=purchase.subscription_id,
                stripe_customer_id=purchase.customer_id,
                payer_name=purchase.name,
                email=purchase.email,
                status=purchase.status,
                current_period_end=purchase.current_period_end,
                relinked_by=inter.author.id,
            )
        except asyncpg.UniqueViolationError:
            # Name who holds it rather than just refusing — the admin needs to
            # know which other row to fix first.
            existing = await subscriber_storage.get_subscriber_by_stripe_id(
                inter.bot.pool, purchase.subscription_id
            )
            holder = f"purchase #{existing['id']}" if existing else "another purchase"
            await inter.response.send_message(
                f"That Stripe payment is already linked to {holder}. "
                "Relink that one first, or pick a different payment.",
                ephemeral=True,
            )
            return

        if updated is None:
            await inter.response.send_message(
                f"Purchase #{self.subscriber_id} no longer exists.", ephemeral=True
            )
            return

        logger.info(
            "Purchase #%s relinked to %s by %s",
            self.subscriber_id,
            purchase.subscription_id,
            inter.author.id,
        )
        await inter.response.edit_message(
            content=(
                f"Purchase **#{updated['id']}** for <@{updated['discord_id']}> is now "
                f"linked to `{purchase.subscription_id}`"
                + (f" ({purchase.name})" if purchase.name else "")
                + "."
            ),
            embed=None,
            view=None,
            allowed_mentions=NO_PINGS,
        )


class ArchiveConfirmView(disnake.ui.View):
    """Confirm a month close-out before it touches anything.

    Archiving is bulk and easy to fire on the wrong month, so the admin sees
    the count and cutoff first. Short-lived and ephemeral like the pickers —
    nothing to restore after a restart.
    """

    def __init__(self, guild_id: int, *, before: datetime, count: int) -> None:
        super().__init__(timeout=CONFIRM_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.before = before
        self.count = count

        confirm = disnake.ui.Button(
            label=f"Archive {count} purchase{'s' if count != 1 else ''}",
            style=disnake.ButtonStyle.primary,
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = disnake.ui.Button(label="Cancel", style=disnake.ButtonStyle.secondary)
        cancel.callback = self._on_cancel
        self.add_item(cancel)

    async def _on_confirm(self, inter: disnake.MessageInteraction) -> None:
        archived = await subscriber_storage.archive_subscribers(
            inter.bot.pool,
            self.guild_id,
            before=self.before,
            archived_by=inter.author.id,
        )
        logger.info(
            "Archived %s purchases before %s in guild %s by %s",
            len(archived),
            self.before.isoformat(),
            self.guild_id,
            inter.author.id,
        )
        await inter.response.edit_message(
            content=(
                f"Archived **{len(archived)}** purchase(s) made before "
                f"<t:{int(self.before.timestamp())}:d>. They stay in the log and on "
                "receipts — they just no longer count as current access."
            ),
            embed=None,
            view=None,
        )

    async def _on_cancel(self, inter: disnake.MessageInteraction) -> None:
        await inter.response.edit_message(
            content="Nothing archived.", embed=None, view=None
        )


def month_start(now: datetime | None = None) -> datetime:
    """First instant of the current month, UTC — the archive cutoff.

    Everything bought BEFORE this belongs to a previous month. Supabase runs
    UTC and `created_at` is written by NOW(), so the boundary matches the
    stored timestamps without a timezone conversion.
    """
    now = now or datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class PurchaseCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="purchases",
        default_member_permissions=ADMIN_PERMS,
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def purchases(self, inter: disnake.ApplicationCommandInteraction) -> None:
        """Parent group; disnake never invokes this directly."""

    @purchases.sub_command(name="list", description="Show recorded purchases.")
    async def list_purchases(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(
            default=None, description="Only this member's purchases."
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)
        try:
            if member is not None:
                records = await subscriber_storage.list_subscribers_for_discord_id(
                    self.bot.pool, member.id
                )
            else:
                records = await subscriber_storage.list_recent_subscribers(
                    self.bot.pool, inter.guild.id, limit=LIST_LIMIT
                )
        except Exception as exc:  # noqa: BLE001 — surface it rather than time out
            logger.exception("Couldn't list purchases")
            await inter.edit_original_response(f"Couldn't load purchases: {exc}")
            return

        # Only attach paging controls when there is more than one page — two
        # dead buttons under a three-row list is just noise.
        if len(records) > PAGE_SIZE:
            view = PurchaseListView(records, member=member)
            await inter.edit_original_response(
                embed=view.embed(), view=view, allowed_mentions=NO_PINGS
            )
            return

        await inter.edit_original_response(
            embed=build_list_embed(records, member=member), allowed_mentions=NO_PINGS
        )

    @purchases.sub_command(
        name="relink",
        description="Point a purchase at a different Stripe payment.",
    )
    async def relink(
        self,
        inter: disnake.ApplicationCommandInteraction,
        purchase_id: int = commands.Param(
            description="The purchase id from /purchases list.", min_value=1
        ),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        record = await subscriber_storage.get_subscriber(self.bot.pool, purchase_id)
        if record is None:
            await inter.edit_original_response(
                f"No purchase #{purchase_id}. Check `/purchases list`."
            )
            return

        try:
            purchases = await stripe_api.list_recent_subscriptions()
        except stripe_api.StripeNotConfiguredError:
            await inter.edit_original_response(
                "Stripe isn't configured (`STRIPE_SECRET_KEY` is unset), so payments "
                "can't be looked up. Ask whoever runs the bot to add it."
            )
            return
        except stripe_api.StripeApiError as exc:
            logger.warning("Stripe lookup failed while relinking #%s: %s", purchase_id, exc)
            await inter.edit_original_response(f"Couldn't reach Stripe: {exc}")
            return

        if not purchases:
            await inter.edit_original_response(
                "Stripe has no recent purchases to link — no active subscriptions and "
                "no successful one-time payments."
            )
            return

        already = await subscriber_storage.linked_stripe_ids(self.bot.pool)
        # The row's own payment is what we're replacing, so it isn't a conflict.
        already.discard(record["stripe_subscription_id"])

        embed = disnake.Embed(
            title=f"Relink purchase #{record['id']}",
            description=(
                f"Buyer: <@{record['discord_id']}> (unchanged)\n"
                f"Currently linked to: `{record['stripe_subscription_id']}`"
                + (f" — {record['payer_name']}" if record["payer_name"] else "")
            ),
            colour=EMBED_COLOUR,
        )
        await inter.edit_original_response(
            embed=embed,
            view=RelinkPickerView(record["id"], purchases, already_linked=already),
            allowed_mentions=NO_PINGS,
        )


    @purchases.sub_command(
        name="archive",
        description="Close out purchases from before this month.",
    )
    async def archive(self, inter: disnake.ApplicationCommandInteraction) -> None:
        await inter.response.defer(ephemeral=True)

        before = month_start()
        active = await subscriber_storage.list_active_subscribers(
            self.bot.pool, inter.guild.id
        )
        stale = [r for r in active if r["created_at"] and r["created_at"] < _naive(before)]

        if not stale:
            await inter.edit_original_response(
                "Nothing to archive — no active purchases predate this month."
            )
            return

        embed = disnake.Embed(
            title="Archive previous months",
            description=(
                f"**{len(stale)}** active purchase(s) were made before "
                f"<t:{int(before.timestamp())}:d> and would be closed out.\n\n"
                "They stay in the purchase log and on receipts — archiving only "
                "stops them counting as current access."
            ),
            colour=EMBED_COLOUR,
        )
        embed.add_field(
            name="Affected",
            value="\n".join(
                f"#{r['id']} — <@{r['discord_id']}>" for r in stale[:PAGE_SIZE]
            )
            + (f"\n…and {len(stale) - PAGE_SIZE} more" if len(stale) > PAGE_SIZE else ""),
            inline=False,
        )
        await inter.edit_original_response(
            embed=embed,
            view=ArchiveConfirmView(inter.guild.id, before=before, count=len(stale)),
            allowed_mentions=NO_PINGS,
        )


def _naive(dt: datetime) -> datetime:
    """asyncpg returns TIMESTAMP (no tz) for created_at, so compare like with like."""
    return dt.replace(tzinfo=None)


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(PurchaseCommands(bot))
