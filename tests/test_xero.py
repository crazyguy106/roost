"""Tests for the Xero SME Ops adapter (Phase 1A read + 1B writes/OAuth)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_xero_tokens():
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM xero_oauth_tokens")
        conn.commit()
    finally:
        conn.close()
    yield


def test_disabled_when_no_pat():
    from roost.extras.sme_ops.services.xero import XeroClient
    c = XeroClient(pat="", tenant_id="tnt", enabled=True)
    assert c.is_configured() is False
    assert c.list_invoices() == {"error": "xero_disabled"}


def test_disabled_when_no_tenant():
    from roost.extras.sme_ops.services.xero import XeroClient
    c = XeroClient(pat="pat_x", tenant_id="", enabled=True)
    assert c.is_configured() is False


@respx.mock
def test_list_invoices_normalizes():
    from roost.extras.sme_ops.services.xero import XeroClient
    respx.get("https://api.xero.com/api.xro/2.0/Invoices").mock(
        return_value=httpx.Response(200, json={
            "Invoices": [{
                "InvoiceID": "abc-123", "InvoiceNumber": "INV-001",
                "Type": "ACCREC", "Status": "AUTHORISED",
                "Total": 1000.0, "AmountDue": 500.0, "AmountPaid": 500.0,
                "CurrencyCode": "SGD", "DueDateString": "2026-06-01",
                "Contact": {"ContactID": "cnt-1", "Name": "Acme Co"},
            }],
        }),
    )
    c = XeroClient(pat="pat_x", tenant_id="tnt-1", enabled=True)
    out = c.list_invoices()
    assert isinstance(out, list)
    assert out[0]["number"] == "INV-001"
    assert out[0]["amount_due"] == 500.0
    assert out[0]["contact_name"] == "Acme Co"


@respx.mock
def test_list_invoices_handles_403():
    from roost.extras.sme_ops.services.xero import XeroClient
    respx.get("https://api.xero.com/api.xro/2.0/Invoices").mock(
        return_value=httpx.Response(403, text="forbidden"),
    )
    c = XeroClient(pat="pat_x", tenant_id="tnt-1", enabled=True)
    out = c.list_invoices()
    assert isinstance(out, dict)
    assert out["error"] == "xero_http_403"


@respx.mock
def test_list_contacts_normalizes():
    from roost.extras.sme_ops.services.xero import XeroClient
    respx.get("https://api.xero.com/api.xro/2.0/Contacts").mock(
        return_value=httpx.Response(200, json={
            "Contacts": [{
                "ContactID": "c1", "Name": "Bob",
                "EmailAddress": "b@x.com",
                "IsCustomer": True, "IsSupplier": False,
            }],
        }),
    )
    c = XeroClient(pat="pat_x", tenant_id="tnt-1", enabled=True)
    out = c.list_contacts()
    assert out[0]["name"] == "Bob"
    assert out[0]["is_customer"] is True


@respx.mock
def test_limit_truncates_results():
    from roost.extras.sme_ops.services.xero import XeroClient
    respx.get("https://api.xero.com/api.xro/2.0/Invoices").mock(
        return_value=httpx.Response(200, json={
            "Invoices": [
                {"InvoiceID": str(i), "InvoiceNumber": f"INV-{i}",
                 "Contact": {}}
                for i in range(20)
            ],
        }),
    )
    c = XeroClient(pat="pat_x", tenant_id="tnt-1", enabled=True)
    out = c.list_invoices(limit=5)
    assert len(out) == 5


# ── OAuth helpers ─────────────────────────────────────────────────────


@respx.mock
def test_exchange_code():
    from roost.extras.sme_ops.services import xero_oauth
    respx.post("https://identity.xero.com/connect/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at1", "refresh_token": "rt1",
            "expires_in": 1800, "scope": "offline_access accounting.contacts",
        }),
    )
    out = xero_oauth.exchange_code("the_code")
    assert out["access_token"] == "at1"
    assert out["refresh_token"] == "rt1"


@respx.mock
def test_refresh_rotates_tokens():
    from roost.extras.sme_ops.services import xero_oauth
    respx.post("https://identity.xero.com/connect/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at2", "refresh_token": "rt2",
            "expires_in": 1800,
        }),
    )
    out = xero_oauth.refresh("rt1")
    assert out["access_token"] == "at2"
    assert out["refresh_token"] == "rt2"


def test_store_and_load_tokens():
    from roost.extras.sme_ops.services import xero_oauth
    xero_oauth.store_tokens("tnt-A", "at1", "rt1", 1800, "scope1")
    row = xero_oauth.load_tokens("tnt-A")
    assert row["access_token"] == "at1"
    assert row["refresh_token"] == "rt1"
    assert row["scope"] == "scope1"


@respx.mock
def test_access_token_for_refreshes_when_expired():
    from roost.extras.sme_ops.services import xero_oauth
    # Store an already-expired token.
    expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO xero_oauth_tokens (tenant_id, access_token,"
            " refresh_token, expires_at, scope) VALUES (?, ?, ?, ?, ?)",
            ("tnt-X", "old_at", "old_rt", expired, ""),
        )
        conn.commit()
    finally:
        conn.close()
    respx.post("https://identity.xero.com/connect/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "fresh_at", "refresh_token": "fresh_rt",
            "expires_in": 1800,
        }),
    )
    tok, tenant = xero_oauth.access_token_for("tnt-X")
    assert tok == "fresh_at"
    assert tenant == "tnt-X"


# ── create_invoice ────────────────────────────────────────────────────


@respx.mock
def test_create_invoice_via_pat():
    from roost.extras.sme_ops.services.xero import XeroClient
    route = respx.post("https://api.xero.com/api.xro/2.0/Invoices").mock(
        return_value=httpx.Response(200, json={
            "Invoices": [{
                "InvoiceID": "inv-1", "InvoiceNumber": "INV-001",
                "Type": "ACCREC", "Status": "DRAFT",
                "Total": 150.0, "AmountDue": 150.0, "AmountPaid": 0,
                "CurrencyCode": "SGD",
                "Contact": {"ContactID": "c1", "Name": "Acme"},
            }],
        }),
    )
    c = XeroClient(pat="pat_x", tenant_id="tnt-1", enabled=True)
    out = c.create_invoice(
        "c1",
        [{"description": "Widget", "quantity": 3, "unit_amount": 50.0,
          "account_code": "200"}],
        due_date="2026-06-01",
    )
    sent = json.loads(route.calls.last.request.content)
    inv = sent["Invoices"][0]
    assert inv["Contact"]["ContactID"] == "c1"
    assert inv["LineItems"][0]["Quantity"] == 3
    assert inv["LineItems"][0]["AccountCode"] == "200"
    assert inv["DueDate"] == "2026-06-01"
    assert out["number"] == "INV-001"


def test_create_invoice_validates_inputs():
    from roost.extras.sme_ops.services.xero import XeroClient
    c = XeroClient(pat="pat_x", tenant_id="tnt-1", enabled=True)
    assert c.create_invoice("", [{"x": 1}]) == {"error": "contact_id_required"}
    assert c.create_invoice("c1", []) == {"error": "line_items_required"}


# ── Webhook ───────────────────────────────────────────────────────────


WEBHOOK_KEY = "xero_test_webhook_key"


def _build_webhook_client(monkeypatch, *, sme=True, xero=True, key=WEBHOOK_KEY):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", sme)
    monkeypatch.setattr(cfg, "XERO_ENABLED", xero)
    monkeypatch.setattr(cfg, "XERO_WEBHOOK_KEY", key)
    import roost.extras.sme_ops.web.api_xero as api
    importlib.reload(api)
    monkeypatch.setattr(api, "fire_event_sync", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _xero_sig(secret: str, body: bytes) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


def test_xero_webhook_404_when_disabled(monkeypatch):
    client = _build_webhook_client(monkeypatch, xero=False)
    r = client.post("/api/xero/webhook", content=b"")
    assert r.status_code == 404


def test_xero_webhook_503_when_no_key(monkeypatch):
    client = _build_webhook_client(monkeypatch, key="")
    r = client.post("/api/xero/webhook", content=b"")
    assert r.status_code == 503


def test_xero_webhook_rejects_bad_signature(monkeypatch):
    client = _build_webhook_client(monkeypatch)
    r = client.post("/api/xero/webhook", content=b"{}",
                    headers={"x-xero-signature": "deadbeef"})
    assert r.status_code == 401


def test_xero_webhook_intent_to_receive(monkeypatch):
    """Empty body with valid signature → 200 (intent-to-receive)."""
    client = _build_webhook_client(monkeypatch)
    body = b""
    sig = _xero_sig(WEBHOOK_KEY, body)
    r = client.post("/api/xero/webhook", content=body,
                    headers={"x-xero-signature": sig})
    assert r.status_code == 200
    assert r.json()["events_processed"] == 0


def test_xero_webhook_happy_path(monkeypatch):
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sme_ops_events")
        conn.commit()
    finally:
        conn.close()

    client = _build_webhook_client(monkeypatch)
    body = json.dumps({
        "events": [{
            "eventCategory": "INVOICE",
            "eventType": "UPDATE",
            "resourceId": "abc-123",
            "resourceUrl": "https://api.xero.com/...",
            "tenantId": "tnt-1",
        }],
    }).encode()
    sig = _xero_sig(WEBHOOK_KEY, body)
    r = client.post("/api/xero/webhook", content=body,
                    headers={"x-xero-signature": sig})
    assert r.status_code == 200, r.text
    assert r.json()["events_processed"] == 1


# ── OAuth callback ────────────────────────────────────────────────────


def _build_oauth_client(monkeypatch, *, sme=True, xero=True,
                         client_id="cid", client_secret="csec",
                         redirect="https://example.com/cb"):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", sme)
    monkeypatch.setattr(cfg, "XERO_ENABLED", xero)
    monkeypatch.setattr(cfg, "XERO_CLIENT_ID", client_id)
    monkeypatch.setattr(cfg, "XERO_CLIENT_SECRET", client_secret)
    monkeypatch.setattr(cfg, "XERO_REDIRECT_URI", redirect)
    import roost.extras.sme_ops.services.xero_oauth as oauth_mod
    importlib.reload(oauth_mod)
    import roost.extras.sme_ops.web.api_xero as api
    importlib.reload(api)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_oauth_start_redirects(monkeypatch):
    client = _build_oauth_client(monkeypatch)
    r = client.get("/api/xero/oauth/start", follow_redirects=False)
    assert r.status_code == 302
    assert "login.xero.com" in r.headers["location"]


def test_oauth_start_503_when_unconfigured(monkeypatch):
    client = _build_oauth_client(monkeypatch, client_id="", redirect="")
    r = client.get("/api/xero/oauth/start", follow_redirects=False)
    assert r.status_code == 503


@respx.mock
def test_oauth_callback_stores_tokens(monkeypatch):
    client = _build_oauth_client(monkeypatch)
    respx.post("https://identity.xero.com/connect/token").mock(
        return_value=httpx.Response(200, json={
            "access_token": "at1", "refresh_token": "rt1",
            "expires_in": 1800, "scope": "offline_access",
        }),
    )
    respx.get("https://api.xero.com/connections").mock(
        return_value=httpx.Response(200, json=[
            {"tenantId": "tnt-A", "tenantName": "Acme Pte Ltd"},
        ]),
    )
    # Set the state cookie first.
    client.cookies.set("xero_oauth_state", "the_state")
    r = client.get("/api/xero/oauth/callback",
                   params={"code": "the_code", "state": "the_state"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["stored"][0]["tenant_id"] == "tnt-A"

    from roost.extras.sme_ops.services import xero_oauth
    row = xero_oauth.load_tokens("tnt-A")
    assert row["access_token"] == "at1"


def test_oauth_callback_rejects_bad_state(monkeypatch):
    client = _build_oauth_client(monkeypatch)
    client.cookies.set("xero_oauth_state", "good_state")
    r = client.get("/api/xero/oauth/callback",
                   params={"code": "x", "state": "bad_state"})
    assert r.status_code == 401
