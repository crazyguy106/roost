"""Lead/enrollment dedup: a returning contact continues their enrolment
instead of spawning duplicates, and isn't re-interrogated.

Regression for the bug where `+65…` vs `65…` phone formats (Chatwoot sends
the bare wa_id on some events, the E.164 contact phone on others) plus an
unconditional INSERT in `enroll_lead` created multiple enrolments per
contact — so a returning WhatsApp/Chatwoot lead got re-enrolled and
re-asked the qualification questions.
"""

from __future__ import annotations

import pytest

from roost.database import get_connection
from roost.extras.lead_nurture.services.leads import _norm_phone


# ── _norm_phone canonicalisation ────────────────────────────────────


def test_norm_phone_canonicalises_to_e164():
    assert _norm_phone("6597531358") == "+6597531358"      # bare wa_id → +
    assert _norm_phone("+6597531358") == "+6597531358"
    assert _norm_phone("+65 9753 1358") == "+6597531358"   # strip spaces
    assert _norm_phone("  +65-9753-1358 ") == "+6597531358"
    assert _norm_phone("(+65) 9753 1358") == "+6597531358"


def test_norm_phone_edges():
    assert _norm_phone("") == ""
    assert _norm_phone("   ") == ""
    assert _norm_phone("12345") == "12345"   # short/local left untouched


# ── enroll_lead dedup ───────────────────────────────────────────────


_TEST_PHONES = ("+6590000001", "+6590000002", "+6590000003")


@pytest.fixture
def enroll_env():
    from roost.extras.lead_nurture.services.cadences import seed_library
    seed_library()

    def _wipe():
        conn = get_connection()
        conn.execute(
            "DELETE FROM nurture_enrollments WHERE contact_phone IN (?,?,?)",
            _TEST_PHONES,
        )
        conn.commit()
        conn.close()

    _wipe()
    yield
    _wipe()


def test_enroll_reuses_existing_by_person(enroll_env):
    from roost.extras.lead_nurture.services.cadences import enroll_lead
    e1 = enroll_lead(cadence_slug="generic_b2b", contact_phone=_TEST_PHONES[0],
                     crm_person_id="px")
    e2 = enroll_lead(cadence_slug="generic_b2b", contact_phone=_TEST_PHONES[0],
                     crm_person_id="px")
    assert e1["id"] == e2["id"]


def test_enroll_reuses_existing_by_phone(enroll_env):
    from roost.extras.lead_nurture.services.cadences import enroll_lead
    e1 = enroll_lead(cadence_slug="generic_b2b", contact_phone=_TEST_PHONES[1])
    e2 = enroll_lead(cadence_slug="generic_b2b", contact_phone=_TEST_PHONES[1])
    assert e1["id"] == e2["id"]


def test_enroll_distinct_contacts_are_separate(enroll_env):
    from roost.extras.lead_nurture.services.cadences import enroll_lead
    e1 = enroll_lead(cadence_slug="generic_b2b", contact_phone=_TEST_PHONES[0])
    e2 = enroll_lead(cadence_slug="generic_b2b", contact_phone=_TEST_PHONES[2])
    assert e1["id"] != e2["id"]


# ── start_qualification re-start guard ──────────────────────────────


def test_qualification_skips_already_qualified(monkeypatch):
    from roost.extras.lead_nurture.services import qualification as q
    monkeypatch.setattr(q, "_get_pack", lambda slug: [{"key": "t",
                                                      "question": "When?"}])
    sent = []
    monkeypatch.setattr(q, "_send_question",
                        lambda c, i, t: sent.append(1) or {"ok": True})
    monkeypatch.setattr(
        q, "_load_enrollment",
        lambda eid: {"id": eid, "fields": {"_qualify_status": "done"}},
    )
    res = q.start_qualification_if_needed(
        enrollment_id=1, cadence_slug="x", channel="chatwoot",
        identifier="+6591234567", trigger_text="hi",
    )
    assert res["reason"] == "already_qualified"
    assert sent == []   # did NOT re-send question 1
