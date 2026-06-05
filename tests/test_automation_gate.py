"""Automation gate: global pause switch + per-contact recency window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from roost.extras.lead_nurture.services import gating


def _now() -> datetime:
    return datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)


def _ago(hours: float) -> str:
    return (_now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _patch(monkeypatch, *, paused=False, window=24, last_activity=None):
    smap = {"automations_paused": paused, "auto_engage_window_hours": window}
    monkeypatch.setattr(gating._settings, "get", lambda k, d=None: smap.get(k, d))
    monkeypatch.setattr(gating._store, "last_activity_at", lambda **kw: last_activity)


def test_paused_blocks_everything(monkeypatch):
    _patch(monkeypatch, paused=True, last_activity=None)
    assert gating.automation_gate(phone="+6591234567", now=_now()) == {
        "proceed": False, "reason": "paused",
    }


def test_new_contact_proceeds(monkeypatch):
    _patch(monkeypatch, window=24, last_activity=None)
    g = gating.automation_gate(phone="+6591234567", now=_now())
    assert g["proceed"] is True and g["reason"] == "ok"


def test_recent_contact_proceeds(monkeypatch):
    _patch(monkeypatch, window=24, last_activity=_ago(2))
    assert gating.automation_gate(phone="+6591234567", now=_now())["proceed"] is True


def test_dormant_contact_gated(monkeypatch):
    _patch(monkeypatch, window=24, last_activity=_ago(30))
    g = gating.automation_gate(phone="+6591234567", now=_now())
    assert g["proceed"] is False
    assert g["reason"] == "dormant"
    assert g["dormant_hours"] == 30.0


def test_window_zero_disables_gate(monkeypatch):
    _patch(monkeypatch, window=0, last_activity=_ago(100))
    assert gating.automation_gate(phone="+6591234567", now=_now())["proceed"] is True


def test_pause_takes_precedence_over_window(monkeypatch):
    _patch(monkeypatch, paused=True, window=24, last_activity=_ago(1))
    assert gating.automation_gate(phone="+6591234567", now=_now())["reason"] == "paused"


def test_paused_via_live_sentinel_file(monkeypatch):
    """The data-dir sentinel file pauses even when the settings flag is off."""
    _patch(monkeypatch, paused=False, window=24, last_activity=None)
    from roost.config import DATABASE_PATH
    sentinel = Path(DATABASE_PATH).parent / "automations_paused"
    try:
        sentinel.write_text("")
        assert gating.is_paused() is True
        assert gating.automation_gate(phone="+6591234567", now=_now()) == {
            "proceed": False, "reason": "paused",
        }
    finally:
        sentinel.unlink(missing_ok=True)
    assert gating.is_paused() is False  # removed → resumed


def test_last_activity_at_reads_enrollment():
    """Integration: store.last_activity_at reflects mark_inbound_for_contact."""
    from roost.database import get_connection
    from roost.extras.lead_nurture.services.cadences import (
        enroll_lead, mark_inbound_for_contact, seed_library, store,
    )
    seed_library()
    phone = "+6590000099"

    def _wipe():
        conn = get_connection()
        conn.execute("DELETE FROM nurture_enrollments WHERE contact_phone=?", (phone,))
        conn.commit()
        conn.close()

    _wipe()
    try:
        enroll_lead(cadence_slug="generic_b2b", contact_phone=phone)
        assert store.last_activity_at(phone=phone) is None  # enrolled, not engaged
        mark_inbound_for_contact(phone=phone)
        assert store.last_activity_at(phone=phone) is not None
    finally:
        _wipe()
