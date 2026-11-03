"""Which Stripe Payment Links belong to which server, found from its product pages.

A product (/product add) is a button label plus a website page; /subscribe shows
one link button per product. To confirm a payment, an admin picks it out of
Stripe — but every server shares one Stripe account, so the list must hold only
payments made through *this server's* Payment Links. Otherwise one server's
admins would see another server's buyers' names and emails.

How a server comes to own a link:

  1. The product's page is fetched and every https://buy.stripe.com/... link on it
     is collected (a product link that is itself a buy.stripe.com link is used
     as-is, with no fetch).
  2. Each URL is matched to its Payment Link id on our Stripe account. URLs on
     some other Stripe account are ignored — nothing of theirs can be looked up.
  3. The id is claimed in stripe_payment_links. Its primary key is the id, so the
     **first server to add a link owns it**; on any other server the button
     still works but that link's payments never appear in Confirm.

Pages change (a new month's CWL pack gets new Payment Links), so a server's
product pages are re-scanned every time Confirm is clicked and on /product list,
and any new links found are claimed then.

**Fetching admin-supplied URLs from the bot's own machine is an SSRF risk** —
anyone can add the bot, and it runs on a home network. So fetches are https-only
on port 443, every hostname must resolve to public addresses only (enforced in
the resolver, so a DNS answer can't change between check and connect), IP
literals are checked before every request, redirects are followed by hand so each
hop is checked again, and the size and time of a fetch are capped.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import aiohttp
import asyncpg

from modules.subscriptions import products, stripe_api

logger = logging.getLogger(__name__)

STRIPE_LINK_HOST = "buy.stripe.com"
STRIPE_LINK_RE = re.compile(r"https://buy\.stripe\.com/[A-Za-z0-9_]+")
PAGE_FETCH_TIMEOUT_SECONDS = 10
MAX_PAGE_BYTES = 1_000_000
MAX_REDIRECTS = 3
# A product page with more checkout links than this is not a normal product page.
MAX_LINKS_PER_PAGE = 20
# The account's Payment Link list changes rarely; reuse it between clicks.
LINK_INDEX_TTL_SECONDS = 600
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)
_USER_AGENT = "JulyBot (product page scan)"

# (fetched_at monotonic seconds, {url: plink_id}); reset to None in tests.
_link_index: tuple[float, dict[str, str]] | None = None


class LinkScanError(Exception):
    """A product page couldn't be read; the message is shown to the admin."""


@dataclass
class ScanResult:
    """What scanning one product's page found, for the /product add reply."""

    found: int = 0
    claimed: list[str] = field(default_factory=list)
    owned_elsewhere: list[str] = field(default_factory=list)
    not_on_our_account: int = 0
    error: str | None = None


# --- safe page fetch -----------------------------------------------------------


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global


class _PublicOnlyResolver(aiohttp.ThreadedResolver):
    """Drops private/loopback/link-local answers, so a hostname can't point the
    bot at the home network — checked at connect time, not just beforehand."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await super().resolve(host, port, family)
        public = [r for r in results if _is_public(r["host"])]
        if not public:
            raise OSError(f"{host} does not resolve to a public address")
        return public


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise LinkScanError("Only https:// pages can be read.")
    if parsed.port not in (None, 443):
        raise LinkScanError("Only pages on the standard https port can be read.")
    try:
        literal = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return  # A hostname: the resolver checks what it points at.
    if not _is_public(str(literal)):
        raise LinkScanError("That address isn't a public website.")


async def fetch_page(url: str) -> str:
    """The page's HTML, fetched with the SSRF guards described above."""
    connector = aiohttp.TCPConnector(resolver=_PublicOnlyResolver(), use_dns_cache=False)
    timeout = aiohttp.ClientTimeout(total=PAGE_FETCH_TIMEOUT_SECONDS)
    try:
        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            for _ in range(MAX_REDIRECTS + 1):
                _check_url(url)
                async with session.get(
                    url, allow_redirects=False, headers={"User-Agent": _USER_AGENT}
                ) as resp:
                    if resp.status in _REDIRECT_STATUSES:
                        location = resp.headers.get("Location")
                        if not location:
                            raise LinkScanError("The page redirected nowhere.")
                        url = urljoin(url, location)
                        continue
                    if resp.status != 200:
                        raise LinkScanError(f"The page returned HTTP {resp.status}.")
                    body = await resp.content.read(MAX_PAGE_BYTES)
                    return body.decode(resp.charset or "utf-8", errors="replace")
            raise LinkScanError("The page redirected too many times.")
    except LinkScanError:
        raise
    except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as e:
        raise LinkScanError(f"Couldn't load the page ({type(e).__name__}).") from e


async def find_stripe_links(url: str) -> list[str]:
    """Every distinct buy.stripe.com link a product points at."""
    if urlparse(url).hostname == STRIPE_LINK_HOST:
        match = STRIPE_LINK_RE.match(url)
        return [match.group(0)] if match else []
    html = await fetch_page(url)
    return list(dict.fromkeys(STRIPE_LINK_RE.findall(html)))[:MAX_LINKS_PER_PAGE]


async def _payment_link_index() -> dict[str, str]:
    """{url: plink_id} for our Stripe account, cached for LINK_INDEX_TTL_SECONDS."""
    global _link_index
    now = time.monotonic()
    if _link_index is not None and now - _link_index[0] < LINK_INDEX_TTL_SECONDS:
        return _link_index[1]
    links = await stripe_api.list_payment_links()
    _link_index = (now, links)
    return links


# --- ownership storage ------------------------------------------------------------


async def claim_link(
    pool: asyncpg.Pool, guild_id: int, product_id: int, plink_id: str, url: str
) -> int:
    """Claim a Payment Link for a server; returns the guild that owns it afterwards.

    First claim wins (plink_id is the primary key). A link the server already
    owns stays put, so re-scanning never moves it between servers.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            WITH ins AS (
                INSERT INTO stripe_payment_links (plink_id, url, guild_id, product_id)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (plink_id) DO NOTHING
                RETURNING guild_id
            )
            SELECT guild_id FROM ins
            UNION ALL
            SELECT guild_id FROM stripe_payment_links WHERE plink_id = $1
            LIMIT 1;
            """,
            plink_id,
            url,
            guild_id,
            product_id,
        )
        if row is None:
            # Lost a race with another server's claim of the same link in the
            # same instant: the statement's snapshot predates their row.
            return await conn.fetchval(
                "SELECT guild_id FROM stripe_payment_links WHERE plink_id = $1;", plink_id
            )
    return row["guild_id"]


async def owned_links(pool: asyncpg.Pool, guild_id: int) -> dict[str, int | None]:
    """{plink_id: product_id} for every Payment Link a server owns."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT plink_id, product_id FROM stripe_payment_links WHERE guild_id = $1;",
            guild_id,
        )
    return {r["plink_id"]: r["product_id"] for r in rows}


async def release_product_links(pool: asyncpg.Pool, product_id: int) -> int:
    """Give up the links a product claimed (on /product remove). Returns how many."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM stripe_payment_links WHERE product_id = $1;", product_id
        )
    return int(result.split()[-1])


async def product_for_link(
    pool: asyncpg.Pool, guild_id: int, plink_id: str | None
) -> asyncpg.Record | None:
    """The product (id, name) a server's Payment Link was found on, if any."""
    if not plink_id:
        return None
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT p.id, p.name
              FROM stripe_payment_links l
              JOIN products p ON p.id = l.product_id
             WHERE l.plink_id = $1 AND l.guild_id = $2;
            """,
            plink_id,
            guild_id,
        )


# --- orchestration ----------------------------------------------------------------


async def scan_product(pool: asyncpg.Pool, guild_id: int, product) -> ScanResult:
    """Find a product's Stripe links and claim the unowned ones for this server.

    Never raises: a page or Stripe failure is reported in `error`, since the
    product's button works regardless — only Confirm's list is affected.
    """
    result = ScanResult()
    try:
        urls = await find_stripe_links(product["payment_link"])
    except LinkScanError as e:
        result.error = str(e)
        return result
    result.found = len(urls)
    if not urls:
        return result

    try:
        index = await _payment_link_index()
    except stripe_api.StripeNotConfiguredError:
        result.error = "Stripe isn't configured on this bot, so its payments can't be listed."
        return result
    except stripe_api.StripeApiError as e:
        result.error = f"Couldn't reach Stripe: {e}"
        return result

    for url in urls:
        plink_id = index.get(url)
        if plink_id is None:
            result.not_on_our_account += 1
            continue
        owner = await claim_link(pool, guild_id, product["id"], plink_id, url)
        if owner != guild_id:
            result.owned_elsewhere.append(url)
        else:
            result.claimed.append(url)
    return result


async def refresh_guild_links(pool: asyncpg.Pool, guild_id: int) -> None:
    """Re-scan every product page of a server and claim any new links found."""
    for product in await products.list_products(pool, guild_id):
        result = await scan_product(pool, guild_id, product)
        if result.error:
            logger.warning(
                "Payment-link scan of product %s (guild=%s) failed: %s",
                product["id"], guild_id, result.error,
            )


async def list_guild_checkouts(
    pool: asyncpg.Pool, guild_id: int, product_ids=None
) -> list[stripe_api.SubscriptionSummary]:
    """Paid checkouts made through this server's own Payment Links, newest first.

    `product_ids` limits it to links found on those products' pages (a purchase
    started with `/subscribe product:`); None means every product.

    Raises StripeNotConfiguredError / StripeApiError like the stripe_api calls.
    """
    if not stripe_api.is_configured():
        # Checked up front: otherwise a server with no links yet would be told
        # "no checkouts found" rather than why.
        raise stripe_api.StripeNotConfiguredError("STRIPE_SECRET_KEY isn't set.")
    await refresh_guild_links(pool, guild_id)
    owned = await owned_links(pool, guild_id)
    if product_ids is not None:
        wanted = set(product_ids)
        owned = {plink: pid for plink, pid in owned.items() if pid in wanted}
    if not owned:
        return []
    return await stripe_api.list_checkouts(owned.keys())
