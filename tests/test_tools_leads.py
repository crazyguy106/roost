"""Tests for the MCP tool wrappers in `roost.extras.lead_nurture.mcp.tools_leads`.

FastMCP's `@mcp.tool()` returns a `FunctionTool` whose `.fn` attribute is the
original Python function. We call `.fn(...)` directly to exercise the wrapper
logic (JSON-string parsing, error envelopes, return shape) without standing
up the full MCP transport.
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


# ── lead_ingest ───────────────────────────────────────────────────────


def test_lead_ingest_rejects_bad_fields_json():
    from roost.extras.lead_nurture.mcp.tools_leads import lead_ingest
    out = lead_ingest.fn(channel="web_form", email="x@example.com",
                         fields_json="{not json")
    assert out["ok"] is False
    assert any("fields_json" in e for e in out["errors"])


def test_lead_ingest_passes_through_to_service(monkeypatch, clean_cadence_tables):
    """The wrapper should call leads.ingest_lead with parsed fields."""
    from roost.extras.lead_nurture.mcp.tools_leads import lead_ingest
    captured: dict = {}

    def fake_ingest(**kw):
        captured.update(kw)
        return {"ok": True, "crm_person_id": "p1", "enrollment_id": 99}

    monkeypatch.setattr("roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest)
    out = lead_ingest.fn(
        channel="whatsapp", phone="+6591234567",
        fields_json='{"first_name": "Sam"}',
        vertical="property", source="meta_ad",
    )
    assert out["ok"] is True
    assert captured["channel"] == "whatsapp"
    assert captured["phone"] == "+6591234567"
    assert captured["vertical"] == "property"
    assert captured["fields"] == {"first_name": "Sam"}


# ── cadence_enroll ────────────────────────────────────────────────────


def test_cadence_enroll_rejects_bad_fields_json():
    from roost.extras.lead_nurture.mcp.tools_leads import cadence_enroll
    out = cadence_enroll.fn(cadence_slug="generic_b2b",
                            fields_json="{nope")
    assert "error" in out
    assert "fields_json" in out["error"]


def test_cadence_enroll_unknown_slug_returns_error_envelope(
    clean_cadence_tables,
):
    from roost.extras.lead_nurture.services.cadences import seed_library
    from roost.extras.lead_nurture.mcp.tools_leads import cadence_enroll
    seed_library()
    out = cadence_enroll.fn(
        cadence_slug="nope_does_not_exist",
        contact_email="x@example.com",
    )
    assert "error" in out
    assert "cadence not found" in out["error"]


def test_cadence_enroll_happy_returns_enrollment(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import seed_library
    from roost.extras.lead_nurture.mcp.tools_leads import cadence_enroll
    seed_library()
    out = cadence_enroll.fn(
        cadence_slug="generic_b2b",
        contact_email="ok@example.com",
    )
    assert "id" in out
    assert out["status"] == "active"


# ── cadence_list_enrollments / cadence_list ───────────────────────────


def test_cadence_list_enrollments_returns_count_and_rows(clean_cadence_tables):
    from roost.extras.lead_nurture.services.cadences import seed_library, enroll_lead
    from roost.extras.lead_nurture.mcp.tools_leads import cadence_list_enrollments
    seed_library()
    enroll_lead(cadence_slug="generic_b2b", contact_email="a@example.com")
    enroll_lead(cadence_slug="generic_b2b", contact_email="b@example.com")
    out = cadence_list_enrollments.fn()
    assert out["count"] >= 2
    assert isinstance(out["enrollments"], list)


def test_cadence_get_unknown_returns_error():
    from roost.extras.lead_nurture.mcp.tools_leads import cadence_get
    out = cadence_get.fn(slug="not_real")
    assert "error" in out


# ── nurture_tick ──────────────────────────────────────────────────────


def test_nurture_tick_returns_summary(monkeypatch, clean_cadence_tables):
    """Wrapper should pass through to nurture.tick and return its dict."""
    from roost.extras.lead_nurture.mcp.tools_leads import nurture_tick
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.nurture.tick",
        lambda max_per_tick=50: {
            "ticked_at": "now", "due": 0, "sent": 0,
            "held": 0, "completed": 0, "errors": 0,
        },
    )
    out = nurture_tick.fn()
    assert "due" in out and "sent" in out
