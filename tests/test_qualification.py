"""Tests for the channel-aware lead qualification flow.

We patch the per-channel send adapters so no real WhatsApp/WeChat/Telegram
calls fire, and verify:
  - start_qualification_if_needed pauses the enrollment + writes state
  - process_answer advances the cursor, persists answers, sends next q
  - the score path produces the correct hot/warm/cold label
  - hot finalisation resumes the cadence and triggers operator notify
  - cold finalisation exits the enrollment
  - find_active_session matches by (channel, identifier) and ignores others
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


def _seed_property_cadence_and_enroll(*, identifier: str, channel: str = "whatsapp"):
    """Seed the property_buyer_intro cadence and create one enrollment.
    Returns the enrollment_id."""
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    seed_library()
    enr = enroll_lead(
        cadence_slug="property_buyer_intro",
        contact_name="Test Buyer",
        contact_phone=identifier if channel == "whatsapp" else "",
        channel=channel,
        source=channel,
    )
    return enr["id"]


# ── start_qualification_if_needed ──────────────────────────────────────


def test_start_pauses_enrollment_and_sends_first_question(
    clean_cadence_tables, stub_sends
):
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591234567")
    res = qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        identifier="+6591234567",
        contact_name="Alice Tan",
    )
    assert res["started"] is True
    assert res["question_count"] == 3

    enr = get_enrollment(enr_id)
    assert enr["status"] == "paused"
    assert enr["pause_reason"] == "qualifying"
    assert enr["fields"]["_qualify_status"] == "in_progress"
    assert enr["fields"]["_qualify_idx"] == 0
    assert enr["fields"]["_qualify_channel"] == "whatsapp"
    assert enr["fields"]["_qualify_identifier"] == "+6591234567"
    assert enr["fields"]["_qualify_cadence_slug"] == "property_buyer_intro"

    assert len(stub_sends) == 1
    ch, ident, text = stub_sends[0]
    assert ch == "whatsapp"
    assert ident == "+6591234567"
    assert "Alice" in text  # personalised


def test_start_skips_when_no_question_pack(clean_cadence_tables, stub_sends):
    """Cadences without a question pack should leave the enrollment alone."""
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    from roost.extras.lead_nurture.services import qualification

    seed_library()
    enr = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="x@example.com",
        channel="email",
    )
    res = qualification.start_qualification_if_needed(
        enrollment_id=enr["id"],
        cadence_slug="generic_b2b",
        channel="whatsapp",
        identifier="+6591234567",
    )
    assert res["started"] is False
    assert res["reason"] == "no_questions"
    assert stub_sends == []


def test_start_skips_when_channel_not_addressable(clean_cadence_tables, stub_sends):
    from roost.extras.lead_nurture.services import qualification

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591234567")
    res = qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="email",          # not in addressable set
        identifier="x@example.com",
    )
    assert res["started"] is False
    assert res["reason"] == "no_addressable_channel"


# ── find_active_session ────────────────────────────────────────────────


def test_find_active_session_matches_by_channel_and_identifier(
    clean_cadence_tables, stub_sends
):
    from roost.extras.lead_nurture.services import qualification

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591111111")
    qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        identifier="+6591111111",
    )

    found = qualification.find_active_session("whatsapp", "+6591111111")
    assert found is not None
    assert found["id"] == enr_id

    # different identifier on same channel → no match
    assert qualification.find_active_session("whatsapp", "+6592222222") is None
    # same identifier but wrong channel → no match
    assert qualification.find_active_session("wechat", "+6591111111") is None


# ── process_answer ─────────────────────────────────────────────────────


def test_process_answer_advances_cursor_and_sends_next_question(
    clean_cadence_tables, stub_sends
):
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591234567")
    qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        identifier="+6591234567",
    )
    stub_sends.clear()  # discard first question

    res = qualification.process_answer(
        "whatsapp", "+6591234567", "in 3 months"
    )
    assert res == {"handled": True, "done": False, "next_idx": 1}
    assert len(stub_sends) == 1  # the next question was sent

    enr = get_enrollment(enr_id)
    assert enr["fields"]["_qualify_idx"] == 1
    assert enr["fields"]["_qualify_answers"]["timeline"] == "in 3 months"
    assert enr["status"] == "paused"


def test_process_answer_no_session_returns_unhandled(clean_cadence_tables, stub_sends):
    from roost.extras.lead_nurture.services import qualification
    res = qualification.process_answer("whatsapp", "+6599999999", "yo")
    assert res == {"handled": False}


# ── scoring ────────────────────────────────────────────────────────────


def test_score_hot_label():
    from roost.extras.lead_nurture.services.qualification import (
        _score,
        QUESTIONS_BY_CADENCE,
    )
    questions = QUESTIONS_BY_CADENCE["property_buyer_intro"]
    answers = {
        "timeline": "this month, asap",
        "budget_band": "3M flexible",
        "mortgage_status": "yes, IPA approved",
    }
    result = _score(answers, questions)
    assert result["label"] == "hot"
    assert result["score"] >= 0.9


def test_score_warm_label():
    from roost.extras.lead_nurture.services.qualification import (
        _score,
        QUESTIONS_BY_CADENCE,
    )
    questions = QUESTIONS_BY_CADENCE["property_buyer_intro"]
    answers = {
        "timeline": "next month",
        "budget_band": "1.5M",
        "mortgage_status": "looking into",
    }
    result = _score(answers, questions)
    assert result["label"] == "warm"


def test_score_cold_label():
    from roost.extras.lead_nurture.services.qualification import (
        _score,
        QUESTIONS_BY_CADENCE,
    )
    questions = QUESTIONS_BY_CADENCE["property_buyer_intro"]
    answers = {
        "timeline": "just browsing",
        "budget_band": "not sure yet",
        "mortgage_status": "not really thought about it",
    }
    result = _score(answers, questions)
    assert result["label"] == "cold"


# ── end-to-end finalise ────────────────────────────────────────────────


def test_full_flow_hot_resumes_cadence(clean_cadence_tables, stub_sends, monkeypatch):
    """All 3 answers hot → enrollment resumed, hot-lead notify called once."""
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    notified: list[dict] = []
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads._notify_hot_lead",
        lambda **kwargs: notified.append(kwargs),
    )

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591234567")
    qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        identifier="+6591234567",
    )
    qualification.process_answer("whatsapp", "+6591234567", "this month")
    qualification.process_answer("whatsapp", "+6591234567", "3M flexible")
    res = qualification.process_answer("whatsapp", "+6591234567", "yes, IPA approved")
    assert res["handled"] is True
    assert res["done"] is True
    assert res["label"] == "hot"

    enr = get_enrollment(enr_id)
    assert enr["status"] == "active"  # resumed
    assert enr["fields"]["_qualify_status"] == "done"
    assert enr["fields"]["_qualify_label"] == "hot"
    assert len(notified) == 1
    assert notified[0]["channel"] == "whatsapp"


def test_full_flow_cold_exits_enrollment(clean_cadence_tables, stub_sends, monkeypatch):
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads._notify_hot_lead",
        lambda **kwargs: None,
    )

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591234567")
    qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        identifier="+6591234567",
    )
    qualification.process_answer("whatsapp", "+6591234567", "just browsing")
    qualification.process_answer("whatsapp", "+6591234567", "no idea")
    res = qualification.process_answer("whatsapp", "+6591234567", "haven't thought about it")
    assert res["label"] == "cold"

    enr = get_enrollment(enr_id)
    assert enr["status"] == "exited"
    assert enr["pause_reason"] == "qualified_cold"
    assert enr["fields"]["_qualify_status"] == "exited_cold"


# ── send-failure abort ─────────────────────────────────────────────────


def test_start_aborts_when_send_fails(clean_cadence_tables, monkeypatch):
    """If the first-question send returns an error, the enrollment must
    NOT be paused — let the cadence proceed instead."""
    from roost.extras.lead_nurture.services import qualification
    from roost.extras.lead_nurture.services.cadences import get_enrollment

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification._send_question",
        lambda *a, **kw: {"error": "whatsapp not configured"},
    )

    enr_id = _seed_property_cadence_and_enroll(identifier="+6591234567")
    res = qualification.start_qualification_if_needed(
        enrollment_id=enr_id,
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        identifier="+6591234567",
    )
    assert res["started"] is False
    assert res["reason"] == "send_failed"

    enr = get_enrollment(enr_id)
    assert enr["status"] == "active"  # untouched
    assert "_qualify_status" not in (enr["fields"] or {})
