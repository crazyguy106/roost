"""Tests for the Shopify SME Ops adapter (Phase 1A) + webhook."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient


SECRET = "shopify_test_secret"


# ── Service ───────────────────────────────────────────────────────────


def test_client_disabled_when_creds_missing():
    from roost.extras.sme_ops.services.shopify import ShopifyClient
    c = ShopifyClient(store_domain="", access_token="", enabled=True)
    assert c.is_configured() is False
    assert c.list_orders() == {"error": "shopify_disabled"}


@respx.mock
def test_list_orders_normalizes():
    from roost.extras.sme_ops.services.shopify import ShopifyClient
    respx.get(
        "https://shop.myshopify.com/admin/api/2024-04/orders.json"
    ).mock(return_value=httpx.Response(200, json={
        "orders": [{
            "id": 1, "name": "#1001", "email": "x@y.com",
            "total_price": "49.90", "currency": "SGD",
            "financial_status": "paid", "fulfillment_status": None,
            "created_at": "2026-01-01T00:00:00Z",
            "line_items": [{"id": 1}, {"id": 2}],
        }],
    }))
    c = ShopifyClient(
        store_domain="shop.myshopify.com",
        access_token="shpat_x", enabled=True,
    )
    out = c.list_orders()
    assert isinstance(out, list)
    assert out[0]["total"] == 49.90
    assert out[0]["line_items"] == 2
    assert out[0]["financial_status"] == "paid"


@respx.mock
def test_list_products_inventory_total():
    from roost.extras.sme_ops.services.shopify import ShopifyClient
    respx.get(
        "https://shop.myshopify.com/admin/api/2024-04/products.json"
    ).mock(return_value=httpx.Response(200, json={
        "products": [{
            "id": 10, "title": "Widget", "status": "active",
            "vendor": "Acme",
            "variants": [
                {"inventory_quantity": 5},
                {"inventory_quantity": 3},
            ],
        }],
    }))
    c = ShopifyClient(
        store_domain="shop.myshopify.com",
        access_token="shpat_x", enabled=True,
    )
    out = c.list_products()
    assert out[0]["variants"] == 2
    assert out[0]["inventory_total"] == 8


@respx.mock
def test_fulfill_order():
    from roost.extras.sme_ops.services.shopify import ShopifyClient
    base = "https://shop.myshopify.com/admin/api/2024-04"
    respx.get(f"{base}/orders/123/fulfillment_orders.json").mock(
        return_value=httpx.Response(200, json={
            "fulfillment_orders": [{"id": 9001}, {"id": 9002}],
        }),
    )
    post_route = respx.post(f"{base}/fulfillments.json").mock(
        return_value=httpx.Response(200, json={
            "fulfillment": {
                "id": 555, "order_id": 123, "status": "success",
                "tracking_number": "TRACK1",
                "created_at": "2026-05-01T00:00:00Z",
            },
        }),
    )
    c = ShopifyClient(store_domain="shop.myshopify.com",
                      access_token="shpat_x", enabled=True)
    out = c.fulfill_order(123, tracking_number="TRACK1", tracking_company="DHL")
    assert post_route.called
    sent = json.loads(post_route.calls.last.request.content)
    items = sent["fulfillment"]["line_items_by_fulfillment_order"]
    assert {i["fulfillment_order_id"] for i in items} == {9001, 9002}
    assert sent["fulfillment"]["tracking_info"]["number"] == "TRACK1"
    assert out["id"] == 555
    assert out["tracking_number"] == "TRACK1"


@respx.mock
def test_fulfill_order_no_fulfillment_orders():
    from roost.extras.sme_ops.services.shopify import ShopifyClient
    base = "https://shop.myshopify.com/admin/api/2024-04"
    respx.get(f"{base}/orders/123/fulfillment_orders.json").mock(
        return_value=httpx.Response(200, json={"fulfillment_orders": []}),
    )
    c = ShopifyClient(store_domain="shop.myshopify.com",
                      access_token="shpat_x", enabled=True)
    assert c.fulfill_order(123) == {"error": "no_fulfillment_orders"}


@respx.mock
def test_cancel_order():
    from roost.extras.sme_ops.services.shopify import ShopifyClient
    base = "https://shop.myshopify.com/admin/api/2024-04"
    route = respx.post(f"{base}/orders/123/cancel.json").mock(
        return_value=httpx.Response(200, json={
            "order": {
                "id": 123, "name": "#1001",
                "cancelled_at": "2026-05-01T01:00:00Z",
                "cancel_reason": "customer",
                "financial_status": "refunded",
            },
        }),
    )
    c = ShopifyClient(store_domain="shop.myshopify.com",
                      access_token="shpat_x", enabled=True)
    out = c.cancel_order(123, reason="customer", refund=True)
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"reason": "customer", "refund": True}
    assert out["cancel_reason"] == "customer"
    assert out["financial_status"] == "refunded"


# ── Webhook ───────────────────────────────────────────────────────────


def _build_client(monkeypatch, *, sme_enabled=True, shopify_enabled=True,
                  secret=SECRET):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", sme_enabled)
    monkeypatch.setattr(cfg, "SHOPIFY_ENABLED", shopify_enabled)
    monkeypatch.setattr(cfg, "SHOPIFY_WEBHOOK_SECRET", secret)
    import roost.extras.sme_ops.web.api_shopify as api
    importlib.reload(api)
    monkeypatch.setattr(api, "fire_event_sync", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _hmac(body: bytes, secret: str = SECRET) -> str:
    return base64.b64encode(
        hmac.new(secret.encode(), body, hashlib.sha256).digest()
    ).decode()


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
    client = _build_client(monkeypatch, shopify_enabled=False)
    r = client.post("/api/shopify/webhook", content=b"{}")
    assert r.status_code == 404


def test_webhook_503_when_no_secret(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch, secret="")
    r = client.post("/api/shopify/webhook", content=b"{}")
    assert r.status_code == 503


def test_webhook_rejects_bad_hmac(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/shopify/webhook", content=b"{}",
        headers={"X-Shopify-Hmac-Sha256": "wrong",
                 "X-Shopify-Topic": "orders/create"},
    )
    assert r.status_code == 401


def test_webhook_happy_path(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    body = json.dumps({"id": 999, "name": "#9001"}).encode()
    r = client.post(
        "/api/shopify/webhook", content=body,
        headers={
            "X-Shopify-Hmac-Sha256": _hmac(body),
            "X-Shopify-Topic": "orders/create",
        },
    )
    assert r.status_code == 200, r.text
    body_json = r.json()
    assert body_json["ok"] is True
    assert body_json["topic"] == "orders/create"

    from roost.database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT source, event FROM sme_ops_events WHERE id = ?",
            (body_json["event_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["source"] == "shopify"
    assert row["event"] == "orders/create"
