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
    assert r.text == "12345"


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


# ── Helpers + STOP / HELP / mark_inbound parity ────────────────────────

_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [{"id": "biz_1", "changes": []}],
}
_HEADERS = {
    "content-type": "application/json",
    "X-Hub-Signature-256": "sha256=stub",
}


def _patch_inbound_text(monkeypatch, text: str, *, sender: str = "+6591234567"):
    """Override the per-test inbound text/sender for parse_webhook_entry."""
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.parse_webhook_entry",
        lambda entry: [{
            "sender": sender,
            "sender_name": "Test User",
            "message_id": "wamid.test",
            "text": text,
        }],
    )


def test_first_token_basics():
    from roost.extras.messaging_external.web.api_whatsapp import _first_token
    assert _first_token("STOP") == "STOP"
    assert _first_token("stop.") == "STOP"
    assert _first_token(" Stop! ") == "STOP"
    assert _first_token("HELP me please") == "HELP"
    assert _first_token("") == ""


def test_stop_keyword_exits_enrollments_and_replies(client, monkeypatch):
    """STOP must exit enrollments + send confirmation reply via WhatsApp.
    Must skip ingest and recipe pipeline."""
    _patch_inbound_text(monkeypatch, "STOP")

    called = {"exit": None, "reply": None, "ingest": 0, "mark": 0}

    def fake_exit(*, phone="", email="", telegram_chat_id="", reason=""):
        called["exit"] = {"phone": phone, "reason": reason}
        return 2

    def fake_send(*, to=None, body=None, **kw):
        # Tolerate positional or keyword call shape
        called["reply"] = {"to": to, "body": body}
        return {"messages": [{"id": "wamid.reply"}]}

    def fake_ingest(**kw):
        called["ingest"] += 1

    def fake_mark(**kw):
        called["mark"] += 1
        return 0

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        fake_exit,
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message",
        fake_send,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        fake_mark,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )
    # Force no recipes so the no-recipe branch can't sneak through.
    monkeypatch.setattr("roost.services.recipes.list_recipes", lambda **kw: [])

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200
    assert r.json()["processed"] == 1

    assert called["exit"] == {
        "phone": "+6591234567", "reason": "opted_out:whatsapp",
    }
    assert called["reply"]["to"] == "+6591234567"
    assert "unsubscribed" in called["reply"]["body"].lower()
    assert called["ingest"] == 0  # STOP must not enroll
    assert called["mark"] == 0    # STOP must not bump last_inbound


@pytest.mark.parametrize("body", [
    "stop.", " Stop! ", "UNSUBSCRIBE", "cancel", "Quit", "STOPALL", "END",
])
def test_stop_variants_all_fire(client, monkeypatch, body):
    """First-token-after-punctuation should normalise to STOP/STOPALL/etc."""
    _patch_inbound_text(monkeypatch, body)

    exits = []
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        lambda **kw: exits.append(kw.get("phone")) or 1,
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message",
        lambda **kw: {"messages": [{"id": "x"}]},
    )

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200, body
    assert exits == ["+6591234567"], f"variant {body!r} did not trigger exit"


def test_help_keyword_replies_without_exit_or_ingest(client, monkeypatch):
    _patch_inbound_text(monkeypatch, "HELP")

    called = {"exit": 0, "ingest": 0, "reply": None, "mark": 0}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        lambda **kw: called.__setitem__("exit", called["exit"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: called.__setitem__("mark", called["mark"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: called.__setitem__("ingest", called["ingest"] + 1),
    )

    def fake_send(*, to=None, body=None, **kw):
        called["reply"] = {"to": to, "body": body}
        return {"messages": [{"id": "x"}]}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message",
        fake_send,
    )
    monkeypatch.setattr("roost.services.recipes.list_recipes", lambda **kw: [])

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200

    assert called["exit"] == 0
    assert called["ingest"] == 0
    assert called["mark"] == 0
    assert called["reply"]["to"] == "+6591234567"
    assert "stop" in called["reply"]["body"].lower()
    assert "support" in called["reply"]["body"].lower()


def test_info_keyword_also_fires_help(client, monkeypatch):
    _patch_inbound_text(monkeypatch, "INFO")
    seen = {"reply": False}
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message",
        lambda **kw: seen.__setitem__("reply", True) or {"messages": [{"id": "x"}]},
    )
    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200
    assert seen["reply"] is True


def test_inbound_message_calls_mark_inbound_before_ingest(client, monkeypatch):
    """Regular inbound (non-STOP/HELP, no qualification) must call
    mark_inbound_for_contact AND ingest_lead in that order."""
    order: list = []

    def fake_mark(**kw):
        order.append(("mark", kw.get("phone")))
        return 1

    def fake_ingest(**kw):
        order.append(("ingest", kw.get("phone")))
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        fake_mark,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )
    monkeypatch.setattr("roost.services.recipes.list_recipes", lambda **kw: [])

    async def fake_classify(message, sender):
        return {"intent": "inquiry", "urgency": "warm"}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.ai_cdr.classify_message",
        fake_classify,
    )

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200

    # Order matters: mark_inbound first (so wait_for_reply gate sees it),
    # then ingest (which may schedule new messages).
    assert order == [("mark", "+6591234567"), ("ingest", "+6591234567")]


def test_stop_exit_failure_is_non_fatal(client, monkeypatch):
    """If exit_enrollments raises, we still 200, still send the reply."""
    _patch_inbound_text(monkeypatch, "STOP")

    def boom(**kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        boom,
    )

    sent = {"n": 0}
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message",
        lambda **kw: sent.__setitem__("n", sent["n"] + 1) or {"messages": [{"id": "x"}]},
    )

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200
    assert sent["n"] == 1  # confirmation reply still sent


def test_stop_reply_send_failure_is_non_fatal(client, monkeypatch):
    """If sending the STOP confirmation fails (user blocked the bot, etc.),
    we still 200 — must not retry-storm Meta."""
    _patch_inbound_text(monkeypatch, "STOP")

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        lambda **kw: 0,
    )

    def boom(**kw):
        raise RuntimeError("recipient blocked us")

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message", boom,
    )

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200


def test_qualification_handled_skips_mark_and_ingest(client, monkeypatch):
    """If qualification engine handles the message, no mark_inbound,
    no ingest. Qualification engine intercept must run before mark."""
    _patch_inbound_text(monkeypatch, "about 50 staff")

    called = {"mark": 0, "ingest": 0}
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        lambda **kw: {"handled": True, "done": False},
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: called.__setitem__("mark", called["mark"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: called.__setitem__("ingest", called["ingest"] + 1),
    )

    r = client.post("/api/whatsapp/webhook",
                    content=json.dumps(_PAYLOAD), headers=_HEADERS)
    assert r.status_code == 200
    assert called == {"mark": 0, "ingest": 0}
