"""/subscribe — run a purchase through a ticket, from payment to confirmation.

An admin opens the flow for a buyer with `/subscribe <member>`. That posts ONE
public message in the ticket which is edited in place as the purchase advances,
so the moderator watching the ticket can see exactly how far along the buyer is:

    pending  → Stripe payment link buttons, waiting on the buyer to pay
    confirmed→ an admin picks the buyer's subscription out of Stripe

**Terms are accepted inside Stripe Checkout, not here.** There was previously an
in-Discord "I Agree" step gating the payment links, backed by a T&C PDF; it was
removed once Stripe's own checkout was configured to collect that consent, since
it made buyers accept the same terms twice. Consequences worth knowing:
  - `agreements.signed_at` and `agreement_text` are NULL/empty on new rows.
    Historical rows still carry both and must keep rendering — see validation.py.
  - The consent record for a new purchase lives in Stripe, not in this database,
    so a dispute is answered from the Stripe Dashboard.
The row itself is kept: it is what every button's custom_id is keyed to, and it
carries the purchase's state and the confirmation audit trail.

Only an admin can Confirm or Cancel. That gate is enforced against the database
row rather than the in-memory view, since a persistent view outlives the process.

**Confirming requires linking a real Stripe subscription** — there is no
attestation-only path. The bot pulls the live subscription list from Stripe's
API (no webhook needed, since that is an outbound call), the admin says which
one belongs to this buyer, and the result is stored in `subscribers` along with
who made that call. Stripe has no idea who its customers are on Discord, so
that match is human judgement; everything around it is verified.

The payment buttons are disnake.ButtonStyle.link buttons, which carry a URL
instead of a custom_id: Discord opens them client-side, so there is no callback
to dispatch for those. The other three buttons are ordinary callbacks on a
persistent view, restored on startup by register_persistent_views().

The links are recurring monthly subscriptions — Stripe re-bills the buyer each
month until they cancel. The tier embed says so explicitly: an unexpected second
charge is what generates chargebacks. Cancellation is manual too (there's no
Stripe customer portal wired up), so the copy points buyers at a moderator.
"""
from __future__ import annotations

import logging

import asyncpg
import disnake
from disnake.ext import commands

from modules.agreements import storage
from modules.agreements import storage as agreement_storage
from modules.agreements.validation import status_embed
from modules.subscriptions import stripe_api
from modules.subscriptions import storage as subscriber_storage
from modules.subscriptions.tiers import TIERS

logger = logging.getLogger(__name__)

EMBED_COLOUR = 0x5865F2
ADMIN_PERMS = disnake.Permissions(administrator=True)
NO_PINGS = disnake.AllowedMentions.none()
CUSTOM_ID_PREFIX = "purchase"
MAX_CANCEL_REASON_LENGTH = 512
MAX_PAYER_NAME_LENGTH = 200
# The admin is mid-confirmation; if they wander off, clicking Confirm again is cheap.
PICKER_TIMEOUT_SECONDS = 300


def build_subscribe_embed() -> disnake.Embed:
    """The tier list. Renders every tier, marking any that aren't purchasable."""
    embed = disnake.Embed(
        title="Subscriptions",
        description=(
            "Pick a tier below to subscribe securely through Stripe.\n"
            "This is a **monthly subscription** — you'll be billed automatically "
            "each month until you cancel. To cancel, message a moderator."
        ),
        colour=EMBED_COLOUR,
    )
    for tier in TIERS.values():
        value = f"**${tier.price_usd}/month**\n{tier.description}"
        if not tier.available:
            value += "\n*Currently unavailable — ask a moderator.*"
        embed.add_field(name=tier.name, value=value, inline=True)
    embed.set_footer(text="After paying, a moderator will confirm it here.")
    return embed


def build_subscribe_view() -> disnake.ui.View | None:
    """Link buttons for the purchasable tiers, or None if none are configured.

    A link button with an empty URL is rejected by Discord, so unavailable
    tiers are left out of the view entirely (the embed still lists them).
    """
    buttons = _payment_buttons()
    if not buttons:
        return None

    view = disnake.ui.View(timeout=None)
    for button in buttons:
        view.add_item(button)
    return view


def _payment_buttons() -> list[disnake.ui.Button]:
    return [
        disnake.ui.Button(
            label=f"{tier.name} — ${tier.price_usd}",
            style=disnake.ButtonStyle.link,
            url=tier.payment_link,
        )
        for tier in TIERS.values()
        if tier.available
    ]


class CancelReasonModal(disnake.ui.Modal):
    """Asks the admin why a purchase is being cancelled, for the record."""

    def __init__(self, agreement_id: int) -> None:
        self.agreement_id = agreement_id
        super().__init__(
            title="Cancel Purchase",
            custom_id=f"{CUSTOM_ID_PREFIX}:cancelmodal:{agreement_id}",
            components=[
                disnake.ui.TextInput(
                    label="Reason",
                    custom_id="reason",
                    style=disnake.TextInputStyle.short,
                    max_length=MAX_CANCEL_REASON_LENGTH,
                    placeholder="e.g. buyer changed their mind",
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        reason = inter.text_values["reason"].strip() or "No reason given"
        record = await storage.void_agreement(
            inter.bot.pool, self.agreement_id, voided_by=inter.author.id, reason=reason
        )
        if record is None:
            await inter.response.send_message(
                "That purchase's data is gone — it may have been deleted.", ephemeral=True
            )
            return

        logger.info(
            "Purchase id=%s cancelled by %s: %s", self.agreement_id, inter.author.id, reason
        )
        await inter.response.edit_message(
            embed=status_embed(record), view=PurchaseView(record), attachments=[]
        )


async def finalize_confirmation(
    inter: disnake.Interaction,
    *,
    agreement_id: int,
    subscription,
    payer_name: str,
    message: disnake.Message | None,
) -> None:
    """Confirm the purchase, record the subscriber, and advance the message.

    Order matters: confirm_agreement first, because its SQL guards are what
    stop a cancelled or already-confirmed purchase being recorded. Only once
    that succeeds is a subscriber row written.
    """
    confirmed = await storage.confirm_agreement(
        inter.bot.pool, agreement_id, confirmed_by=inter.author.id
    )
    if confirmed is None:
        await inter.response.send_message(
            "That purchase can't be confirmed — it's already confirmed or cancelled.",
            ephemeral=True,
        )
        return

    await agreement_storage.set_payer_name(inter.bot.pool, agreement_id, payer_name)

    try:
        await subscriber_storage.create_subscriber(
            inter.bot.pool,
            discord_id=confirmed["buyer_id"],
            guild_id=confirmed["guild_id"],
            agreement_id=agreement_id,
            stripe_subscription_id=subscription.subscription_id,
            stripe_customer_id=subscription.customer_id,
            payer_name=payer_name,
            email=subscription.email,
            tier=None,
            status=subscription.status,
            current_period_end=subscription.current_period_end,
            linked_by=inter.author.id,
        )
    except asyncpg.UniqueViolationError:
        # Already attributed to someone — say who rather than silently
        # re-pointing a payment at a second Discord user.
        existing = await subscriber_storage.get_subscriber_by_stripe_id(
            inter.bot.pool, subscription.subscription_id
        )
        owner = f"<@{existing['discord_id']}>" if existing else "another member"
        await inter.response.send_message(
            f"That Stripe subscription is already linked to {owner}. "
            "The agreement was confirmed, but no new subscriber record was created.",
            ephemeral=True,
            allowed_mentions=NO_PINGS,
        )
        return

    logger.info(
        "Purchase id=%s confirmed by %s, linked to Stripe subscription %s",
        agreement_id,
        inter.author.id,
        subscription.subscription_id,
    )

    # Refresh the record so the embed shows the name that was just stored.
    updated = await storage.get_agreement(inter.bot.pool, agreement_id) or confirmed
    if message is not None:
        await message.edit(embed=status_embed(updated), view=PurchaseView(updated))

    await inter.response.send_message(
        f"Confirmed — linked to `{subscription.subscription_id}` for **{payer_name}**.",
        ephemeral=True,
    )


class PayerNameModal(disnake.ui.Modal):
    """Asks for the buyer's full name when Stripe has none on file."""

    def __init__(self, agreement_id: int, subscription, message) -> None:
        self.agreement_id = agreement_id
        self.subscription = subscription
        self.message = message
        super().__init__(
            title="Buyer's Full Name",
            custom_id=f"{CUSTOM_ID_PREFIX}:namemodal:{agreement_id}",
            components=[
                disnake.ui.TextInput(
                    label="Full name",
                    custom_id="payer_name",
                    style=disnake.TextInputStyle.short,
                    max_length=MAX_PAYER_NAME_LENGTH,
                    placeholder="As it appears on the payment",
                )
            ],
        )

    async def callback(self, inter: disnake.ModalInteraction) -> None:
        name = inter.text_values["payer_name"].strip()
        if not name:
            await inter.response.send_message("A name is required.", ephemeral=True)
            return
        await finalize_confirmation(
            inter,
            agreement_id=self.agreement_id,
            subscription=self.subscription,
            payer_name=name,
            message=self.message,
        )


class StripePickerView(disnake.ui.View):
    """Lets an admin pick which Stripe subscription belongs to this buyer.

    Stripe has no idea who its customers are on Discord, so this match is human
    judgement — recorded in subscribers.linked_by rather than left implicit.

    Short-lived and ephemeral, so unlike PurchaseView it needs no persistent
    registration: if it expires the admin clicks Confirm Payment again.
    """

    def __init__(self, agreement_id: int, subscriptions, *, message) -> None:
        super().__init__(timeout=PICKER_TIMEOUT_SECONDS)
        self.agreement_id = agreement_id
        self.message = message
        self._by_id = {s.subscription_id: s for s in subscriptions}

        select = disnake.ui.StringSelect(
            placeholder="Which Stripe subscription?",
            options=[
                disnake.SelectOption(
                    label=s.label()[:100],
                    value=s.subscription_id,
                    description=(s.email or s.subscription_id)[:100],
                )
                for s in subscriptions
            ],
        )
        select.callback = self._on_pick
        self.add_item(select)

    async def _on_pick(self, inter: disnake.MessageInteraction) -> None:
        subscription = self._by_id.get(inter.data.values[0])
        if subscription is None:
            await inter.response.send_message(
                "That subscription is no longer in the list — try again.", ephemeral=True
            )
            return

        # Stripe's name is the one actually on the payment, so prefer it; only
        # ask the admin when Stripe has none.
        if subscription.name:
            await finalize_confirmation(
                inter,
                agreement_id=self.agreement_id,
                subscription=subscription,
                payer_name=subscription.name,
                message=self.message,
            )
            return

        await inter.response.send_modal(
            PayerNameModal(self.agreement_id, subscription, self.message)
        )


class PurchaseView(disnake.ui.View):
    """The buttons under a purchase status message.

    Persistent (timeout=None) with deterministic custom_ids, so a restarted bot
    re-attaches handlers to messages posted in a previous run — see
    register_persistent_views.

    Which buttons exist depends on the row's state, so the view is rebuilt from
    the record on every edit rather than mutated in place.
    """

    def __init__(self, record) -> None:
        super().__init__(timeout=None)
        self.agreement_id = record["id"]

        terminal = bool(record["voided_at"] or record["confirmed_at"])

        if terminal:
            self.stop()
            return

        confirm = disnake.ui.Button(
            label="Confirm Payment",
            style=disnake.ButtonStyle.primary,
            custom_id=f"{CUSTOM_ID_PREFIX}:confirm:{self.agreement_id}",
        )
        confirm.callback = self._on_confirm
        self.add_item(confirm)

        cancel = disnake.ui.Button(
            label="Cancel",
            style=disnake.ButtonStyle.danger,
            custom_id=f"{CUSTOM_ID_PREFIX}:cancel:{self.agreement_id}",
        )
        cancel.callback = self._on_cancel
        self.add_item(cancel)

        # Shown immediately. Terms are accepted in Stripe Checkout now, so there
        # is no in-Discord step gating these any more.
        for button in _payment_buttons():
            self.add_item(button)

    def _id_from(self, inter: disnake.MessageInteraction) -> int:
        """The agreement id encoded in the clicked button's custom_id.

        A persistent view is matched by custom_id, but the callback runs on
        whichever registered instance disnake dispatches to — its
        `self.agreement_id` is NOT necessarily the clicked purchase's. Always
        take the id from the interaction (the same lesson as
        base_post_commands.py).
        """
        custom_id = (inter.data.custom_id or "") if inter.data else ""
        try:
            return int(custom_id.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            logger.warning("Unparsable purchase custom_id %r", custom_id)
            return self.agreement_id

    @staticmethod
    def _is_admin(inter: disnake.MessageInteraction) -> bool:
        perms = getattr(inter.author, "guild_permissions", None)
        return bool(perms and (perms.administrator or perms.manage_guild))

    async def _on_confirm(self, inter: disnake.MessageInteraction) -> None:
        """Open the Stripe picker. Confirming requires choosing a real subscription."""
        if not self._is_admin(inter):
            await inter.response.send_message(
                "Only a moderator can confirm a payment.", ephemeral=True
            )
            return

        agreement_id = self._id_from(inter)

        try:
            subscriptions = await stripe_api.list_recent_subscriptions()
        except stripe_api.StripeNotConfiguredError:
            await inter.response.send_message(
                "Stripe isn't configured (`STRIPE_SECRET_KEY` is unset), so payments "
                "can't be verified. Ask whoever runs the bot to add it.",
                ephemeral=True,
            )
            return
        except stripe_api.StripeApiError as exc:
            logger.warning("Stripe lookup failed while confirming id=%s: %s", agreement_id, exc)
            await inter.response.send_message(
                f"Couldn't reach Stripe: {exc}", ephemeral=True
            )
            return

        if not subscriptions:
            await inter.response.send_message(
                "Stripe has no active subscriptions to link. If the payment just went "
                "through, give it a moment and try again.",
                ephemeral=True,
            )
            return

        # Ephemeral: only the admin needs this, and it carries buyer emails.
        await inter.response.send_message(
            "Pick the Stripe subscription for this buyer:",
            view=StripePickerView(agreement_id, subscriptions, message=inter.message),
            ephemeral=True,
        )

    async def _on_cancel(self, inter: disnake.MessageInteraction) -> None:
        if not self._is_admin(inter):
            await inter.response.send_message(
                "Only a moderator can cancel a purchase.", ephemeral=True
            )
            return
        await inter.response.send_modal(CancelReasonModal(self._id_from(inter)))


class SubscribeCommands(commands.Cog):
    def __init__(self, bot: commands.InteractionBot) -> None:
        self.bot = bot

    @commands.slash_command(
        name="subscribe",
        description="Start a purchase for a member: Stripe payment, then confirmation.",
        default_member_permissions=ADMIN_PERMS,
        contexts=disnake.InteractionContextTypes(guild=True),
    )
    async def subscribe(
        self,
        inter: disnake.ApplicationCommandInteraction,
        member: disnake.User = commands.Param(description="The buyer."),
    ) -> None:
        await inter.response.defer(ephemeral=True)

        # Check purchasability before creating anything, so no purchase is
        # started for something that can't actually be bought.
        if not _payment_buttons():
            logger.warning("/subscribe used with no payment links configured")
            await inter.edit_original_response(
                "Subscriptions aren't set up yet — no Stripe payment links are configured."
            )
            return

        record = await storage.create_pending_agreement(
            self.bot.pool,
            guild_id=inter.guild.id,
            channel_id=inter.channel.id,
            buyer_id=member.id,
            sent_by=inter.author.id,
        )

        try:
            message = await inter.channel.send(
                content=member.mention,
                embed=status_embed(record),
                view=PurchaseView(record),
                allowed_mentions=disnake.AllowedMentions(users=[member]),
            )
        except Exception as exc:  # noqa: BLE001 — surface any failure + log
            # Roll back so no orphan row points at a message that never existed.
            await storage.delete_agreement(self.bot.pool, record["id"])
            logger.exception("Failed to post purchase for buyer=%s", member.id)
            await inter.edit_original_response(f"Couldn't start the purchase: {exc}")
            return

        await storage.attach_message(self.bot.pool, record["id"], message.id)
        await inter.edit_original_response(
            f"Purchase #{record['id']} started for {member.mention} — {message.jump_url}",
            allowed_mentions=NO_PINGS,
        )


async def register_persistent_views(bot: commands.InteractionBot) -> None:
    """Re-attach purchase button handlers to messages from previous bot runs.

    Called once after login. Without this, buttons on old messages do nothing
    ("This interaction failed") because the in-memory View is gone.
    """
    try:
        rows = await storage.list_views_to_restore(bot.pool)
    except Exception:  # noqa: BLE001 — never block startup on this
        logger.exception("Couldn't load purchases to restore views")
        return

    for row in rows:
        # list_views_to_restore only returns unterminated rows, so the view is
        # rebuilt from the columns that decide which buttons are live.
        bot.add_view(
            PurchaseView(
                {
                    "id": row["id"],
                    "buyer_id": row["buyer_id"],
                    "signed_at": row["signed_at"],
                    "confirmed_at": None,
                    "voided_at": None,
                }
            )
        )
    if rows:
        logger.info("Restored %d purchase view(s)", len(rows))


def setup(bot: commands.InteractionBot) -> None:
    bot.add_cog(SubscribeCommands(bot))
