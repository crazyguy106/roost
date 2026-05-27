"""Integration tests for the Twilio SMS inbound webhook.

Mount only the SMS router on a bare FastAPI app, force SMS_ENABLED on,
patch the signature verifier (default: True so we exercise the ingest
path), and capture `leads.ingest_lead` call args. A dedicated test pair
exercises the real signature verifier and the 403 branch.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SMS_ENABLED", True)

    import importlib
    import roost.extras.messaging_external.web.api_sms as sms_route
    importlib.reload(sms_route)

    # Default to a permissive verifier so the happy-path tests can focus
    # on ingest behaviour. The signature-verification tests below patch
    # this back to the real implementation.
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.verify_twilio_signature",
        lambda url, params, signature: True,
    )

    app = FastAPI()
    app.include_router(sms_route.router)
    return TestClient(app)


def test_disabled_returns_404(monkeypatch):
    import roost.config as cfg
    monkeypatch.setattr(cfg, "SMS_ENABLED", False)

    import importlib
    import roost.extras.messaging_external.web.api_sms as sms_route
    importlib.reload(sms_route)

    app = FastAPI()
    app.include_router(sms_route.router)
    c = TestClient(app)
    r = c.post(
        "/api/sms/webhook",
        data={"From": "+6591234567", "Body": "hi", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "x"},
    )
    assert r.status_code == 404


def test_invalid_signature_returns_403(client, monkeypatch):
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.verify_twilio_signature",
        lambda url, params, signature: False,
    )
    r = client.post(
        "/api/sms/webhook",
        data={"From": "+6591234567", "Body": "hi", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "wrong"},
    )
    assert r.status_code == 403


def test_inbound_message_calls_lead_ingest(client, monkeypatch):
    captured: dict = {}

    def fake_ingest(**kw):
        captured.update(kw)
        return {"ok": True, "crm_person_id": "p1"}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    r = client.post(
        "/api/sms/webhook",
        data={
            "From": "+6591234567",
            "Body": "I'd like to know more about your services",
            "MessageSid": "SMaaaaaa",
        },
        headers={"X-Twilio-Signature": "ok"},
    )
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert "<Response/>" in r.text

    assert captured["channel"] == "sms"
    assert captured["source"] == "sms_inbound"
    assert captured["phone"] == "+6591234567"
    assert "more about" in captured["message_text"]
    assert captured["qualifying_identifier"] == "+6591234567"


def test_inbound_with_missing_body_silently_acks(client, monkeypatch):
    """Twilio status-callbacks may hit the same URL with no Body — we should
    not 4xx, just return empty TwiML and skip lead ingest."""
    called = {"n": 0}

    def fake_ingest(**kw):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    r = client.post(
        "/api/sms/webhook",
        data={"From": "+6591234567", "MessageSid": "SMxx"},  # no Body
        headers={"X-Twilio-Signature": "ok"},
    )
    assert r.status_code == 200
    assert called["n"] == 0


def test_inbound_ingest_exception_does_not_500(client, monkeypatch):
    def boom(**kw):
        raise RuntimeError("DB closed")

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", boom,
    )

    r = client.post(
        "/api/sms/webhook",
        data={"From": "+6591234567", "Body": "hi", "MessageSid": "SM1"},
        headers={"X-Twilio-Signature": "ok"},
    )
    # We swallow the exception (non-fatal) and return empty TwiML so Twilio
    # doesn't retry-storm us. The exception is logged for ops.
    assert r.status_code == 200
    assert "<Response" in r.text


# ── verify_twilio_signature unit ──────────────────────────────────────


def test_verify_signature_happy_path(monkeypatch):
    """Signature must be base64(HMAC-SHA1(token, url + sorted-kv-concat))."""
    import base64
    import hashlib
    import hmac

    from roost.extras.messaging_external.services import sms as sms_mod
    monkeypatch.setattr(sms_mod, "TWILIO_AUTH_TOKEN", "tok_test")

    url = "https://example.com/api/sms/webhook"
    params = {"From": "+6591234567", "Body": "hi", "MessageSid": "SM1"}
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    expected = base64.b64encode(
        hmac.new(b"tok_test", data.encode(), hashlib.sha1).digest()
    ).decode()

    assert sms_mod.verify_twilio_signature(url, params, expected) is True


def test_verify_signature_rejects_mismatch(monkeypatch):
    from roost.extras.messaging_external.services import sms as sms_mod
    monkeypatch.setattr(sms_mod, "TWILIO_AUTH_TOKEN", "tok_test")
    assert sms_mod.verify_twilio_signature(
        "https://example.com/api/sms/webhook",
        {"Body": "hi"},
        "totally-wrong-signature",
    ) is False


def test_verify_signature_missing_token_fails_closed(monkeypatch):
    from roost.extras.messaging_external.services import sms as sms_mod
    monkeypatch.setattr(sms_mod, "TWILIO_AUTH_TOKEN", "")
    assert sms_mod.verify_twilio_signature(
        "https://example.com/api/sms/webhook",
        {"Body": "hi"},
        "any-signature",
    ) is False


# ── STOP / HELP keyword handling ──────────────────────────────────────


def test_stop_keyword_exits_enrollments_and_replies(client, monkeypatch):
    """STOP must mark enrollments opted-out and skip lead ingest."""
    called = {"exit": None, "ingest": 0}

    def fake_exit(*, phone="", email="", reason=""):
        called["exit"] = {"phone": phone, "email": email, "reason": reason}
        return 2

    def fake_ingest(**kw):
        called["ingest"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        fake_exit,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    r = client.post(
        "/api/sms/webhook",
        data={"From": "+6591234567", "Body": "STOP", "MessageSid": "SMstop"},
        headers={"X-Twilio-Signature": "ok"},
    )
    assert r.status_code == 200
    assert called["exit"] == {
        "phone": "+6591234567",
        "email": "",
        "reason": "opted_out:sms",
    }
    assert called["ingest"] == 0  # STOP must not enroll
    assert "unsubscribed" in r.text.lower()


def test_help_keyword_replies_with_help_text(client, monkeypatch):
    """HELP must return support info, not ingest, not exit."""
    called = {"exit": 0, "ingest": 0}

    def fake_exit(**kw):
        called["exit"] += 1
        return 0

    def fake_ingest(**kw):
        called["ingest"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        fake_exit,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    r = client.post(
        "/api/sms/webhook",
        data={"From": "+6591234567", "Body": "HELP", "MessageSid": "SMhelp"},
        headers={"X-Twilio-Signature": "ok"},
    )
    assert r.status_code == 200
    assert called["exit"] == 0
    assert called["ingest"] == 0
    assert "stop" in r.text.lower()
    assert "support" in r.text.lower()


def test_stop_keyword_lowercase_with_punctuation(client, monkeypatch):
    """'stop.' / ' Stop! ' must still trigger the STOP branch."""
    exits = []

    def fake_exit(*, phone="", email="", reason=""):
        exits.append(phone)
        return 1

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        fake_exit,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: {"ok": True},
    )

    for body in ("stop.", " Stop! ", "UNSUBSCRIBE", "cancel"):
        r = client.post(
            "/api/sms/webhook",
            data={"From": "+6591234567", "Body": body, "MessageSid": "SMx"},
            headers={"X-Twilio-Signature": "ok"},
        )
        assert r.status_code == 200, body

    assert len(exits) == 4
    assert all(p == "+6591234567" for p in exits)
