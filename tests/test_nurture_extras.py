"""Extra nurture-engine tests:
  * `_step_run_at` time-math (day_offset, minute_offset, hour+tz)
  * Last-step → completed transition (full multi-tick run)
"""

from __future__ import annotations

from datetime import datetime, timezone

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
def stub_dispatch(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n
    calls: list[dict] = []

    def fake(*, enrollment, message, when_utc):
        calls.append({"enrollment_id": enrollment["id"]})
        return {"ok": True, "channel": message["channel"], "ref": "ref",
                "detail": "stub"}

    monkeypatch.setattr(n, "_dispatch_send", fake)
    monkeypatch.setattr(n, "_notify_telegram", lambda *a, **kw: None)
    return calls


# ── _step_run_at ───────────────────────────────────────────────────────


def test_step_run_at_day_offset_only():
    from roost.extras.lead_nurture.services.nurture import _step_run_at
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    out = _step_run_at({"day_offset": 3}, base)
    assert out == datetime(2026, 1, 4, 12, 0, tzinfo=timezone.utc)


def test_step_run_at_minute_offset_overrides_hour():
    from roost.extras.lead_nurture.services.nurture import _step_run_at
    base = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    out = _step_run_at(
        {"day_offset": 0, "minute_offset": 5, "hour": 9}, base,
    )
    # minute_offset wins
    assert out == datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)


def test_step_run_at_hour_in_singapore_tz():
    from roost.extras.lead_nurture.services.nurture import _step_run_at
    # base is 2026-01-01 00:00 UTC == 08:00 Singapore on the 1st.
    # day_offset=2 + hour=9 SGT → 09:00 SGT on the 3rd → 01:00 UTC on the 3rd.
    base = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    out = _step_run_at(
        {"day_offset": 2, "hour": 9, "tz": "Asia/Singapore"}, base,
    )
    assert out == datetime(2026, 1, 3, 1, 0, tzinfo=timezone.utc)


# ── Multi-tick → completed ─────────────────────────────────────────────


def test_full_run_terminates_in_completed(clean_cadence_tables, stub_dispatch):
    """Tick a preapproved enrollment through every step until the engine
    marks it `completed` and stops dispatching."""
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, enroll_lead, create_preapproval,
        get_cadence_by_slug, get_enrollment, update_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import tick
    seed_library()
    create_preapproval(cadence_slug="generic_b2b")  # auto-send all
    cad = get_cadence_by_slug("generic_b2b")
    n_steps = len(cad["steps"])
    assert n_steps >= 2

    e = enroll_lead(
        cadence_slug="generic_b2b",
        contact_email="loop@example.com",
        next_run_at="2000-01-01T00:00:00Z",
    )

    sent_total = 0
    for i in range(n_steps + 2):  # extra ticks should be safe no-ops
        # Force next_run_at into the past so the step is due immediately
        update_enrollment(e["id"], next_run_at="2000-01-01T00:00:00Z")
        row = get_enrollment(e["id"])
        if row["status"] != "active":
            break
        res = tick()
        sent_total += res.get("sent", 0)

    final = get_enrollment(e["id"])
    assert final["status"] == "completed"
    # Sent exactly once per step
    assert sent_total == n_steps
    assert len(stub_dispatch) == n_steps
