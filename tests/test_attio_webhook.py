"""Tests for the Attio webhook HTTP endpoint.

Covers signature verification, payload-shape extraction, and the routing
into cadence stage handlers. The internal handler logic itself is covered
in test_cadences.py — here we just exercise the FastAPI/HTTP boundary.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient


SECRET = "test_attio_secret_value"


@pytest.fixture
def client(monkeypatch):
    """Build a FastAPI app with the webhook router and a known secret."""
    from fastapi import FastAPI
    # Patch the secret BEFORE importing the router so the module-level read sees it
    import roost.config as cfg
    monkeypatch.setattr(cfg, "ATTIO_WEBHOOK_SECRET", SECRET)

    # Re-import module so its top-level `from roost.config import …` rebinds
    import importlib
    import roost.extras.crm.web.api_attio_webhook as wh
    importlib.reload(wh)

    app = FastAPI()
    app.include_router(wh.router)
    return TestClient(app)


@pytest.fixture
def clean_cadence_tables():
    from roost.database import get_connection
    tables = ("nurture_enrollments", "cadence_preapprovals", "nurture_cadences")
    conn = get_connection()
    try:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(
        SECRET.encode(), body, hashlib.sha256
    ).hexdigest()


def _stage_event(deal_id: str, stage: str) -> dict:
    return {
        "events": [{
            "event_type": "record.updated",
            "target_object": "deals",
            "target_record": {
                "record_id": deal_id,
                "values": {"stage": [{"status": stage}]},
            },
        }]
    }


# ── Signature handling ────────────────────────────────────────────────


def test_missing_signature_returns_403(client):
    body = json.dumps(_stage_event("d1", "Won")).encode()
    r = client.post(
        "/api/attio/webhook", content=body,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 403


def test_bad_signature_returns_403(client):
    body = json.dumps(_stage_event("d1", "Won")).encode()
    r = client.post(
        "/api/attio/webhook", content=body,
        headers={
            "content-type": "application/json",
            "X-Attio-Signature": "sha256=deadbeef",
        },
    )
    assert r.status_code == 403


def test_malformed_json_returns_400(client):
    body = b"{not json"
    r = client.post(
        "/api/attio/webhook", content=body,
        headers={
            "content-type": "application/json",
            "X-Attio-Signature": _sign(body),
        },
    )
    assert r.status_code == 400


def test_missing_secret_returns_404(monkeypatch):
    """When ATTIO_WEBHOOK_SECRET is empty, the endpoint refuses to serve."""
    from fastapi import FastAPI
    import roost.config as cfg
    monkeypatch.setattr(cfg, "ATTIO_WEBHOOK_SECRET", "")

    import importlib
    import roost.extras.crm.web.api_attio_webhook as wh
    importlib.reload(wh)

    app = FastAPI()
    app.include_router(wh.router)
    c = TestClient(app)
    r = c.post("/api/attio/webhook", json={"events": []})
    assert r.status_code == 404


# ── Stage routing ─────────────────────────────────────────────────────


def test_won_stage_exits_enrollment(client, clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, get_enrollment,
    )
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="webhook@example.com",
        crm_deal_id="deal_won_1",
    )
    body = json.dumps(_stage_event("deal_won_1", "Closed Won")).encode()
    r = client.post(
        "/api/attio/webhook", content=body,
        headers={
            "content-type": "application/json",
            "X-Attio-Signature": _sign(body),
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["applied"] == 1
    assert data["actions"][0]["action"] == "exited"
    assert get_enrollment(e["id"])["status"] == "exited"


def test_unknown_stage_is_noop(client, clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, get_enrollment,
    )
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="noop@example.com",
        crm_deal_id="deal_noop",
    )
    body = json.dumps(_stage_event("deal_noop", "Some New Stage")).encode()
    r = client.post(
        "/api/attio/webhook", content=body,
        headers={
            "content-type": "application/json",
            "X-Attio-Signature": _sign(body),
        },
    )
    assert r.status_code == 200
    assert r.json()["applied"] == 0
    assert get_enrollment(e["id"])["status"] == "active"


def test_actions_diff_payload_shape(client, clean_cadence_tables):
    """Some Attio events carry the new stage value under actions[].new_value
    rather than target_record.values.stage. Both shapes must work."""
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, get_enrollment,
    )
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="diff@example.com",
        crm_deal_id="deal_diff",
    )
    payload = {
        "events": [{
            "target_object": "deals",
            "target_record": {"record_id": "deal_diff"},
            "actions": [
                {"attribute": "stage", "new_value": {"status": "Lost"}},
            ],
        }]
    }
    body = json.dumps(payload).encode()
    r = client.post(
        "/api/attio/webhook", content=body,
        headers={
            "content-type": "application/json",
            "X-Attio-Signature": _sign(body),
        },
    )
    assert r.status_code == 200
    assert r.json()["applied"] == 1
    assert get_enrollment(e["id"])["status"] == "exited"
