"""Unit tests for web.stripe_client (mocked Stripe SDK calls, no real API)."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import stripe

from web import stripe_client


@pytest.mark.asyncio
async def test_create_checkout_session_passes_expected_kwargs() -> None:
    with patch.object(stripe.checkout.Session, "create", return_value={"id": "cs_123", "url": "https://checkout.stripe.com/cs_123"}) as mock_create:
        result = await stripe_client.create_checkout_session(
            price_id="price_abc",
            success_url="https://example.com/success",
            cancel_url="https://example.com/cancel",
            metadata={"tier": "l1"},
        )

    assert result["id"] == "cs_123"
    mock_create.assert_called_once_with(
        mode="payment",
        line_items=[{"price": "price_abc", "quantity": 1}],
        success_url="https://example.com/success",
        cancel_url="https://example.com/cancel",
        metadata={"tier": "l1"},
    )


@pytest.mark.asyncio
async def test_construct_webhook_event_success() -> None:
    fake_event = {"type": "checkout.session.completed", "data": {"object": {}}}
    with patch.object(stripe.Webhook, "construct_event", return_value=fake_event) as mock_construct:
        result = await stripe_client.construct_webhook_event(
            payload=b'{"type": "checkout.session.completed"}',
            sig_header="t=1,v1=abc",
        )

    assert result == fake_event
    mock_construct.assert_called_once()


@pytest.mark.asyncio
async def test_construct_webhook_event_bad_signature_raises_typed_error() -> None:
    with patch.object(
        stripe.Webhook,
        "construct_event",
        side_effect=stripe.error.SignatureVerificationError("bad sig", "sig_header"),
    ):
        with pytest.raises(stripe_client.WebhookSignatureError):
            await stripe_client.construct_webhook_event(payload=b"{}", sig_header="bad")


@pytest.mark.asyncio
async def test_construct_webhook_event_malformed_payload_raises_typed_error() -> None:
    with patch.object(stripe.Webhook, "construct_event", side_effect=ValueError("bad payload")):
        with pytest.raises(stripe_client.WebhookSignatureError):
            await stripe_client.construct_webhook_event(payload=b"not json", sig_header="t=1,v1=abc")


@pytest.mark.asyncio
async def test_retrieve_subscription() -> None:
    with patch.object(stripe.Subscription, "retrieve", return_value={"id": "sub_123", "status": "active"}) as mock_retrieve:
        result = await stripe_client.retrieve_subscription("sub_123")

    assert result["status"] == "active"
    mock_retrieve.assert_called_once_with("sub_123")
