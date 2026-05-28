"""Tests for the human-escape path and the YAML-pack lookup fix.

Two things are covered here:

1. `_wants_human` phrase detection (unit).
2. The qualification flow honours a "let me talk to a person" request,
   both as the opening inbound message and mid-questionnaire — pausing
   the enrollment and alerting the operator instead of force-marching
   the lead through the rest of the questions.
3. Regression for the `financial_advisor_intro` pack, which only exists
   in YAML (not the in-code `QUESTIONS_BY_CADENCE` dict): `process_answer`
   must resolve questions via `_get_pack`, not the dict, or the dialog
   never advances past question 1.
"""

from __future__ import annotations

import pytest


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


@pytest.fixture
def stub_sends(monkeypatch):
    """Replace channel send adapters with a recorder."""
    sent: list[tuple[str, str, str]] = []

    def fake_send(channel, identifier, text):
        sent.append((channel, identifier, text))
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification._send_question",
        fake_send,
    )
    return sent


@pytest.fixture
def stub_notify(monkeypatch):
    """Capture _notify_hot_lead calls so escalation can be asserted without
    firing real Telegram/email."""
    notified: list[dict] = []
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads._notify_hot_lead",
        lambda **kwargs: notified.append(kwargs),
    )
    return notified


def _seed_fa_cadence_and_enroll(*, identifier: str):
    """Seed the financial_advisor_intro cadence (YAML-only question pack)
    and create one whatsapp enrollment. Returns the enrollment_id."""
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    seed_library()
    enr = enroll_lead(
        cadence_slug="financial_advisor_intro",
        contact_name="Test Lead",
        contact_phone=identifier,
        channel="whatsapp",
        source="whatsapp",
    )
    return enr["id"]


# ── _wants_human ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "can I just talk to someone?",
        "I'd rather speak to a person please",
        "just call me",
        "give me a call when you can",
        "talk to a real human pls",
        "can you get an advisor to phone me",
    ],
)
def test_wants_human_positive(text):
    from roost.extras.lead_nurture.services.qualification import _wants_human
    assert _wants_human(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "this month",
        "I'm looking to review my finances soon",
        "new baby on the way",
        "I have some life insurance already",
        "",
        "no rush, just exploring",
    ],
)
def test_wants_human_negative(text):
    from roost.extras.lead_nurture.services.qualification import _wants_human
    assert _wants_human(text) is False


# ── line-294 regression: YAML-only pack must advance ────────────────────


def test_financial_advisor_pack_advances(clean_cadence_tables, stub_sends):
    """financial_advisor_intro has no entry in QUESTIONS_BY_CADENCE — it
    lives only in YAML. Before the fix, process_answer read the in-code
    dict and returned {handled: False}, so the dialog stalled after q1.
    """
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    enr_id = _seed_fa_cadence_and_enroll(identifier="+6591234567")
    res = qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="financial_advisor_intro",
        channel="whatsapp",
        identifier="+6591234567",
    )
    assert res["started"] is True
    assert res["question_count"] == 3
    stub_sends.clear()  # discard first question

    # Answer q1 → must advance to q2 (this is the regression).
    res = qualification.process_answer("whatsapp", "+6591234567", "in a few months")
    assert res["handled"] is True
    assert res["done"] is False
    assert res["next_idx"] == 1
    assert len(stub_sends) == 1  # q2 sent

    enr = get_enrollment(enr_id)
    assert enr["fields"]["_qualify_idx"] == 1
    assert enr["fields"]["_qualify_answers"]["timeline"] == "in a few months"


# ── mid-questionnaire human escape ──────────────────────────────────────


def test_mid_questionnaire_human_escape(
    clean_cadence_tables, stub_sends, stub_notify
):
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    enr_id = _seed_fa_cadence_and_enroll(identifier="+6591234567")
    qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="financial_advisor_intro",
        channel="whatsapp",
        identifier="+6591234567",
    )
    stub_sends.clear()

    res = qualification.process_answer(
        "whatsapp", "+6591234567", "actually can I just talk to someone?"
    )
    assert res == {"handled": True, "done": True, "label": "human_requested"}

    # Reassurance went out to the lead.
    assert len(stub_sends) == 1
    assert "reach out" in stub_sends[0][2].lower()

    # Operator was alerted, hot, with the ask quoted.
    assert len(stub_notify) == 1
    assert stub_notify[0]["classification"]["urgency"] == "hot"
    assert stub_notify[0]["classification"]["intent"] == "human_handoff_requested"

    # Enrollment paused for human, not exited.
    enr = get_enrollment(enr_id)
    assert enr["status"] == "paused"
    assert enr["pause_reason"] == "human_requested"
    assert enr["fields"]["_qualify_status"] == "escalated_human"
    assert "talk to someone" in enr["fields"]["_qualify_escalation_text"].lower()


# ── first-message human escape ──────────────────────────────────────────


def test_first_message_human_escape(
    clean_cadence_tables, stub_sends, stub_notify
):
    """If the opening inbound message already asks for a person, skip the
    questionnaire entirely."""
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    enr_id = _seed_fa_cadence_and_enroll(identifier="+6591234567")
    res = qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="financial_advisor_intro",
        channel="whatsapp",
        identifier="+6591234567",
        trigger_text="hi, can someone please call me back?",
    )
    assert res["started"] is False
    assert res["reason"] == "human_requested"
    assert res["escalated"] is True

    # No questionnaire question was sent — only the reassurance.
    assert len(stub_sends) == 1
    assert "reach out" in stub_sends[0][2].lower()

    assert len(stub_notify) == 1
    assert stub_notify[0]["classification"]["intent"] == "human_handoff_requested"

    enr = get_enrollment(enr_id)
    assert enr["status"] == "paused"
    assert enr["pause_reason"] == "human_requested"
    assert enr["fields"]["_qualify_status"] == "escalated_human"
