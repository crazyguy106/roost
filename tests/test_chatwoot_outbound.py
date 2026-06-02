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
   text **and** templates **and** media (file path) through Chatwoot, and
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


def test_whatsapp_template_routes_to_chatwoot_when_enabled(monkeypatch):
    """send_template_message should delegate to chatwoot.route_template_to_whatsapp
    and translate Meta-style components into Chatwoot's processed_params."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    captured: dict = {}

    def fake_route(phone, template_name, **kw):
        captured["phone"] = phone
        captured["template_name"] = template_name
        captured["kwargs"] = kw
        return {"ok": True, "conversation_id": 42, "message_id": 101,
                "created_conversation": True}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.route_template_to_whatsapp",
        fake_route,
    )

    from roost.extras.messaging_external.services import whatsapp
    components = [
        {"type": "body",
         "parameters": [{"type": "text", "text": "Mei Ling"},
                        {"type": "text", "text": "endowment"}]},
    ]
    result = whatsapp.send_template_message(
        "+6591234567", "fa_welcome",
        language_code="en", components=components,
    )
    assert result == {
        "ok": True,
        "message_id": 101,
        "via": "chatwoot",
        "conversation_id": 42,
    }
    assert captured["phone"] == "+6591234567"
    assert captured["template_name"] == "fa_welcome"
    assert captured["kwargs"]["language"] == "en"
    assert captured["kwargs"]["processed_params"] == {"1": "Mei Ling", "2": "endowment"}


def test_whatsapp_document_routes_to_chatwoot_when_enabled(monkeypatch, tmp_path):
    """send_document with a local path should multipart-upload via Chatwoot."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    fake_pdf = tmp_path / "policy.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    captured: dict = {}

    def fake_route(phone, file_path, **kw):
        captured["phone"] = phone
        captured["file_path"] = str(file_path)
        captured["kwargs"] = kw
        return {"ok": True, "conversation_id": 7, "message_id": 202,
                "created_conversation": False}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.route_media_to_whatsapp",
        fake_route,
    )

    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_document("+6591234567", path=fake_pdf, caption="ts'kor your policy")
    assert result == {
        "ok": True,
        "message_id": 202,
        "via": "chatwoot",
        "conversation_id": 7,
    }
    assert captured["phone"] == "+6591234567"
    assert captured["file_path"] == str(fake_pdf)
    assert captured["kwargs"]["caption"] == "ts'kor your policy"


def test_whatsapp_image_link_errors_in_fa_edition(monkeypatch):
    """Meta-style link= and media_id= don't translate to Chatwoot — surface
    a clear error instead of silently dropping the send."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_image("+6591234567", link="https://example.com/a.jpg")
    assert "error" in result
    assert "local file path" in result["error"]


def test_whatsapp_mark_as_read_is_noop_in_fa_edition(monkeypatch):
    """Chatwoot owns read receipts on its inbox — mark_as_read becomes a no-op."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.mark_as_read("wamid.fake")
    assert result == {"ok": True, "via": "chatwoot", "no_op": True}


# ──────────────────────── route_template_to_whatsapp ──────────────────────


def test_route_template_reuses_open_conversation(cw_enabled, monkeypatch):
    """If a conv is already open, send the template into it — no create call."""
    import roost.extras.messaging_external.services.chatwoot as cw

    monkeypatch.setattr(cw, "find_or_create_contact", lambda **kw: {
        "ok": True, "contact_id": 7, "source_id": "+6591234567", "created": False,
    })
    monkeypatch.setattr(cw, "_find_open_conversation", lambda cid, inbox_id=None: 88)
    sends: list[dict] = []

    def fake_send(conv_id, template_name, **kw):
        sends.append({"conv_id": conv_id, "template_name": template_name, **kw})
        return {"ok": True, "message_id": 555}

    monkeypatch.setattr(cw, "send_template", fake_send)
    monkeypatch.setattr(cw, "create_conversation", lambda **kw: pytest.fail(
        "should not create when an open conv exists"
    ))

    result = cw.route_template_to_whatsapp(
        "+6591234567", "fa_welcome",
        processed_params={"1": "Mei Ling"},
    )
    assert result == {
        "ok": True,
        "conversation_id": 88,
        "message_id": 555,
        "created_conversation": False,
    }
    assert len(sends) == 1
    assert sends[0]["template_name"] == "fa_welcome"
    assert sends[0]["processed_params"] == {"1": "Mei Ling"}


def test_route_template_creates_empty_conversation_when_no_open(cw_enabled, monkeypatch):
    """Cold start: no open conv → open empty conv, then fire template."""
    import roost.extras.messaging_external.services.chatwoot as cw

    monkeypatch.setattr(cw, "find_or_create_contact", lambda **kw: {
        "ok": True, "contact_id": 7, "source_id": "+6591234567", "created": True,
    })
    monkeypatch.setattr(cw, "_find_open_conversation", lambda cid, inbox_id=None: None)
    creates: list[dict] = []

    def fake_create(**kw):
        creates.append(kw)
        return {"ok": True, "conversation_id": 99}

    sends: list[dict] = []

    def fake_send(conv_id, template_name, **kw):
        sends.append({"conv_id": conv_id, "template_name": template_name, **kw})
        return {"ok": True, "message_id": 777}

    monkeypatch.setattr(cw, "create_conversation", fake_create)
    monkeypatch.setattr(cw, "send_template", fake_send)

    result = cw.route_template_to_whatsapp("+6591234567", "fa_welcome")
    assert result["ok"] is True
    assert result["conversation_id"] == 99
    assert result["created_conversation"] is True
    # Empty conversation create (no initial_message — template arrives next call)
    assert creates == [{"source_id": "+6591234567", "contact_id": 7}]
    assert sends[0]["conv_id"] == 99


# ──────────────────────── route_media_to_whatsapp ────────────────────────


def test_route_media_uploads_via_open_conversation(cw_enabled, monkeypatch, tmp_path):
    """File path → find conv → send_attachment, no create."""
    import roost.extras.messaging_external.services.chatwoot as cw

    pdf = tmp_path / "policy.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(cw, "find_or_create_contact", lambda **kw: {
        "ok": True, "contact_id": 7, "source_id": "+6591234567", "created": False,
    })
    monkeypatch.setattr(cw, "_find_open_conversation", lambda cid, inbox_id=None: 88)
    sends: list[dict] = []

    def fake_attach(conv_id, file_path, **kw):
        sends.append({"conv_id": conv_id, "file_path": str(file_path), **kw})
        return {"ok": True, "message_id": 333}

    monkeypatch.setattr(cw, "send_attachment", fake_attach)
    monkeypatch.setattr(cw, "create_conversation", lambda **kw: pytest.fail(
        "should not create when an open conv exists"
    ))

    result = cw.route_media_to_whatsapp("+6591234567", pdf, caption="ts'kor")
    assert result == {
        "ok": True,
        "conversation_id": 88,
        "message_id": 333,
        "created_conversation": False,
    }
    assert sends[0]["file_path"] == str(pdf)
    assert sends[0]["caption"] == "ts'kor"


# ──────────────────────── send_template payload shape ────────────────────


def test_send_template_posts_chatwoot_payload(cw_enabled, monkeypatch):
    """Verify the exact JSON shape Chatwoot's UI expects for templates."""
    import roost.extras.messaging_external.services.chatwoot as cw

    captured: dict = {}

    class _FakeResp:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {"id": 9001}

    class _FakeClient:
        def __init__(self, timeout=30):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, *, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResp()

    monkeypatch.setattr(cw, "httpx", type("X", (), {
        "Client": _FakeClient,
        "HTTPStatusError": RuntimeError,
    }))
    monkeypatch.setattr(cw, "CHATWOOT_URL", "https://chatwoot.test")
    monkeypatch.setattr(cw, "CHATWOOT_API_KEY", "test-token")
    monkeypatch.setattr(cw, "CHATWOOT_ACCOUNT_ID", "1")

    result = cw.send_template(
        42, "fa_welcome",
        processed_params={"1": "Mei"},
        language="en",
        category="MARKETING",
        body="Hi Mei, your endowment review is ready.",
    )
    assert result["ok"] is True
    assert result["message_id"] == 9001
    assert captured["url"] == \
        "https://chatwoot.test/api/v1/accounts/1/conversations/42/messages"
    assert captured["headers"]["api_access_token"] == "test-token"
    assert captured["json"] == {
        "content": "Hi Mei, your endowment review is ready.",
        "message_type": "outgoing",
        "template_params": {
            "name": "fa_welcome",
            "category": "MARKETING",
            "language": "en",
            "processed_params": {"1": "Mei"},
        },
    }


# ──────────────────────── list_templates ─────────────────────────────────


def test_list_templates_returns_payload(cw_enabled, monkeypatch):
    """GET /inboxes/:iid surfaces message_templates synced from WABA."""
    import roost.extras.messaging_external.services.chatwoot as cw

    class _FakeResp:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "id": 2,
                "name": "WhatsApp Cloud",
                "message_templates": [
                    {"name": "fa_welcome", "language": "en", "category": "MARKETING"},
                    {"name": "appointment_confirm", "language": "en", "category": "UTILITY"},
                ],
            }

    class _FakeClient:
        def __init__(self, timeout=30):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *, headers):
            assert url.endswith("/inboxes/2")
            return _FakeResp()

    monkeypatch.setattr(cw, "httpx", type("X", (), {
        "Client": _FakeClient,
        "HTTPStatusError": RuntimeError,
    }))
    monkeypatch.setattr(cw, "CHATWOOT_URL", "https://chatwoot.test")
    monkeypatch.setattr(cw, "CHATWOOT_API_KEY", "test-token")
    monkeypatch.setattr(cw, "CHATWOOT_ACCOUNT_ID", "1")

    result = cw.list_templates(inbox_id=2)
    assert result["ok"] is True
    assert len(result["templates"]) == 2
    assert result["templates"][0]["name"] == "fa_welcome"


# ──────────────── conversation_meta / list_open_conversations (FA-H) ─────


def test_conversation_meta_returns_counts(cw_enabled, monkeypatch):
    """GET /conversations/meta?assignee_type=me projects the four counts."""
    import roost.extras.messaging_external.services.chatwoot as cw

    class _FakeResp:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "meta": {
                    "open": 4,
                    "resolved": 17,
                    "pending": 1,
                    "all_count": 22,
                },
            }

    captured: dict = {}

    class _FakeClient:
        def __init__(self, timeout=30):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *, headers, params=None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp()

    monkeypatch.setattr(cw, "httpx", type("X", (), {
        "Client": _FakeClient,
        "HTTPStatusError": RuntimeError,
    }))
    monkeypatch.setattr(cw, "CHATWOOT_URL", "https://chatwoot.test")
    monkeypatch.setattr(cw, "CHATWOOT_API_KEY", "test-token")
    monkeypatch.setattr(cw, "CHATWOOT_ACCOUNT_ID", "1")

    result = cw.conversation_meta(assignee_type="me")
    assert result == {
        "ok": True,
        "open": 4,
        "resolved": 17,
        "pending": 1,
        "all_count": 22,
    }
    assert captured["url"].endswith("/conversations/meta")
    assert captured["params"] == {"assignee_type": "me"}


def test_list_open_conversations_returns_top(cw_enabled, monkeypatch):
    """GET /conversations?status=open projects {id, contact, preview} rows
    from the Chatwoot envelope shape (`data.payload[…].meta.sender`)."""
    import roost.extras.messaging_external.services.chatwoot as cw

    class _FakeResp:
        status_code = 200
        content = b"{}"

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "payload": [
                        {
                            "id": 101,
                            "meta": {"sender": {
                                "name": "Tan Mei",
                                "phone_number": "+6591234567",
                            }},
                            "messages": [{"content": "Hi, can we meet Thursday?"}],
                        },
                        {
                            "id": 102,
                            "meta": {"sender": {
                                "phone_number": "+6598887777",
                            }},
                            "messages": [{"content": "Following up on the\nquote."}],
                        },
                        {
                            "id": 103,
                            "meta": {"sender": {}},
                            "messages": [],
                        },
                    ],
                },
            }

    captured: dict = {}

    class _FakeClient:
        def __init__(self, timeout=30):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, *, headers, params=None):
            captured["url"] = url
            captured["params"] = params
            return _FakeResp()

    monkeypatch.setattr(cw, "httpx", type("X", (), {
        "Client": _FakeClient,
        "HTTPStatusError": RuntimeError,
    }))
    monkeypatch.setattr(cw, "CHATWOOT_URL", "https://chatwoot.test")
    monkeypatch.setattr(cw, "CHATWOOT_API_KEY", "test-token")
    monkeypatch.setattr(cw, "CHATWOOT_ACCOUNT_ID", "1")

    result = cw.list_open_conversations(limit=5)
    assert result["ok"] is True
    conversations = result["conversations"]
    assert len(conversations) == 3
    assert conversations[0] == {
        "id": 101,
        "contact": "Tan Mei",
        "preview": "Hi, can we meet Thursday?",
    }
    # Phone fallback when name missing.
    assert conversations[1]["contact"] == "+6598887777"
    # No sender → placeholder string; no messages → empty preview.
    assert conversations[2] == {
        "id": 103,
        "contact": "(no contact)",
        "preview": "",
    }
    assert captured["url"].endswith("/conversations")
    assert captured["params"] == {"status": "open", "page": 1}
