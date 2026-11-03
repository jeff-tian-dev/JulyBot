"""Unit tests for modules.subscriptions.payment_links (mocked pool, HTTP and Stripe)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from database import models
from modules.subscriptions import payment_links, stripe_api

PAGE = "https://seventhmonth.com/sub/"
LINK_A = "https://buy.stripe.com/14AeVcdJ86hJeYIfWO8og08"
LINK_B = "https://buy.stripe.com/7sY28qdJ88pRaIs6me8og07"


class _Ctx:
    def __init__(self, value=None) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


def _fake_pool(conn) -> MagicMock:
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_Ctx(conn))
    return pool


@pytest.fixture(autouse=True)
def _fresh_link_index():
    payment_links._link_index = None
    yield
    payment_links._link_index = None


# --- SSRF guards ------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://seventhmonth.com/sub/",       # not https
        "https://127.0.0.1/admin",            # loopback literal
        "https://192.168.1.1/",               # home network
        "https://169.254.169.254/latest/",    # link-local metadata address
        "https://[::1]/",                     # IPv6 loopback
        "https://[::ffff:10.0.0.1]/",         # IPv4-mapped private
        "https://seventhmonth.com:8080/sub/", # non-standard port
        "file:///etc/passwd",
    ],
)
def test_unsafe_urls_are_refused_before_any_request(url) -> None:
    with pytest.raises(payment_links.LinkScanError):
        payment_links._check_url(url)


def test_public_urls_pass_the_pre_check() -> None:
    payment_links._check_url(PAGE)
    payment_links._check_url("https://8.8.8.8/")


@pytest.mark.asyncio
async def test_resolver_drops_private_answers_and_refuses_all_private() -> None:
    """Checked at connect time, so a DNS answer can't change after the check."""
    resolver = payment_links._PublicOnlyResolver()
    answers = [{"host": "10.0.0.5"}, {"host": "93.184.216.34"}]
    with patch.object(payment_links.aiohttp.ThreadedResolver, "resolve", new=AsyncMock(return_value=answers)):
        assert await resolver.resolve("mixed.example") == [{"host": "93.184.216.34"}]
    with patch.object(
        payment_links.aiohttp.ThreadedResolver, "resolve", new=AsyncMock(return_value=[{"host": "127.0.0.1"}])
    ):
        with pytest.raises(OSError):
            await resolver.resolve("localhost")


def _response(status: int, *, location: str | None = None, body: bytes = b""):
    resp = MagicMock()
    resp.status = status
    resp.headers = {"Location": location} if location else {}
    resp.charset = "utf-8"
    resp.content.read = AsyncMock(return_value=body)
    return resp


def _fake_session(*responses):
    session = MagicMock()
    session.get = MagicMock(side_effect=[_Ctx(r) for r in responses])
    return MagicMock(return_value=_Ctx(session)), session


@pytest.mark.asyncio
async def test_redirects_are_checked_hop_by_hop() -> None:
    """A public page redirecting to the home network must not be followed."""
    factory, session = _fake_session(_response(302, location="https://192.168.1.1/"))
    with patch.object(payment_links.aiohttp, "ClientSession", factory):
        with pytest.raises(payment_links.LinkScanError, match="public website"):
            await payment_links.fetch_page(PAGE)
    assert session.get.call_count == 1
    assert session.get.call_args.kwargs["allow_redirects"] is False


@pytest.mark.asyncio
async def test_fetch_follows_a_safe_redirect_and_caps_the_body() -> None:
    factory, session = _fake_session(
        _response(301, location="/sub/new/"), _response(200, body=b"<a href='x'>hi</a>")
    )
    with patch.object(payment_links.aiohttp, "ClientSession", factory):
        html = await payment_links.fetch_page(PAGE)

    assert "hi" in html
    assert session.get.call_args.args[0] == "https://seventhmonth.com/sub/new/"


@pytest.mark.asyncio
async def test_too_many_redirects_give_up() -> None:
    hops = [_response(302, location=PAGE)] * (payment_links.MAX_REDIRECTS + 1)
    factory, _ = _fake_session(*hops)
    with patch.object(payment_links.aiohttp, "ClientSession", factory):
        with pytest.raises(payment_links.LinkScanError, match="too many"):
            await payment_links.fetch_page(PAGE)


@pytest.mark.asyncio
async def test_a_failing_page_is_a_scan_error() -> None:
    factory, _ = _fake_session(_response(404))
    with patch.object(payment_links.aiohttp, "ClientSession", factory):
        with pytest.raises(payment_links.LinkScanError, match="404"):
            await payment_links.fetch_page(PAGE)


# --- finding links -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bare_payment_link_is_used_without_fetching() -> None:
    with patch.object(payment_links, "fetch_page", new=AsyncMock()) as fetch:
        assert await payment_links.find_stripe_links(LINK_A + "?prefilled_email=x") == [LINK_A]
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_page_links_are_found_and_deduplicated() -> None:
    html = f'<a href="{LINK_A}">Buy</a> <a href="{LINK_B}">Buy</a> <a href="{LINK_A}">again</a>'
    with patch.object(payment_links, "fetch_page", new=AsyncMock(return_value=html)):
        assert await payment_links.find_stripe_links(PAGE) == [LINK_A, LINK_B]


# --- ownership storage ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_claim_is_first_come_and_reports_the_owner() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"guild_id": 99})

    owner = await payment_links.claim_link(_fake_pool(conn), 1, 3, "plink_a", LINK_A)

    sql, *args = conn.fetchrow.await_args.args
    assert "ON CONFLICT (plink_id) DO NOTHING" in sql
    assert args == ["plink_a", LINK_A, 1, 3]
    assert owner == 99


@pytest.mark.asyncio
async def test_claim_survives_losing_a_simultaneous_race() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=42)

    assert await payment_links.claim_link(_fake_pool(conn), 1, 3, "plink_a", LINK_A) == 42


@pytest.mark.asyncio
async def test_product_lookup_is_scoped_to_the_server() -> None:
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    await payment_links.product_for_link(_fake_pool(conn), 1, "plink_a")

    sql, *args = conn.fetchrow.await_args.args
    assert "l.guild_id = $2" in sql
    assert args == ["plink_a", 1]
    assert await payment_links.product_for_link(_fake_pool(conn), 1, None) is None


@pytest.mark.asyncio
async def test_release_only_touches_that_products_links() -> None:
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="DELETE 2")

    assert await payment_links.release_product_links(_fake_pool(conn), 3) == 2
    assert conn.execute.await_args.args[1] == 3


def test_link_ownership_is_keyed_on_the_stripe_id() -> None:
    """The primary key is what makes ownership one server per link."""
    assert "plink_id VARCHAR(64) PRIMARY KEY" in models.CREATE_STRIPE_PAYMENT_LINKS
    assert models.ALL_TABLES[0] == "stripe_payment_links"  # dropped before products


# --- scanning a product ------------------------------------------------------------------


PRODUCT = {"id": 3, "payment_link": PAGE}


@pytest.mark.asyncio
async def test_scan_claims_ours_and_reports_others() -> None:
    index = {LINK_A: "plink_a", LINK_B: "plink_b"}
    owners = {"plink_a": 1, "plink_b": 2}

    async def claim(pool, guild_id, product_id, plink_id, url):
        return owners[plink_id]

    with patch.object(payment_links, "find_stripe_links", new=AsyncMock(
        return_value=[LINK_A, LINK_B, "https://buy.stripe.com/elsewhere"]
    )), patch.object(payment_links.stripe_api, "list_payment_links", new=AsyncMock(return_value=index)), \
         patch.object(payment_links, "claim_link", new=claim):
        result = await payment_links.scan_product(MagicMock(), 1, PRODUCT)

    assert result.found == 3
    assert result.claimed == [LINK_A]
    assert result.owned_elsewhere == [LINK_B]
    assert result.not_on_our_account == 1
    assert result.error is None


@pytest.mark.asyncio
async def test_scan_reports_an_unreadable_page_instead_of_raising() -> None:
    with patch.object(payment_links, "find_stripe_links", new=AsyncMock(
        side_effect=payment_links.LinkScanError("The page returned HTTP 500.")
    )):
        result = await payment_links.scan_product(MagicMock(), 1, PRODUCT)
    assert "500" in result.error


@pytest.mark.asyncio
async def test_scan_without_stripe_says_so() -> None:
    with patch.object(payment_links, "find_stripe_links", new=AsyncMock(return_value=[LINK_A])), \
         patch.object(payment_links.stripe_api, "list_payment_links", new=AsyncMock(
             side_effect=stripe_api.StripeNotConfiguredError()
         )):
        result = await payment_links.scan_product(MagicMock(), 1, PRODUCT)
    assert "isn't configured" in result.error


@pytest.mark.asyncio
async def test_the_payment_link_index_is_cached() -> None:
    listing = AsyncMock(return_value={LINK_A: "plink_a"})
    with patch.object(payment_links.stripe_api, "list_payment_links", new=listing):
        await payment_links._payment_link_index()
        await payment_links._payment_link_index()
    listing.assert_awaited_once()


# --- Confirm's list ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_guild_checkouts_list_only_that_servers_links() -> None:
    checkouts = AsyncMock(return_value=["a payment"])
    with patch.object(payment_links.stripe_api, "is_configured", return_value=True), \
         patch.object(payment_links, "refresh_guild_links", new=AsyncMock()) as refresh, \
         patch.object(payment_links, "owned_links", new=AsyncMock(return_value={"plink_a": 3})), \
         patch.object(payment_links.stripe_api, "list_checkouts", new=checkouts):
        result = await payment_links.list_guild_checkouts(MagicMock(), 1)

    refresh.assert_awaited_once()  # pages re-scanned first, so new links count
    assert list(checkouts.await_args.args[0]) == ["plink_a"]
    assert result == ["a payment"]


@pytest.mark.asyncio
async def test_a_server_owning_no_links_never_queries_stripe() -> None:
    checkouts = AsyncMock()
    with patch.object(payment_links.stripe_api, "is_configured", return_value=True), \
         patch.object(payment_links, "refresh_guild_links", new=AsyncMock()), \
         patch.object(payment_links, "owned_links", new=AsyncMock(return_value={})), \
         patch.object(payment_links.stripe_api, "list_checkouts", new=checkouts):
        assert await payment_links.list_guild_checkouts(MagicMock(), 1) == []
    checkouts.assert_not_awaited()


@pytest.mark.asyncio
async def test_guild_checkouts_without_stripe_raise_not_configured() -> None:
    with patch.object(payment_links.stripe_api, "is_configured", return_value=False), \
         patch.object(payment_links, "refresh_guild_links", new=AsyncMock()) as refresh:
        with pytest.raises(stripe_api.StripeNotConfiguredError):
            await payment_links.list_guild_checkouts(MagicMock(), 1)
    refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_refresh_scans_every_product_and_survives_failures() -> None:
    rows = [{"id": 3, "payment_link": PAGE}, {"id": 4, "payment_link": PAGE}]
    scan = AsyncMock(side_effect=[
        payment_links.ScanResult(error="boom"), payment_links.ScanResult(found=1, claimed=[LINK_A])
    ])
    with patch.object(payment_links.products, "list_products", new=AsyncMock(return_value=rows)), \
         patch.object(payment_links, "scan_product", new=scan):
        await payment_links.refresh_guild_links(MagicMock(), 1)
    assert scan.await_count == 2
