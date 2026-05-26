"""End-to-end test for the WhatsApp webhook → leads.ingest_lead hook.

We mount only the WhatsApp router on a bare FastAPI app, patch the WhatsApp
service helpers (signature verify, parse, mark-as-read), patch the recipes
list to empty so the no-recipe branch runs, and patch `leads.ingest_lead`
to capture its call args. Verifies the ingest hook is exercised for new
inbound messages.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # Force the module to think WhatsApp is enabled, with a known verify token.
    import roost.config as cfg
    monkeypatch.setattr(cfg, "WHATSAPP_ENABLED", True)
    monkeypatch.setattr(cfg, "WHATSAPP_VERIFY_TOKEN", "verify-token")

    import importlib
    import roost.extras.messaging_external.web.api_whatsapp as wa
    importlib.reload(wa)

    # Stub the WhatsApp service helpers — the imports inside the route happen
    # at call time, so patching the source module is enough.
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.verify_webhook_signature",
        lambda body, sig: True,
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.parse_webhook_entry",
        lambda entry: [{
            "sender": "+6591234567",
            "sender_name": "Test User",
            "message_id": "wamid.abc",
            "text": "Hi, looking for a 3-bedroom condo in District 9",
        }],
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.mark_as_read", lambda mid: None,
    )

    app = FastAPI()
    app.include_router(wa.router)
    return TestClient(app)


def test_get_verify_handshake_succeeds(client):
    r = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-token",
            "hub.challenge": "12345",
        },
    )
    assert r.status_code == 200
    assert r.json() == 12345


def test_get_verify_wrong_token_403(client):
    r = client.get(
        "/api/whatsapp/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "1",
        },
    )
    assert r.status_code == 403


def test_inbound_message_calls_lead_ingest(client, monkeypatch):
    """Posting a Meta-shaped payload should invoke leads.ingest_lead with
    channel='whatsapp' and the sender's phone."""
    captured: dict = {}

    def fake_ingest(**kw):
        captured.update(kw)
        return {"ok": True, "crm_person_id": "p1", "enrollment_id": 7}

    monkeypatch.setattr("roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest)
    # Force the no-recipe branch
    monkeypatch.setattr(
        "roost.services.recipes.list_recipes", lambda **kw: [],
    )

    async def fake_classify(message, sender):
        return {"intent": "inquiry", "urgency": "warm"}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.ai_cdr.classify_message", fake_classify,
    )

    payload = {
        "object": "whatsapp_business_account",
        "entry": [{"id": "biz_1", "changes": []}],
    }
    r = client.post(
        "/api/whatsapp/webhook",
        content=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "X-Hub-Signature-256": "sha256=stub",
        },
    )
    assert r.status_code == 200
    assert r.json()["processed"] == 1
    assert captured["channel"] == "whatsapp"
    assert captured["phone"] == "+6591234567"
    assert captured["vertical"] == "property"


def test_non_whatsapp_object_acks_without_processing(client, monkeypatch):
    """Meta sends a test notification on subscribe — must ack 200, no ingest."""
    called: list = []
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: called.append(kw) or {"ok": True},
    )
    r = client.post(
        "/api/whatsapp/webhook",
        content=json.dumps({"object": "something_else"}),
        headers={
            "content-type": "application/json",
            "X-Hub-Signature-256": "sha256=stub",
        },
    )
    assert r.status_code == 200
    assert called == []
