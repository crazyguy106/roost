"""Tests for the nurture engine — tick path, approval gate, approve/skip.

External dispatch (Gmail, WhatsApp, Telegram) is mocked at the
`_dispatch_send` boundary so tests never reach the network.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def clean_cadence_tables():
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


@pytest.fixture
def stub_dispatch(monkeypatch):
    """Replace _dispatch_send with a recording stub."""
    from roost.extras.lead_nurture.services import nurture as n
    calls: list[dict] = []

    def fake(*, enrollment, message, when_utc):
        calls.append({
            "enrollment_id": enrollment["id"],
            "channel": message["channel"],
            "subject": message.get("subject"),
        })
        return {"ok": True, "channel": message["channel"],
                "ref": "stub-ref", "detail": "stub"}

    monkeypatch.setattr(n, "_dispatch_send", fake)
    # Also silence the Telegram notifier
    monkeypatch.setattr(n, "_notify_telegram", lambda *a, **kw: None)
    return calls


# ── Tick — held for approval (default) ─────────────────────────────────


def test_tick_holds_when_no_preapproval(clean_cadence_tables, stub_dispatch):
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead, get_enrollment
    from roost.extras.lead_nurture.services.nurture import tick
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="hold@example.com",
        next_run_at="2000-01-01T00:00:00Z",  # immediately due
    )
    res = tick()
    assert res["due"] >= 1
    assert res["held"] >= 1
    assert res["sent"] == 0
    row = get_enrollment(e["id"])
    assert row["status"] == "paused"
    assert row["pause_reason"].startswith("awaiting_approval")
    # No actual dispatch happened
    assert stub_dispatch == []


# ── Tick — preapproved auto-send ───────────────────────────────────────


def test_tick_sends_when_preapproved(clean_cadence_tables, stub_dispatch):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, create_preapproval, get_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import tick
    seed_library()
    create_preapproval(cadence_slug="generic_b2b")  # auto-send everything
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="auto@example.com",
        next_run_at="2000-01-01T00:00:00Z",
    )
    res = tick()
    assert res["sent"] == 1
    assert res["held"] == 0
    # Dispatched once for this enrollment
    assert any(c["enrollment_id"] == e["id"] for c in stub_dispatch)
    # Enrollment advanced to step 1, still active
    row = get_enrollment(e["id"])
    assert row["current_step"] == 1
    assert row["status"] == "active"


# ── Approve / skip ─────────────────────────────────────────────────────


def test_approve_pending_sends_and_advances(clean_cadence_tables, stub_dispatch):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, get_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import tick, approve_pending
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="approve@example.com",
        next_run_at="2000-01-01T00:00:00Z",
    )
    tick()  # holds for approval
    assert get_enrollment(e["id"])["status"] == "paused"

    res = approve_pending(e["id"])
    assert res.get("ok") is True
    assert res.get("action") in ("sent", "sent_after_approval")
    row = get_enrollment(e["id"])
    assert row["status"] == "active"
    assert row["current_step"] == 1
    assert any(c["enrollment_id"] == e["id"] for c in stub_dispatch)


def test_skip_pending_advances_without_sending(clean_cadence_tables, stub_dispatch):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, get_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import tick, skip_pending
    seed_library()
    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="skip@example.com",
        next_run_at="2000-01-01T00:00:00Z",
    )
    tick()
    res = skip_pending(e["id"], reason="not relevant")
    assert res.get("ok") is True
    row = get_enrollment(e["id"])
    assert row["current_step"] == 1
    assert row["status"] == "active"
    # No dispatch call recorded — the skip path bypasses sending
    assert stub_dispatch == []
