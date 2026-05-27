"""Tests for the canonical leads.ingest_lead orchestrator and the legacy
lead_pipeline.ingest_lead shim.

A FakeProvider replaces the real CRM at the provider boundary so tests don't
hit Attio. We assert the dedupe path, vertical→cadence mapping, deal/person
linkage on the resulting enrollment, and the legacy return shape.
"""

from __future__ import annotations

import pytest

from roost.extras.crm.services.base import Deal, Person


class FakeProvider:
    """Minimal CRM stand-in for ingest_lead tests."""
    name = "fake"

    def __init__(self, existing: dict[str, Person] | None = None):
        # email or phone -> Person
        self.existing = existing or {}
        self.created_people: list[dict] = []
        self.created_deals: list[dict] = []
        self.notes: list[dict] = []
        self.updates: list[dict] = []

    def find_person(self, *, email=None, phone=None):
        if email and email in self.existing:
            return self.existing[email]
        if phone and phone in self.existing:
            return self.existing[phone]
        return None

    def create_person(self, *, name=None, emails=None, phones=None,
                      organization_id=None, **_):
        pid = f"person_{len(self.created_people) + 1}"
        self.created_people.append({
            "id": pid, "name": name, "emails": emails or [], "phones": phones or [],
        })
        return Person(id=pid, name=name, emails=emails or [], phones=phones or [])

    def update_person(self, person_id, **fields):
        self.updates.append({"id": person_id, **fields})
        return Person(id=person_id, name=fields.get("name"))

    def create_deal(self, *, name, stage=None, value=None, person_id=None, **_):
        did = f"deal_{len(self.created_deals) + 1}"
        self.created_deals.append({
            "id": did, "name": name, "stage": stage, "person_id": person_id,
        })
        return Deal(id=did, name=name, stage=stage, person_id=person_id)

    def append_note(self, *, person_id=None, deal_id=None, content="", title=""):
        self.notes.append({"person_id": person_id, "deal_id": deal_id,
                           "title": title, "content": content})
        return {"ok": True}


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
def patch_crm(monkeypatch):
    """Patch get_provider at its source module so the lazy imports in
    leads.ingest_lead / nurture._log_to_crm both see the fake."""
    fake = FakeProvider()
    monkeypatch.setattr(
        "roost.extras.crm.services.get_provider", lambda: fake
    )
    return fake


# ── ingest_lead happy paths ────────────────────────────────────────────


def test_ingest_lead_creates_person_deal_and_enrollment(
    clean_cadence_tables, patch_crm
):
    from roost.extras.lead_nurture.services.cadences import seed_library, get_enrollment
    from roost.extras.lead_nurture.services.leads import ingest_lead
    seed_library()

    res = ingest_lead(
        channel="web_form",
        email="new@example.com",
        name="New Lead",
        vertical="generic",
        mirror_local_task=False,
    )
    assert res["ok"] is True
    assert res["crm_person_id"] == "person_1"
    assert res["crm_deal_id"] == "deal_1"
    assert res["enrollment_id"]
    assert res["cadence_slug"] == "generic_b2b"
    assert len(patch_crm.created_people) == 1
    assert len(patch_crm.created_deals) == 1
    # Enrollment carries the CRM linkage forward
    enr = get_enrollment(res["enrollment_id"])
    assert enr["crm_person_id"] == "person_1"
    assert enr["crm_deal_id"] == "deal_1"


def test_ingest_lead_dedupes_existing_person(
    clean_cadence_tables, patch_crm
):
    from roost.extras.lead_nurture.services.cadences import seed_library
    from roost.extras.lead_nurture.services.leads import ingest_lead
    seed_library()
    patch_crm.existing["dup@example.com"] = Person(
        id="person_existing", name="Existing", emails=["dup@example.com"],
    )
    res = ingest_lead(
        channel="web_form", email="dup@example.com",
        vertical="generic", mirror_local_task=False,
    )
    assert res["ok"] is True
    assert res["crm_person_id"] == "person_existing"
    assert patch_crm.created_people == []  # never called create_person


def test_ingest_lead_picks_property_cadence_for_property_vertical(
    clean_cadence_tables, patch_crm
):
    from roost.extras.lead_nurture.services.cadences import seed_library
    from roost.extras.lead_nurture.services.leads import ingest_lead
    seed_library()
    res = ingest_lead(
        channel="whatsapp", phone="+6591234567",
        vertical="property", mirror_local_task=False,
    )
    assert res["cadence_slug"] == "property_buyer_intro"


def test_ingest_lead_requires_email_or_phone(clean_cadence_tables, patch_crm):
    from roost.extras.lead_nurture.services.leads import ingest_lead
    res = ingest_lead(channel="web_form")
    assert res["ok"] is False
    # Error message now also mentions telegram_chat_id as an accepted
    # identifier (added when Telegram became a first-class customer
    # channel). The "email or phone" substring still appears.
    assert "email" in res["errors"][0]
    assert "phone" in res["errors"][0]
    assert "required" in res["errors"][0]


def test_ingest_lead_unknown_cadence_returns_error(
    clean_cadence_tables, patch_crm
):
    from roost.extras.lead_nurture.services.cadences import seed_library
    from roost.extras.lead_nurture.services.leads import ingest_lead
    seed_library()
    res = ingest_lead(
        channel="web_form", email="x@example.com",
        cadence_slug="not_a_real_cadence", mirror_local_task=False,
    )
    assert res["ok"] is False
    assert any("cadence not found" in e for e in res["errors"])


# ── Legacy shim ────────────────────────────────────────────────────────


def test_legacy_lead_pipeline_returns_compat_shape(
    clean_cadence_tables, patch_crm
):
    """lead_pipeline.ingest_lead must still return contact_id / task_id /
    emails_scheduled keys for callers we haven't migrated."""
    from roost.extras.lead_nurture.services.cadences import seed_library
    from roost.extras.lead_nurture.services.lead_pipeline import ingest_lead as legacy_ingest
    seed_library()
    res = legacy_ingest({
        "email": "legacy@example.com",
        "name": "Legacy Lead",
        "org_name": "Acme",
        "risk_score": 72,
        "risk_level": "High",
        "risk_factors": ["a", "b"],
    })
    # New keys
    assert res["enrollment_id"]
    assert res["crm_person_id"] == "person_1"
    # Old keys still present (renamed: contact_id == crm_person_id, no inline emails)
    assert "contact_id" in res
    assert "task_id" in res
    assert res["emails_scheduled"] == []
