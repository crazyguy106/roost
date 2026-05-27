"""Tests for the Zapier ingress webhook (SME Ops bundle).

Covers bearer-token auth, feature-flag gating, payload validation, and DB
persistence. SOP trigger dispatch is mocked — the trigger module has its
own tests.
"""

from __future__ import annotations

import importlib
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


TOKEN = "test-zapier-token-abc123"


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
    conn = get_connection()
    try:
        conn.execute("DELETE FROM sme_ops_events")
        conn.commit()
    finally:
        conn.close()


def _build_client(monkeypatch, *, enabled=True, token=TOKEN):
    """Rebuild the FastAPI app with patched config so the router sees the
    intended values at module-import time."""
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", enabled)
    monkeypatch.setattr(cfg, "ZAPIER_INGRESS_TOKEN", token)
    # Also no-op the SOP trigger dispatch so we don't kick off a thread.
    import roost.extras.sme_ops.web.api_zapier as api
    importlib.reload(api)
    monkeypatch.setattr(api, "fire_event_sync", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_rejects_missing_token(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post("/api/zapier/inbound",
                    json={"event": "x.y", "payload": {}})
    assert r.status_code == 401


def test_rejects_wrong_token(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post("/api/zapier/inbound",
                    json={"event": "x.y", "payload": {}},
                    headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_503_when_no_token_configured(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch, token="")
    r = client.post("/api/zapier/inbound",
                    json={"event": "x.y", "payload": {}},
                    headers={"Authorization": "Bearer anything"})
    assert r.status_code == 503


def test_404_when_bundle_disabled(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch, enabled=False)
    r = client.post("/api/zapier/inbound",
                    json={"event": "x.y", "payload": {}},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 404


def test_400_when_event_missing(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post("/api/zapier/inbound",
                    json={"payload": {"x": 1}},
                    headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 400


def test_happy_path_persists_event(monkeypatch, clean_events_table):
    client = _build_client(monkeypatch)
    r = client.post(
        "/api/zapier/inbound",
        json={"event": "order.created", "payload": {"id": 7, "total": 49.9}},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["event"] == "order.created"
    assert isinstance(body["event_id"], int)

    from roost.database import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT source, event, payload_json FROM sme_ops_events WHERE id = ?",
            (body["event_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row["source"] == "zapier"
    assert row["event"] == "order.created"
    payload = json.loads(row["payload_json"])
    assert payload == {"id": 7, "total": 49.9}


def test_fires_sop_trigger_with_event_name(monkeypatch, clean_events_table):
    """The SOP trigger must receive `zapier_event` + the original name in data."""
    captured = {}

    def fake_fire(event_type, event_data=None, user_id=""):
        captured["event_type"] = event_type
        captured["data"] = event_data

    import roost.config as cfg
    monkeypatch.setattr(cfg, "SME_OPS_ENABLED", True)
    monkeypatch.setattr(cfg, "ZAPIER_INGRESS_TOKEN", TOKEN)
    import roost.extras.sme_ops.web.api_zapier as api
    importlib.reload(api)
    monkeypatch.setattr(api, "fire_event_sync", fake_fire)

    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)

    r = client.post(
        "/api/zapier/inbound",
        json={"event": "invoice.paid", "payload": {"id": 42}},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert r.status_code == 200
    assert captured["event_type"] == "zapier_event"
    assert captured["data"]["zapier_event_name"] == "invoice.paid"
    assert captured["data"]["id"] == 42
