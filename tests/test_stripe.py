"""Tests for the Stripe SME Ops adapter (Phase 1A — read-only) + webhook."""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import time

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── Service ───────────────────────────────────────────────────────────


def test_client_disabled_when_no_key():
    from roost.extras.sme_ops.services.stripe import StripeClient
    c = StripeClient(api_key="", enabled=True)
    assert c.is_configured() is False
    assert c.list_charges() == {"error": "stripe_disabled"}


def test_client_disabled_when_flag_off():
    from roost.extras.sme_ops.services.stripe import StripeClient
    c = StripeClient(api_key="sk_test_x", enabled=False)
    assert c.is_configured() is False


@respx.mock
def test_list_charges_normalizes():
    from roost.extras.sme_ops.services.stripe import StripeClient
    respx.get("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(200, json={
            "data": [{
                "id": "ch_1", "amount": 4999, "currency": "usd",
                "status": "succeeded", "paid": True,
                "customer": "cus_X", "created": 1700000000,
                "description": "Test charge",
            }],
        }),
    )
    c = StripeClient(api_key="sk_test_x", enabled=True)
    out = c.list_charges()
    assert isinstance(out, list)
    assert out[0]["amount"] == 49.99
    assert out[0]["currency"] == "USD"
    assert out[0]["customer_id"] == "cus_X"


@respx.mock
def test_create_refund_full():
    from roost.extras.sme_ops.services.stripe import StripeClient
    route = respx.post("https://api.stripe.com/v1/refunds").mock(
        return_value=httpx.Response(200, json={
            "id": "re_1", "charge": "ch_1", "amount": 4999,
            "currency": "usd", "status": "succeeded",
            "reason": None, "created": 1700000001,
        }),
    )
    c = StripeClient(api_key="sk_test_x", enabled=True)
    out = c.create_refund("ch_1")
    assert route.called
    body = route.calls.last.request.content.decode()
    assert "charge=ch_1" in body
    assert "amount=" not in body
    assert out["amount"] == 49.99
    assert out["status"] == "succeeded"


@respx.mock
def test_create_refund_partial_with_reason():
    from roost.extras.sme_ops.services.stripe import StripeClient
    route = respx.post("https://api.stripe.com/v1/refunds").mock(
        return_value=httpx.Response(200, json={
            "id": "re_2", "charge": "ch_2", "amount": 1000,
            "currency": "usd", "status": "succeeded",
            "reason": "requested_by_customer", "created": 1700000002,
        }),
    )
    c = StripeClient(api_key="sk_test_x", enabled=True)
    out = c.create_refund("ch_2", amount=10.00, reason="requested_by_customer")
    body = route.calls.last.request.content.decode()
    assert "charge=ch_2" in body
    assert "amount=1000" in body
    assert "reason=requested_by_customer" in body
    assert out["reason"] == "requested_by_customer"


def test_create_refund_requires_charge_id():
    from roost.extras.sme_ops.services.stripe import StripeClient
    c = StripeClient(api_key="sk_test_x", enabled=True)
    assert c.create_refund("") == {"error": "charge_id_required"}


@respx.mock
def test_create_payment_link():
    from roost.extras.sme_ops.services.stripe import StripeClient
    route = respx.post("https://api.stripe.com/v1/payment_links").mock(
        return_value=httpx.Response(200, json={
            "id": "plink_1", "url": "https://buy.stripe.com/test_xx",
            "active": True,
        }),
    )
    c = StripeClient(api_key="sk_test_x", enabled=True)
    out = c.create_payment_link(25.50, "USD", description="Test product")
    body = route.calls.last.request.content.decode()
    assert "unit_amount%5D=2550" in body
    assert "currency%5D=usd" in body
    assert out["url"].startswith("https://buy.stripe.com/")


@respx.mock
def test_list_charges_handles_http_error():
    from roost.extras.sme_ops.services.stripe import StripeClient
    respx.get("https://api.stripe.com/v1/charges").mock(
        return_value=httpx.Response(401, text="bad auth"),
    )
    c = StripeClient(api_key="sk_test_bad", enabled=True)
    out = c.list_charges()
    assert isinstance(out, dict)
    assert out["error"] == "stripe_http_401"


# ── Webhook ───────────────────────────────────────────────────────────


SECRET = "whsec_test_value"


def _build_client(monkeypatch, *, sme_enabled=True, stripe_enabled=True,
                  secret=SECRET):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", sme_enabled)
    monkeypatch.setattr(cfg, "STRIPE_ENABLED", stripe_enabled)
    monkeypatch.setattr(cfg, "STRIPE_WEBHOOK_SECRET", secret)
    import roost.extras.sme_ops.web.api_stripe as api
    importlib.reload(api)
    monkeypatch.setattr(api, "fire_event_sync", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _sign(secret: str, body: bytes, ts: int | None = None) -> tuple[str, int]:
    ts = ts or int(time.time())
    sig = hmac.new(
        secret.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={sig}", ts


@pytest.fixture
def clean_events_table():
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sme_ops_events")
        conn.commit()
    finally:
        conn.close()
    yield


def test_webhook_404_when_disabled(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch, stripe_enabled=False)
    r = client.post("/api/stripe/webhook", content=b"{}")
    assert r.status_code == 404


def test_webhook_503_when_no_secret(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch, secret="")
    r = client.post("/api/stripe/webhook", content=b"{}")
    assert r.status_code == 503


def test_webhook_rejects_missing_signature(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post("/api/stripe/webhook", content=b"{}")
    assert r.status_code == 401


def test_webhook_rejects_bad_signature(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/stripe/webhook",
        content=b'{"type":"x"}',
        headers={"Stripe-Signature": "t=1,v1=deadbeef"},
    )
    assert r.status_code == 401


def test_webhook_rejects_old_timestamp(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    body = b'{"type":"x"}'
    sig, _ = _sign(SECRET, body, ts=int(time.time()) - 600)
    r = client.post("/api/stripe/webhook", content=body,
                    headers={"Stripe-Signature": sig})
    assert r.status_code == 401


def test_webhook_happy_path_persists_event(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    body = json.dumps({
        "id": "evt_1", "type": "charge.succeeded",
        "data": {"object": {"id": "ch_1", "amount": 1000}},
    }).encode()
    sig, _ = _sign(SECRET, body)
    r = client.post("/api/stripe/webhook", content=body,
                    headers={"Stripe-Signature": sig})
    assert r.status_code == 200, r.text
    body_json = r.json()
    assert body_json["ok"] is True
    assert body_json["event"] == "charge.succeeded"

    from roost.database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT source, event FROM sme_ops_events WHERE id = ?",
            (body_json["event_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["source"] == "stripe"
    assert row["event"] == "charge.succeeded"
