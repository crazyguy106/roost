"""Tests for the `wait_for_reply` cadence step.

A step `{type: wait_for_reply, day_offset: 2, timeout_days: 7}` is a gate:

* If `last_inbound_at > since` (since = previous step time, or
  `started_at` for the first step), the enrollment exits with
  `pause_reason='reply_received'`.
* If no reply yet and the timeout has not elapsed, `next_run_at` is
  pushed forward and the enrollment stays active.
* If the timeout has elapsed without reply, the engine advances to the
  next step.

The SMS inbound webhook also stamps `last_inbound_at` for matching
contacts so the gate can detect engagement across channels.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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


def _seed_cadence_with_wait(*, slug: str = "wfr_test", timeout_days: int = 7):
    """Create a 3-step cadence: message → wait_for_reply → message."""
    from roost.extras.lead_nurture.services.cadences import set_cadence
    from roost.services import response_templates as templates_svc

    for name in ("wfr_intro", "wfr_followup"):
        existing = templates_svc.get_template_by_name(name)
        if "error" in existing:
            templates_svc.create_template(
                name=name,
                subject=f"{name} subject",
                body=f"hello from {name}",
                channel="email",
                category="nurture",
            )

    return set_cadence(
        slug=slug,
        name="Wait-for-reply test",
        vertical="generic",
        steps=[
            {"day_offset": 0, "channel": "email", "template": "wfr_intro"},
            {
                "type": "wait_for_reply",
                "day_offset": 2,
                "timeout_days": timeout_days,
            },
            {"day_offset": 3, "channel": "email", "template": "wfr_followup"},
        ],
        source="library",
    )


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Engine behaviour ───────────────────────────────────────────────────


def test_wait_for_reply_exits_when_reply_seen(clean_cadence_tables):
    """If last_inbound_at > previous step time when the gate fires, exit cadence."""
    from roost.extras.lead_nurture.services.cadences import (
        enroll_lead, get_enrollment, update_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import advance_enrollment

    _seed_cadence_with_wait()
    e = enroll_lead(
        cadence_slug="wfr_test",
        contact_phone="+6591234567",
        next_run_at=_utc(datetime(2000, 1, 1, tzinfo=timezone.utc)),
    )
    # Skip past the first message step to the wait gate.
    update_enrollment(
        e["id"],
        started_at=_utc(datetime(2000, 1, 1, tzinfo=timezone.utc)),
        current_step=1,
        last_step_at=_utc(datetime(2000, 1, 1, 1, 0, tzinfo=timezone.utc)),
        last_inbound_at=_utc(datetime(2000, 1, 1, 2, 0, tzinfo=timezone.utc)),
        next_run_at=_utc(datetime(2000, 1, 3, tzinfo=timezone.utc)),
    )

    res = advance_enrollment(e["id"])
    assert res["action"] == "exited_replied"
    row = get_enrollment(e["id"])
    assert row["status"] == "exited"
    assert row["pause_reason"] == "reply_received"
    assert row["next_run_at"] is None


def test_wait_for_reply_defers_when_no_reply_within_timeout(clean_cadence_tables):
    """No reply and timeout not elapsed → push next_run_at to deadline, stay active.

    Deadline must be anchored to the step's *original* scheduled fire time
    (started_at + day_offset), not to next_run_at — otherwise re-entries
    push the deadline forward forever (see regression test below).
    """
    from roost.extras.lead_nurture.services.cadences import (
        enroll_lead, get_enrollment, update_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import advance_enrollment

    _seed_cadence_with_wait(timeout_days=7)
    # started_at = now → original_fire_at = now + 2d → deadline = now + 9d.
    now = datetime.now(timezone.utc)
    e = enroll_lead(
        cadence_slug="wfr_test",
        contact_phone="+6591234567",
        next_run_at=_utc(now - timedelta(hours=1)),
    )
    update_enrollment(
        e["id"],
        started_at=_utc(now),
        current_step=1,
        last_step_at=_utc(now - timedelta(days=2)),
        next_run_at=_utc(now - timedelta(hours=1)),
    )

    res = advance_enrollment(e["id"])
    assert res["action"] == "waiting_for_reply"
    row = get_enrollment(e["id"])
    assert row["status"] == "active"
    assert row["current_step"] == 1
    assert row["pause_reason"].startswith("waiting_for_reply:")
    deadline = datetime.fromisoformat(row["next_run_at"].replace("Z", "+00:00"))
    # original_fire_at = started_at + 2d = now + 2d; deadline = +9d from now.
    assert deadline > now + timedelta(days=8)
    assert deadline < now + timedelta(days=10)


def test_wait_for_reply_deadline_does_not_shift_on_re_entry(clean_cadence_tables):
    """REGRESSION: deferring twice must not push the deadline further out.

    If the deadline were anchored to `next_run_at` (which the gate
    itself updates on each defer), every re-entry would slide the
    deadline forward by another timeout_days. Anchor must be
    `started_at + day_offset`.
    """
    from roost.extras.lead_nurture.services.cadences import (
        enroll_lead, get_enrollment, update_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import advance_enrollment

    _seed_cadence_with_wait(timeout_days=3)
    started = datetime.now(timezone.utc) - timedelta(days=1)
    e = enroll_lead(
        cadence_slug="wfr_test",
        contact_phone="+6591234567",
        next_run_at=_utc(started + timedelta(days=2)),  # immediately due conceptually
    )
    update_enrollment(
        e["id"],
        started_at=_utc(started),
        current_step=1,
        last_step_at=_utc(started + timedelta(hours=1)),
        next_run_at=_utc(datetime.now(timezone.utc) - timedelta(minutes=5)),
    )

    res1 = advance_enrollment(e["id"])
    assert res1["action"] == "waiting_for_reply"
    deadline1 = res1["deadline"]

    # Pretend the gate fired again (e.g. an operator manually reset
    # next_run_at, or the deadline was reached and another tick re-entered).
    update_enrollment(e["id"], next_run_at=deadline1)
    res2 = advance_enrollment(e["id"])
    assert res2["action"] == "waiting_for_reply"
    deadline2 = res2["deadline"]

    assert deadline1 == deadline2, (
        "deadline must be anchored to started_at+day_offset, not to next_run_at"
    )
    assert get_enrollment(e["id"])["current_step"] == 1  # still at the gate


def test_wait_for_reply_timeout_elapsed_advances(clean_cadence_tables):
    """timeout elapsed without reply → advance to next message step."""
    from roost.extras.lead_nurture.services.cadences import (
        enroll_lead, get_enrollment, update_enrollment,
    )
    from roost.extras.lead_nurture.services.nurture import advance_enrollment

    _seed_cadence_with_wait(timeout_days=1)
    # started_at sits 30 days ago → original_fire_at = 28d ago → deadline = 27d ago.
    started = datetime.now(timezone.utc) - timedelta(days=30)
    e = enroll_lead(
        cadence_slug="wfr_test",
        contact_phone="+6591234567",
        next_run_at=_utc(started + timedelta(days=2)),
    )
    update_enrollment(
        e["id"],
        started_at=_utc(started),
        current_step=1,
        last_step_at=_utc(started),
        next_run_at=_utc(started + timedelta(days=2)),
    )

    res = advance_enrollment(e["id"])
    assert res["action"] == "wait_timeout"
    row = get_enrollment(e["id"])
    assert row["current_step"] == 2
    assert row["status"] == "active"


# ── Loader validation ──────────────────────────────────────────────────


def test_loader_accepts_wait_for_reply_step():
    from roost.extras.lead_nurture.services.cadences.loader import validate_cadence

    cfg = {
        "slug": "x",
        "name": "X",
        "steps": [
            {"day_offset": 0, "channel": "email", "template": "t1"},
            {"type": "wait_for_reply", "day_offset": 2, "timeout_days": 5},
            {"day_offset": 5, "channel": "email", "template": "t2"},
        ],
        "templates": [
            {"name": "t1", "body": "hi"},
            {"name": "t2", "body": "follow"},
        ],
    }
    assert validate_cadence(cfg) == []


def test_loader_rejects_negative_timeout_days():
    from roost.extras.lead_nurture.services.cadences.loader import validate_cadence

    cfg = {
        "slug": "x",
        "name": "X",
        "steps": [{"type": "wait_for_reply", "day_offset": 2, "timeout_days": -1}],
    }
    errs = validate_cadence(cfg)
    assert any("timeout_days" in e for e in errs)


# ── mark_inbound_for_contact ──────────────────────────────────────────


def test_mark_inbound_for_contact_stamps_active_enrollments(clean_cadence_tables):
    """Helper should update active/paused enrollments matching phone or email."""
    from roost.extras.lead_nurture.services.cadences import (
        enroll_lead, get_enrollment, mark_inbound_for_contact, update_enrollment,
    )

    _seed_cadence_with_wait()
    e1 = enroll_lead(cadence_slug="wfr_test", contact_phone="+6591234567")
    e2 = enroll_lead(cadence_slug="wfr_test", contact_email="x@example.com")
    # A completed enrollment (distinct contact) must NOT be touched. It uses a
    # different phone because enroll_lead now dedupes by contact — a second
    # enroll on +6591234567 would reuse e1 rather than create a separate row.
    e3 = enroll_lead(cadence_slug="wfr_test", contact_phone="+6599999999")
    update_enrollment(e3["id"], status="completed")

    n_phone = mark_inbound_for_contact(phone="+6591234567")
    n_email = mark_inbound_for_contact(email="x@example.com")
    n_done = mark_inbound_for_contact(phone="+6599999999")

    assert n_phone == 1  # e1
    assert n_email == 1  # e2
    assert n_done == 0   # e3 is completed → not stamped
    assert get_enrollment(e1["id"])["last_inbound_at"] is not None
    assert get_enrollment(e2["id"])["last_inbound_at"] is not None
    assert get_enrollment(e3["id"])["last_inbound_at"] is None


def test_mark_inbound_no_identifiers_is_noop():
    from roost.extras.lead_nurture.services.cadences import mark_inbound_for_contact
    assert mark_inbound_for_contact() == 0
