"""Route-level tests for the web/ FastAPI app (Stripe + DB fully mocked)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from stripe._stripe_object import StripeObject

from web.app import app


def _stripe_object(data: dict) -> StripeObject:
    """Build a real stripe.StripeObject from plain data for event fixtures.

    Stripe's SDK returns StripeObject instances (e.g. Session), not plain
    dicts, for event["data"]["object"] — they support subscript access but
    NOT .get() (StripeObject.__getattr__ intercepts "get" and raises
    AttributeError instead of dict.get's normal behavior). A plain-dict
    fixture here would silently hide any webhook.py code that calls .get()
    on the wrong thing — this bit us for real in production testing, so
    fixtures must use real StripeObjects to catch that class of bug.
    """
    return StripeObject.construct_from(data, "sk_test_stub")


@pytest.fixture
def client() -> TestClient:
    # The app's lifespan opens a real asyncpg pool via get_pool(); route tests
    # never need a live DB since every DB-touching call is patched per-test, so
    # bypass the lifespan by constructing the TestClient without triggering it
    # for GET-only tests, and patching get_pool for the lifespan-dependent ones.
    with patch("web.app.get_pool", new=AsyncMock(return_value=object())), \
         patch("web.app.close_pool", new=AsyncMock()):
        with TestClient(app) as c:
            yield c


def test_pricing_page_renders_configured_tiers(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "L2/L3" in response.text
    assert "L1" in response.text
    assert "$20/month" in response.text
    assert "$30/month" in response.text


def test_success_and_cancel_pages_render(client: TestClient) -> None:
    assert client.get("/success").status_code == 200
    assert client.get("/cancel").status_code == 200


def test_checkout_unknown_tier_404s(client: TestClient) -> None:
    response = client.post("/checkout/not_a_real_tier", data={})
    assert response.status_code == 404


def test_checkout_valid_tier_redirects_to_stripe(client: TestClient) -> None:
    fake_session = type("FakeSession", (), {"url": "https://checkout.stripe.com/cs_test_abc"})()
    with patch(
        "web.routes.checkout.stripe_client.create_checkout_session",
        new=AsyncMock(return_value=fake_session),
    ) as mock_create:
        response = client.post(
            "/checkout/l1",
            data={"discord_username": "buyerdiscord"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "https://checkout.stripe.com/cs_test_abc"
    _, kwargs = mock_create.call_args
    assert kwargs["metadata"]["tier"] == "l1"
    assert kwargs["metadata"]["discord_username"] == "buyerdiscord"


def test_checkout_blank_discord_username_omitted_from_metadata(client: TestClient) -> None:
    fake_session = type("FakeSession", (), {"url": "https://checkout.stripe.com/cs_test_xyz"})()
    with patch(
        "web.routes.checkout.stripe_client.create_checkout_session",
        new=AsyncMock(return_value=fake_session),
    ) as mock_create:
        client.post("/checkout/l2_l3", data={"discord_username": "   "}, follow_redirects=False)

    _, kwargs = mock_create.call_args
    assert "discord_username" not in kwargs["metadata"]


def _webhook_event(event_type: str, data_object: dict):
    return {"type": event_type, "data": {"object": _stripe_object(data_object)}}


def test_webhook_bad_signature_returns_400(client: TestClient) -> None:
    from web.stripe_client import WebhookSignatureError

    with patch(
        "web.routes.webhook.stripe_client.construct_webhook_event",
        new=AsyncMock(side_effect=WebhookSignatureError("bad sig")),
    ):
        response = client.post(
            "/webhook/stripe", content=b"{}", headers={"stripe-signature": "bad"}
        )
    assert response.status_code == 400


def test_webhook_checkout_completed_upserts(client: TestClient) -> None:
    event = _webhook_event(
        "checkout.session.completed",
        {
            "id": "cs_123",
            "customer": "cus_123",
            "subscription": None,
            "payment_status": "paid",
            "metadata": {"tier": "l1", "discord_username": "buyerdiscord"},
            "customer_details": {"email": "buyer@example.com"},
        },
    )
    with patch(
        "web.routes.webhook.stripe_client.construct_webhook_event", new=AsyncMock(return_value=event)
    ), patch(
        "web.routes.webhook.storage.upsert_from_checkout", new=AsyncMock(return_value={"id": 1})
    ) as mock_upsert:
        response = client.post(
            "/webhook/stripe", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
        )

    assert response.status_code == 200
    _, kwargs = mock_upsert.call_args
    assert kwargs["stripe_customer_id"] == "cus_123"
    assert kwargs["stripe_checkout_session_id"] == "cs_123"
    assert kwargs["tier"] == "l1"
    assert kwargs["email"] == "buyer@example.com"
    assert kwargs["discord_username_hint"] == "buyerdiscord"
    assert kwargs["status"] == "paid"
    assert "stripe_subscription_id" not in kwargs


def test_webhook_checkout_completed_idempotent_replay(client: TestClient) -> None:
    """Posting the same checkout.session.completed payload twice calls the
    upsert twice with the same identifying kwargs — no error, no duplicate
    row (the actual dedup happens in storage's ON CONFLICT, tested separately;
    here we confirm the route doesn't choke on a replay)."""
    event = _webhook_event(
        "checkout.session.completed",
        {
            "id": "cs_123",
            "customer": "cus_123",
            "subscription": None,
            "payment_status": "paid",
            "metadata": {"tier": "l1"},
            "customer_details": {"email": "buyer@example.com"},
        },
    )
    with patch(
        "web.routes.webhook.stripe_client.construct_webhook_event", new=AsyncMock(return_value=event)
    ), patch(
        "web.routes.webhook.storage.upsert_from_checkout", new=AsyncMock(return_value={"id": 1})
    ) as mock_upsert:
        first = client.post("/webhook/stripe", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"})
        second = client.post("/webhook/stripe", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert mock_upsert.await_count == 2


def test_webhook_unhandled_event_type_returns_200(client: TestClient) -> None:
    event = _webhook_event("invoice.paid", {"id": "in_123"})
    with patch(
        "web.routes.webhook.stripe_client.construct_webhook_event", new=AsyncMock(return_value=event)
    ):
        response = client.post(
            "/webhook/stripe", content=b"{}", headers={"stripe-signature": "t=1,v1=abc"}
        )
    assert response.status_code == 200
