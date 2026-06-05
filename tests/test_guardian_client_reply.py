"""Guardian holds agent-drafted client replies until the adviser approves.

The FA-edition flow: inbound → AI draft → `guardian_gate("send_client_reply")`
holds it as a pending Guardian draft (Telegram Approve/Reject) → on approve,
the executor delivers it on the originating channel. No auto-send.
"""

from __future__ import annotations

import pytest

from roost.database import get_connection
from roost.services import guardian


@pytest.fixture
def clean_drafts():
    def _wipe():
        c = get_connection()
        c.execute("DELETE FROM guardian_drafts")
        c.commit()
        c.close()
    _wipe()
    yield
    _wipe()


def test_client_reply_needs_approval():
    r = guardian.guardian_check(
        "send_client_reply",
        {"channel": "chatwoot", "conversation_id": 2, "text": "hi"},
    )
    assert r["decision"] == guardian.NEEDS_APPROVAL
    assert r["rule"] == "client_message_draft"


def test_unrelated_tool_not_gated_by_client_rule():
    assert guardian.guardian_check("get_person", {})["decision"] == guardian.ALLOW


def test_gate_creates_pending_draft(clean_drafts):
    res = guardian.guardian_gate(
        "send_client_reply",
        {"channel": "chatwoot", "conversation_id": 2, "text": "hello"},
    )
    assert res is not None
    assert res["status"] == "pending_approval"
    assert isinstance(res["draft_id"], int)


def test_approve_sends_via_chatwoot(clean_drafts, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(
        cw, "send_message",
        lambda conv, content, **k: sent.append((conv, content)) or {"ok": True},
    )
    res = guardian.guardian_gate(
        "send_client_reply",
        {"channel": "chatwoot", "conversation_id": 7, "text": "Hi there!"},
    )
    guardian.approve_draft(res["draft_id"])
    assert sent == [(7, "Hi there!")]


def test_approve_sends_via_whatsapp(clean_drafts, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.whatsapp as wa
    monkeypatch.setattr(
        wa, "send_text_message",
        lambda to, body: sent.append((to, body)) or {"ok": True},
    )
    res = guardian.guardian_gate(
        "send_client_reply",
        {"channel": "whatsapp", "to": "+6599999999", "text": "Hello"},
    )
    guardian.approve_draft(res["draft_id"])
    assert sent == [("+6599999999", "Hello")]


def test_reject_does_not_send(clean_drafts, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(cw, "send_message", lambda *a, **k: sent.append(a))
    res = guardian.guardian_gate(
        "send_client_reply",
        {"channel": "chatwoot", "conversation_id": 2, "text": "no"},
    )
    guardian.reject_draft(res["draft_id"], reason="off-tone")
    assert sent == []
