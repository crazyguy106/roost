"""Tests for the FA-edition outbound routing: Roost's WhatsApp service
delegates to Chatwoot when CHATWOOT_ENABLED.

Two layers:

1. `chatwoot.route_text_to_whatsapp` — exercised with monkeypatched
   `find_or_create_contact` / `_find_open_conversation` /
   `create_conversation` / `send_message` so we cover the three branches
   (open-conv reuse, new-conv create, contact-create failure) without
   hitting the network.

2. `whatsapp.send_text_message` / `send_template_message` / `send_document`
   / `mark_as_read` — confirm that flipping `CHATWOOT_ENABLED` redirects
   text to Chatwoot, refuses templates/media with a clear error, and
   no-ops mark_as_read. The CHATWOOT_ENABLED=false path is already covered
   by existing tests; we only assert the FA-edition behaviour here.
"""
from __future__ import annotations

import pytest


# ──────────────────────── route_text_to_whatsapp ──────────────────────────


@pytest.fixture
def cw_enabled(monkeypatch):
    """Pretend Chatwoot is configured. The route fn checks the imported
    symbol — patching at the service-module path is sufficient."""
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.CHATWOOT_ENABLED",
        True,
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.CHATWOOT_INBOX_ID",
        "1",
    )


def test_route_text_reuses_open_conversation(cw_enabled, monkeypatch):
    """Most common path: contact exists, conversation is open — post into it."""
    import roost.extras.messaging_external.services.chatwoot as cw

    monkeypatch.setattr(cw, "find_or_create_contact", lambda **kw: {
        "ok": True, "contact_id": 7, "source_id": "+6591234567", "created": False,
    })
    monkeypatch.setattr(cw, "_find_open_conversation", lambda cid, inbox_id=None: 42)
    sends: list[tuple[int, str]] = []
    monkeypatch.setattr(cw, "send_message", lambda cid, content, **kw: (
        sends.append((cid, content)) or {"ok": True, "message_id": 99}
    ))
    monkeypatch.setattr(cw, "create_conversation", lambda **kw: pytest.fail(
        "should not create when an open conv exists"
    ))

    result = cw.route_text_to_whatsapp("+6591234567", "hello")
    assert result == {
        "ok": True,
        "conversation_id": 42,
        "message_id": 99,
        "created_conversation": False,
    }
    assert sends == [(42, "hello")]


def test_route_text_creates_conversation_when_no_open(cw_enabled, monkeypatch):
    """Cold-start: no open conversation → open a new one with initial_message."""
    import roost.extras.messaging_external.services.chatwoot as cw

    monkeypatch.setattr(cw, "find_or_create_contact", lambda **kw: {
        "ok": True, "contact_id": 7, "source_id": "+6591234567", "created": True,
    })
    monkeypatch.setattr(cw, "_find_open_conversation", lambda cid, inbox_id=None: None)
    monkeypatch.setattr(cw, "send_message", lambda *a, **kw: pytest.fail(
        "should not send into nothing; create_conversation handles the initial message"
    ))
    creates: list[dict] = []
    monkeypatch.setattr(cw, "create_conversation", lambda **kw: (
        creates.append(kw) or {"ok": True, "conversation_id": 88}
    ))

    result = cw.route_text_to_whatsapp("+6591234567", "first ping")
    assert result["ok"] is True
    assert result["conversation_id"] == 88
    assert result["created_conversation"] is True
    assert creates == [{
        "source_id": "+6591234567",
        "contact_id": 7,
        "initial_message": "first ping",
    }]


def test_route_text_propagates_contact_failure(cw_enabled, monkeypatch):
    """If we can't get a contact, surface the error — don't try to send."""
    import roost.extras.messaging_external.services.chatwoot as cw

    monkeypatch.setattr(cw, "find_or_create_contact",
                        lambda **kw: {"error": "Chatwoot API 422",
                                      "details": {"message": "invalid phone"}})
    monkeypatch.setattr(cw, "_find_open_conversation", lambda *a, **kw: pytest.fail())
    monkeypatch.setattr(cw, "send_message", lambda *a, **kw: pytest.fail())
    monkeypatch.setattr(cw, "create_conversation", lambda *a, **kw: pytest.fail())

    result = cw.route_text_to_whatsapp("not-a-phone", "hi")
    assert "error" in result
    assert result["error"] == "Chatwoot API 422"


def test_route_text_refuses_when_disabled(monkeypatch):
    """Failsafe: with CHATWOOT_ENABLED=false the router returns an explicit
    error so a misconfigured caller doesn't silently hit None paths."""
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.CHATWOOT_ENABLED",
        False,
    )
    import roost.extras.messaging_external.services.chatwoot as cw
    result = cw.route_text_to_whatsapp("+6591234567", "x")
    assert "error" in result


# ──────────────────────── whatsapp.py redirect ─────────────────────────────


def test_whatsapp_text_routes_to_chatwoot_when_enabled(monkeypatch):
    """send_text_message should delegate to chatwoot.route_text_to_whatsapp."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    captured: dict = {}

    def fake_route(phone, body, **kw):
        captured["phone"] = phone
        captured["body"] = body
        return {"ok": True, "conversation_id": 42, "message_id": 99,
                "created_conversation": False}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.route_text_to_whatsapp",
        fake_route,
    )

    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_text_message("+6591234567", "hello")
    assert result == {
        "ok": True,
        "message_id": 99,
        "via": "chatwoot",
        "conversation_id": 42,
    }
    assert captured == {"phone": "+6591234567", "body": "hello"}


def test_whatsapp_template_errors_in_fa_edition(monkeypatch):
    """Templates have no Chatwoot equivalent — surface a clear error."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_template_message("+6591234567", "welcome")
    assert "error" in result
    assert "Chatwoot" in result["error"]


def test_whatsapp_document_errors_in_fa_edition(monkeypatch):
    """Media via Chatwoot is FA-B v2 — surface a clear error for now."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_document("+6591234567", link="https://example.com/a.pdf")
    assert "error" in result
    assert "Chatwoot" in result["error"]


def test_whatsapp_image_errors_in_fa_edition(monkeypatch):
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_image("+6591234567", link="https://example.com/a.jpg")
    assert "error" in result
    assert "Chatwoot" in result["error"]


def test_whatsapp_mark_as_read_is_noop_in_fa_edition(monkeypatch):
    """Chatwoot owns read receipts on its inbox — mark_as_read becomes a no-op."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.mark_as_read("wamid.fake")
    assert result == {"ok": True, "via": "chatwoot", "no_op": True}
