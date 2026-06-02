"""Tests for the per-contact conversation thread + operator reply box.

Covers:
1. `conversation.log_message` / `get_thread` — store + retrieve, ordering,
   matching by enrollment_id OR (channel, identifier), bad-direction guard.
2. `conversation.send_reply` — resolves the channel/identifier from the
   enrollment, sends via the (stubbed) dispatcher, and the outbound is
   recorded in the thread (logged once, at the _send_question chokepoint).
3. `enrollment_detail` exposes `messages` + `can_reply`.
4. The `POST /api/leads/{id}/reply` endpoint round-trip.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def clean_conversation_tables():
    from roost.database import get_connection
    tables = (
        "lead_messages",
        "nurture_enrollments",
        "cadence_preapprovals",
        "nurture_cadences",
    )
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


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Replace the raw network send so no real WhatsApp/Telegram call fires,
    while leaving the real `_send_question` (and its thread logging) intact."""
    sent: list[tuple[str, str, str]] = []

    def fake_do_send(channel, identifier, text):
        sent.append((channel, identifier, text))
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification._do_send",
        fake_do_send,
    )
    return sent


def _seed_and_enroll(*, identifier="+6591234567", channel="whatsapp"):
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    seed_library()
    enr = enroll_lead(
        cadence_slug="property_buyer_intro",
        contact_name="Test Lead",
        contact_phone=identifier,
        channel=channel,
        fields={
            "_qualify_channel": channel,
            "_qualify_identifier": identifier,
        },
    )
    return enr["id"]


# ── log_message / get_thread ────────────────────────────────────────────


def test_log_and_get_thread_by_contact(clean_conversation_tables):
    from roost.extras.lead_nurture.services import conversation
    conversation.log_message(
        channel="whatsapp", identifier="+65111", direction="in", body="hi"
    )
    conversation.log_message(
        channel="whatsapp", identifier="+65111", direction="out", body="hello back"
    )
    thread = conversation.get_thread(channel="whatsapp", identifier="+65111")
    assert [m["body"] for m in thread] == ["hi", "hello back"]
    assert [m["direction"] for m in thread] == ["in", "out"]


def test_get_thread_by_enrollment_id(clean_conversation_tables):
    from roost.extras.lead_nurture.services import conversation
    conversation.log_message(
        channel="whatsapp", identifier="+65222", direction="in",
        body="enrolled msg", enrollment_id=42,
    )
    thread = conversation.get_thread(enrollment_id=42)
    assert len(thread) == 1
    assert thread[0]["body"] == "enrolled msg"


def test_get_thread_matches_enrollment_or_contact(clean_conversation_tables):
    """A pre-enrollment inbound (keyed by contact only) and a later message
    keyed by enrollment_id both surface for the same lead."""
    from roost.extras.lead_nurture.services import conversation
    conversation.log_message(
        channel="whatsapp", identifier="+65333", direction="in", body="before enroll"
    )
    conversation.log_message(
        channel="whatsapp", identifier="+65333", direction="out",
        body="after enroll", enrollment_id=7,
    )
    thread = conversation.get_thread(
        enrollment_id=7, channel="whatsapp", identifier="+65333"
    )
    assert [m["body"] for m in thread] == ["before enroll", "after enroll"]


def test_log_message_rejects_bad_direction(clean_conversation_tables):
    from roost.extras.lead_nurture.services import conversation
    conversation.log_message(
        channel="whatsapp", identifier="+65444", direction="sideways", body="x"
    )
    assert conversation.get_thread(channel="whatsapp", identifier="+65444") == []


def test_get_thread_empty_without_keys(clean_conversation_tables):
    from roost.extras.lead_nurture.services import conversation
    assert conversation.get_thread() == []


# ── send_reply ──────────────────────────────────────────────────────────


def test_send_reply_sends_and_logs_outbound(clean_conversation_tables, stub_dispatch):
    from roost.extras.lead_nurture.services import conversation
    enr_id = _seed_and_enroll(identifier="+6591234567")

    res = conversation.send_reply(enrollment_id=enr_id, text="Hi there, following up!")
    assert res["ok"] is True
    assert res["channel"] == "whatsapp"

    # The dispatcher was called exactly once with the operator's text.
    assert stub_dispatch == [("whatsapp", "+6591234567", "Hi there, following up!")]

    # The outbound is in the thread exactly once (logged at the chokepoint,
    # not double-logged by send_reply).
    thread = conversation.get_thread(channel="whatsapp", identifier="+6591234567")
    out = [m for m in thread if m["direction"] == "out"]
    assert len(out) == 1
    assert out[0]["body"] == "Hi there, following up!"


def test_send_reply_empty_text_errors(clean_conversation_tables, stub_dispatch):
    from roost.extras.lead_nurture.services import conversation
    enr_id = _seed_and_enroll()
    res = conversation.send_reply(enrollment_id=enr_id, text="   ")
    assert "error" in res
    assert stub_dispatch == []


def test_send_reply_missing_enrollment_errors(clean_conversation_tables, stub_dispatch):
    from roost.extras.lead_nurture.services import conversation
    res = conversation.send_reply(enrollment_id=999_999, text="hello")
    assert "error" in res


def test_send_reply_no_channel_errors(clean_conversation_tables, stub_dispatch):
    """An email-only enrollment has no addressable inbound channel for a
    real-time reply."""
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    from roost.extras.lead_nurture.services import conversation
    seed_library()
    enr = enroll_lead(
        cadence_slug="property_buyer_intro",
        contact_name="Email Only",
        contact_email="x@example.com",
        channel="email",
    )
    res = conversation.send_reply(enrollment_id=enr["id"], text="hi")
    assert "error" in res
    assert stub_dispatch == []


def test_send_reply_accepts_chatwoot_channel(clean_conversation_tables, stub_dispatch):
    """Chatwoot leads (ingested by api_chatwoot with channel='chatwoot')
    must be replyable from the /leads UI. FA-I added 'chatwoot' to the
    addressable-channel whitelist; before that this returned
    'no addressable channel'."""
    from roost.extras.lead_nurture.services import conversation
    enr_id = _seed_and_enroll(identifier="+6591234567", channel="chatwoot")

    res = conversation.send_reply(enrollment_id=enr_id, text="Thanks — Thursday 2pm works.")
    assert res["ok"] is True
    assert res["channel"] == "chatwoot"
    assert res["identifier"] == "+6591234567"
    assert stub_dispatch == [("chatwoot", "+6591234567", "Thanks — Thursday 2pm works.")]


# ── enrollment_detail payload ───────────────────────────────────────────


def test_enrollment_detail_includes_messages_and_can_reply(
    clean_conversation_tables, stub_dispatch
):
    from roost.extras.lead_nurture.services import conversation
    from roost.extras.lead_nurture.web.pages import enrollment_detail
    enr_id = _seed_and_enroll(identifier="+6599998888")

    conversation.log_message(
        channel="whatsapp", identifier="+6599998888", direction="in", body="hello?"
    )
    d = enrollment_detail(enr_id)
    assert d is not None
    assert d["can_reply"] is True
    assert [m["body"] for m in d["messages"]] == ["hello?"]


# ── HTTP round-trip ─────────────────────────────────────────────────────


def _build_app():
    from roost.extras.lead_nurture.web.api_leads import router as leads_router
    app = FastAPI()
    app.include_router(leads_router)
    return app


def test_reply_endpoint_sends(clean_conversation_tables, stub_dispatch):
    from roost.extras.lead_nurture.services import conversation
    enr_id = _seed_and_enroll(identifier="+6577776666")
    client = TestClient(_build_app())
    r = client.post(f"/api/leads/{enr_id}/reply", json={"text": "thanks for reaching out"})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert stub_dispatch == [("whatsapp", "+6577776666", "thanks for reaching out")]
    thread = conversation.get_thread(channel="whatsapp", identifier="+6577776666")
    assert any(m["body"] == "thanks for reaching out" for m in thread)


def test_reply_endpoint_400_on_bad_enrollment(clean_conversation_tables, stub_dispatch):
    client = TestClient(_build_app())
    r = client.post("/api/leads/999999/reply", json={"text": "hi"})
    assert r.status_code == 400
    assert "error" in r.json()
