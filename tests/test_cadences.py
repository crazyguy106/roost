"""Tests for the lead-nurture cadence engine — schema/loader, enrollment,
preapproval matching, and CRM stage-change router.

These tests exercise the data path only (DB + YAML files). Dispatch (email
send, WhatsApp call) is covered separately in test_nurture.py with mocks.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def clean_cadence_tables():
    """Wipe nurture tables so each test starts from empty."""
    from roost.database import get_connection
    tables = (
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


# ── Library YAML ───────────────────────────────────────────────────────


def test_library_files_all_validate():
    from roost.extras.lead_nurture.services.cadences import LIBRARY_DIR, load_yaml
    files = sorted(LIBRARY_DIR.glob("*.yaml"))
    assert files, "no cadence library files found"
    for p in files:
        load_yaml(p)  # raises on validation error


def test_seed_library_is_idempotent(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import seed_library, list_cadences
    r1 = seed_library()
    n1 = len(list_cadences(enabled_only=False))
    r2 = seed_library()
    n2 = len(list_cadences(enabled_only=False))
    assert n1 == n2 > 0
    assert r1.get("seeded", 0) >= 1
    # Second run should be a pure no-op (everything skipped)
    assert r2.get("seeded", 0) == 0
    assert r2.get("skipped", 0) == r1.get("seeded", 0)


# ── Enrollment ─────────────────────────────────────────────────────────


def test_enroll_lead_creates_active_enrollment(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead, get_enrollment
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="x@example.com",
        channel="email",
    )
    assert e["status"] == "active"
    assert e["current_step"] == 0
    assert e["next_run_at"]
    fetched = get_enrollment(e["id"])
    assert fetched["id"] == e["id"]


def test_enroll_unknown_cadence_raises(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    seed_library()
    with pytest.raises(ValueError):
        enroll_lead(cadence_slug="nope_does_not_exist",
                    contact_email="x@example.com")


def test_due_picker_returns_only_elapsed(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, list_due_enrollments,
    )
    seed_library()
    # next_run_at far in future → not due
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="future@example.com",
        next_run_at="2099-01-01T00:00:00Z",
    )
    due_now = list_due_enrollments(now_utc="2026-01-01T00:00:00Z")
    assert all(d["id"] != e["id"] for d in due_now)
    # Once we move time forward, it's due
    due_later = list_due_enrollments(now_utc="2099-12-31T00:00:00Z")
    assert any(d["id"] == e["id"] for d in due_later)


# ── Preapproval matching ───────────────────────────────────────────────


def test_preapproval_wildcards(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        create_preapproval, match_preapproval,
    )
    create_preapproval()  # all wildcards
    m = match_preapproval(
        cadence_slug="anything", source="anywhere",
        channel="email", vertical="generic",
    )
    assert m is not None


def test_preapproval_specificity_wins(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        create_preapproval, match_preapproval,
    )
    # Wildcard rule
    create_preapproval(note="loose")
    # Specific rule on cadence + channel
    create_preapproval(
        cadence_slug="property_buyer_intro",
        channel="whatsapp",
        note="tight",
    )
    m = match_preapproval(
        cadence_slug="property_buyer_intro",
        source="anywhere",
        channel="whatsapp",
        vertical="property",
    )
    assert m is not None
    assert m["note"] == "tight"


def test_preapproval_excludes_non_matching(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        create_preapproval, match_preapproval,
    )
    create_preapproval(channel="whatsapp", note="wa-only")
    m = match_preapproval(
        cadence_slug="x", source="y", channel="email", vertical="generic",
    )
    assert m is None


# ── CRM stage-change router ────────────────────────────────────────────


def test_handle_stage_change_exits_on_won(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, handle_stage_change, get_enrollment,
    )
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="won@example.com",
        crm_deal_id="deal_abc",
    )
    res = handle_stage_change("deal_abc", "Closed Won")
    assert res["action"] == "exited"
    assert res["applied"] == 1
    assert get_enrollment(e["id"])["status"] == "exited"


def test_handle_stage_change_pauses_on_qualified(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, handle_stage_change, get_enrollment,
    )
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="q@example.com",
        crm_deal_id="deal_q",
    )
    res = handle_stage_change("deal_q", "Qualified")
    assert res["action"] == "paused"
    assert res["applied"] == 1
    row = get_enrollment(e["id"])
    assert row["status"] == "paused"
    assert "stage:" in (row["pause_reason"] or "")


def test_handle_stage_change_unknown_is_noop(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, handle_stage_change, get_enrollment,
    )
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="n@example.com",
        crm_deal_id="deal_n",
    )
    res = handle_stage_change("deal_n", "Brand New Stage")
    assert res["action"] == "noop"
    assert res["applied"] == 0
    assert get_enrollment(e["id"])["status"] == "active"
