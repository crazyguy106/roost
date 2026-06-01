"""Tests for the Chatwoot adapter.

Three layers:

1. `parse_webhook_event` — drive every captured 4.14.1 sample through the
   parser and assert the normalised shape (event, message_type, source_id,
   contact, etc.). Locks behaviour against the real payloads in
   `docs/chatwoot-webhook-samples/`.
2. `verify_webhook_signature` — synthetic round-trip with the documented
   HMAC algorithm + replay-window + malformed-input rejections.
3. End-to-end webhook hit — patches the service stubs and the AI pipeline
   helpers to confirm:
     - bad signature → 401
     - non-message events (conversation_updated, etc.) → 200 + ignored
     - message_created + incoming → lead ingest fires
"""
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

SAMPLES = pathlib.Path("docs/chatwoot-webhook-samples")


def _load(name: str) -> dict:
    return json.loads((SAMPLES / name).read_text())


# ───────────────────────── parse_webhook_event ────────────────────────────


def test_parse_incoming_message():
    from roost.extras.messaging_external.services.chatwoot import parse_webhook_event
    p = parse_webhook_event(_load("03_message_created_incoming.json"))
    assert p["event"] == "message_created"
    assert p["message_type"] == "incoming"
    assert p["content"] == "hi i am interested in the property"
    assert p["channel"] == "Channel::Api"
    assert p["source_id"] == "2e845ec2-b1fe-448e-86d0-4649505500e5"
    assert p["conversation_id"] == 1
    assert p["account_id"] == 4
    assert p["contact"] == {
        "id": 1,
        "name": "Roost Test",
        "phone": "+6591234567",
        "email": "contact@example.com",
    }


def test_parse_outgoing_message():
    """Outgoing — sender is the agent, contact pulled from conversation.meta.sender."""
    from roost.extras.messaging_external.services.chatwoot import parse_webhook_event
    p = parse_webhook_event(_load("04_message_created_outgoing.json"))
    assert p["event"] == "message_created"
    assert p["message_type"] == "outgoing"
    # The contact projection must still find the customer (not the agent
    # sending the reply) — pulled from conversation.meta.sender.
    assert p["contact"]["phone"] == "+6591234567"


def test_parse_conversation_updated_has_no_message_type():
    """conversation_updated shares envelope but has no top-level message_type."""
    from roost.extras.messaging_external.services.chatwoot import parse_webhook_event
    p = parse_webhook_event(_load("06_conversation_updated.json"))
    assert p["event"] == "conversation_updated"
    assert p["message_type"] == ""
    assert p["conversation_id"] == 1
    assert p["source_id"] == "2e845ec2-b1fe-448e-86d0-4649505500e5"


def test_parse_contact_created_no_account_top_level():
    """contact_created has `account` at top level (envelope quirk #7)."""
    from roost.extras.messaging_external.services.chatwoot import parse_webhook_event
    p = parse_webhook_event(_load("01_contact_created.json"))
    assert p["event"] == "contact_created"
    assert p["contact"]["phone"] == "+6591234567"
    assert p["account_id"] == 4


# ───────────────────────── verify_webhook_signature ────────────────────────


@pytest.fixture
def cw_signed(monkeypatch):
    """Provide a fresh (secret, ts, body, sig) tuple with the env loaded so
    verify_webhook_signature reads the same secret we sign with."""
    secret = "test-secret-abc123"
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.CHATWOOT_WEBHOOK_SECRET",
        secret,
    )
    body = b'{"event":"message_created","id":99}'
    ts = str(int(time.time()))
    digest = hmac.new(
        secret.encode(), f"{ts}.{body.decode()}".encode(), hashlib.sha256,
    ).hexdigest()
    return ts, body, f"sha256={digest}", secret


def test_verify_accepts_valid_signature(cw_signed):
    from roost.extras.messaging_external.services.chatwoot import verify_webhook_signature
    ts, body, sig, _ = cw_signed
    assert verify_webhook_signature(ts, body, sig) is True


def test_verify_rejects_wrong_signature(cw_signed):
    from roost.extras.messaging_external.services.chatwoot import verify_webhook_signature
    ts, body, _, _ = cw_signed
    assert verify_webhook_signature(ts, body, "sha256=" + "f" * 64) is False


def test_verify_rejects_missing_prefix(cw_signed):
    from roost.extras.messaging_external.services.chatwoot import verify_webhook_signature
    ts, body, sig, _ = cw_signed
    assert verify_webhook_signature(ts, body, sig.removeprefix("sha256=")) is False


def test_verify_rejects_old_timestamp(cw_signed):
    from roost.extras.messaging_external.services.chatwoot import verify_webhook_signature
    _, body, sig, _ = cw_signed
    old_ts = str(int(time.time()) - 400)  # outside the 300s replay window
    assert verify_webhook_signature(old_ts, body, sig) is False


def test_verify_rejects_garbage_timestamp(cw_signed):
    from roost.extras.messaging_external.services.chatwoot import verify_webhook_signature
    _, body, sig, _ = cw_signed
    assert verify_webhook_signature("not-a-number", body, sig) is False


def test_verify_rejects_when_secret_unset(monkeypatch):
    """If CHATWOOT_WEBHOOK_SECRET is empty we must fail closed."""
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.CHATWOOT_WEBHOOK_SECRET",
        "",
    )
    from roost.extras.messaging_external.services.chatwoot import verify_webhook_signature
    ts = str(int(time.time()))
    assert verify_webhook_signature(ts, b'{"x":1}', "sha256=abc") is False


# ───────────────────────── end-to-end webhook hit ──────────────────────────


@pytest.fixture
def client(monkeypatch):
    """Mount only the Chatwoot router on a bare app; stub the AI pipeline so
    we can assert routing decisions without invoking real recipes/Telegram."""
    import roost.config as cfg
    monkeypatch.setattr(cfg, "CHATWOOT_ENABLED", True)

    import importlib
    import roost.extras.messaging_external.web.api_chatwoot as cwapi
    importlib.reload(cwapi)

    # Bypass signature verification by stubbing the verify function the
    # router imports at call time.
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.verify_webhook_signature",
        lambda ts, body, sig: True,
    )

    # Capture lead ingest calls so we can assert the pipeline ran (or didn't).
    captured: list[dict] = []
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kwargs: captured.append(kwargs) or {"id": 1, "is_new": True},
    )
    # Stub out everything else the inbound processor touches.
    monkeypatch.setattr(
        "roost.services.recipes.list_recipes", lambda **kwargs: [],
    )

    async def _classify(message, sender):
        return {"intent": "info", "urgency": "warm"}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.ai_cdr.classify_message",
        _classify,
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.mark_as_read",
        lambda cid: {"ok": True},
    )
    # Run inbound inline rather than through the buffer for deterministic
    # assertions in tests.
    async def _inline_submit(channel, sender, message, processor):
        await processor(message)

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.inbound_buffer.submit",
        _inline_submit,
    )

    app = FastAPI()
    app.include_router(cwapi.router)
    return TestClient(app), captured


def test_post_rejects_bad_signature(monkeypatch):
    """Bad signature → 401, not 500 (so Chatwoot retries sanely)."""
    import roost.config as cfg
    monkeypatch.setattr(cfg, "CHATWOOT_ENABLED", True)

    import importlib
    import roost.extras.messaging_external.web.api_chatwoot as cwapi
    importlib.reload(cwapi)

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.verify_webhook_signature",
        lambda ts, body, sig: False,
    )

    app = FastAPI()
    app.include_router(cwapi.router)
    c = TestClient(app)

    resp = c.post(
        "/api/chatwoot/webhook",
        content=b'{"event":"message_created"}',
        headers={
            "X-Chatwoot-Timestamp": "1",
            "X-Chatwoot-Signature": "sha256=bad",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401


def test_post_ignores_conversation_updated(client):
    """conversation_updated is chatty — must NOT run the pipeline."""
    c, captured = client
    body = json.dumps(_load("06_conversation_updated.json")).encode()
    resp = c.post(
        "/api/chatwoot/webhook",
        content=body,
        headers={
            "X-Chatwoot-Timestamp": str(int(time.time())),
            "X-Chatwoot-Signature": "sha256=stub",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "conversation_updated"
    assert captured == []


def test_post_ignores_outgoing_message(client):
    """Agent-sent reply also bypasses the pipeline — only incoming runs it."""
    c, captured = client
    body = json.dumps(_load("04_message_created_outgoing.json")).encode()
    resp = c.post(
        "/api/chatwoot/webhook",
        content=body,
        headers={
            "X-Chatwoot-Timestamp": str(int(time.time())),
            "X-Chatwoot-Signature": "sha256=stub",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ignored"] == "message_created"
    assert captured == []


def test_post_runs_pipeline_on_incoming_message(client):
    """The happy path — incoming customer message reaches lead ingest."""
    c, captured = client
    body = json.dumps(_load("03_message_created_incoming.json")).encode()
    resp = c.post(
        "/api/chatwoot/webhook",
        content=body,
        headers={
            "X-Chatwoot-Timestamp": str(int(time.time())),
            "X-Chatwoot-Signature": "sha256=stub",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert len(captured) == 1
    assert captured[0]["channel"] == "chatwoot"
    assert captured[0]["phone"] == "+6591234567"
    assert captured[0]["message_text"] == "hi i am interested in the property"
