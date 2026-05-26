"""Tests for the /leads dashboard pipeline helpers + JSON endpoints.

The helpers in `extras/lead_nurture/web/pages.py` are pure functions over
the SQLite enrollments table, so we drive them directly (no HTTP) for the
column-grouping logic, then add a single TestClient round-trip to confirm
`/api/leads/pipeline` and `/api/leads/{id}` are mounted.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


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


def _seed():
    from roost.extras.lead_nurture.services.cadences import seed_library
    seed_library()


def _enroll(*, name: str, phone: str = "", channel: str = "whatsapp", fields: dict | None = None):
    from roost.extras.lead_nurture.services.cadences import enroll_lead
    return enroll_lead(
        cadence_slug="property_buyer_intro",
        contact_name=name,
        contact_phone=phone,
        channel=channel,
        fields=fields or {},
    )


def _update(enrollment_id: int, **fields):
    from roost.extras.lead_nurture.services.cadences import update_enrollment
    return update_enrollment(enrollment_id, **fields)


# ── Column grouping ────────────────────────────────────────────────────


def test_new_enrollment_lands_in_new_column(clean_cadence_tables):
    _seed()
    enr = _enroll(name="Alice")
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    new_col = next(c for c in pipeline["columns"] if c["key"] == "new")
    ids = [l["id"] for l in new_col["leads"]]
    assert enr["id"] in ids


def test_qualifying_enrollment_lands_in_qualifying_column(clean_cadence_tables):
    _seed()
    enr = _enroll(name="Bob", phone="+6591234567")
    _update(
        enr["id"],
        status="paused",
        pause_reason="qualifying",
        fields={
            "_qualify_status": "in_progress",
            "_qualify_idx": 0,
            "_qualify_channel": "whatsapp",
            "_qualify_identifier": "+6591234567",
            "_qualify_answers": {},
            "_qualify_cadence_slug": "property_buyer_intro",
        },
    )
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    qual_col = next(c for c in pipeline["columns"] if c["key"] == "qualifying")
    assert enr["id"] in [l["id"] for l in qual_col["leads"]]
    # Progress should render as "1/3"
    lead = next(l for l in qual_col["leads"] if l["id"] == enr["id"])
    assert lead["qualifying_progress"] == "1/3"


def test_hot_label_lands_in_hot_column(clean_cadence_tables):
    _seed()
    enr = _enroll(name="Charlie", phone="+6598765432")
    _update(
        enr["id"],
        status="active",
        fields={
            "_qualify_status": "done",
            "_qualify_label": "hot",
            "_qualify_score": 0.95,
        },
    )
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    hot_col = next(c for c in pipeline["columns"] if c["key"] == "hot")
    assert enr["id"] in [l["id"] for l in hot_col["leads"]]
    assert pipeline["totals"]["hot"] >= 1


def test_lead_urgency_hot_from_ai_cdr_lands_in_hot_column(clean_cadence_tables):
    """Even without a finished qualification, an AI-CDR hot tag wins."""
    _seed()
    enr = _enroll(name="Dana")
    _update(enr["id"], fields={"_lead_urgency": "hot", "_lead_intent": "buy_now"})
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    hot_col = next(c for c in pipeline["columns"] if c["key"] == "hot")
    assert enr["id"] in [l["id"] for l in hot_col["leads"]]


def test_warm_label_lands_in_nurturing_column(clean_cadence_tables):
    _seed()
    enr = _enroll(name="Erin")
    _update(enr["id"], fields={"_qualify_label": "warm", "_qualify_status": "done"})
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    nur_col = next(c for c in pipeline["columns"] if c["key"] == "nurturing")
    assert enr["id"] in [l["id"] for l in nur_col["leads"]]


def test_exited_enrollment_lands_in_closed_column(clean_cadence_tables):
    _seed()
    enr = _enroll(name="Frank")
    _update(enr["id"], status="exited", pause_reason="qualified_cold")
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    closed_col = next(c for c in pipeline["columns"] if c["key"] == "closed")
    assert enr["id"] in [l["id"] for l in closed_col["leads"]]


def test_paused_non_qualifying_lands_in_paused_column(clean_cadence_tables):
    """Stage-change pauses (e.g. 'meeting booked') go to the Paused column,
    not Qualifying."""
    _seed()
    enr = _enroll(name="Gina")
    _update(enr["id"], status="paused", pause_reason="stage:meeting booked")
    from roost.extras.lead_nurture.web.pages import build_pipeline
    pipeline = build_pipeline()
    paused_col = next(c for c in pipeline["columns"] if c["key"] == "paused")
    assert enr["id"] in [l["id"] for l in paused_col["leads"]]


# ── Detail payload ─────────────────────────────────────────────────────


def test_enrollment_detail_includes_qualification_qa(clean_cadence_tables):
    _seed()
    enr = _enroll(name="Henry", phone="+6590000000")
    _update(
        enr["id"],
        fields={
            "_qualify_status": "done",
            "_qualify_label": "warm",
            "_qualify_score": 0.55,
            "_qualify_answers": {
                "timeline": "next month",
                "budget_band": "1.5M",
                "mortgage_status": "looking into",
            },
        },
    )
    from roost.extras.lead_nurture.web.pages import enrollment_detail
    d = enrollment_detail(enr["id"])
    assert d is not None
    assert d["card"]["name"] == "Henry"
    # Q&A list should match the property_buyer_intro question pack length
    assert len(d["qualification"]["qa"]) == 3
    answers = {q["key"]: q["answer"] for q in d["qualification"]["qa"]}
    assert answers["timeline"] == "next month"
    assert answers["mortgage_status"] == "looking into"
    assert d["qualification"]["label"] == "warm"


def test_enrollment_detail_returns_none_for_missing_id(clean_cadence_tables):
    from roost.extras.lead_nurture.web.pages import enrollment_detail
    assert enrollment_detail(999_999) is None


# ── HTTP round-trip ────────────────────────────────────────────────────


def _build_app():
    from roost.extras.lead_nurture.web.api_leads import router as leads_router
    app = FastAPI()
    app.include_router(leads_router)
    return app


def test_pipeline_endpoint_returns_six_columns(clean_cadence_tables):
    _seed()
    _enroll(name="Iris")
    client = TestClient(_build_app())
    r = client.get("/api/leads/pipeline")
    assert r.status_code == 200
    data = r.json()
    assert [c["key"] for c in data["columns"]] == [
        "new", "qualifying", "hot", "nurturing", "paused", "closed",
    ]
    assert data["totals"]["total"] >= 1


def test_lead_detail_endpoint_404s_when_missing(clean_cadence_tables):
    client = TestClient(_build_app())
    r = client.get("/api/leads/999999")
    assert r.status_code == 404
