"""Unit tests for the LocalProvider CRM backend.

LocalProvider wraps Roost's own contacts/communications/notes tables so
the CRM API works even when no external CRM is connected. These tests
exercise it against the real SQLite (no mocks) — same pattern as the
other DB tests in the suite.
"""

from __future__ import annotations

import pytest

from roost.database import db_connection
from roost.extras.crm.services.base import (
    CrmConfigError,
    CrmNotFoundError,
    CrmProvider,
    Deal,
    Org,
    Person,
)
from roost.extras.crm.services.local import LocalProvider


@pytest.fixture(autouse=True)
def clean_crm_tables():
    """Wipe the contacts / entities / communications / notes between tests."""
    with db_connection() as conn:
        for table in ("communications", "notes", "contact_identifiers",
                      "contact_entities", "contacts", "entities"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()
    yield
    with db_connection() as conn:
        for table in ("communications", "notes", "contact_identifiers",
                      "contact_entities", "contacts", "entities"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                pass
        conn.commit()


@pytest.fixture
def crm():
    return LocalProvider()


# ── Health ──────────────────────────────────────────────────────────


def test_local_provider_subclasses_base(crm):
    assert isinstance(crm, CrmProvider)
    assert crm.name == "local"


def test_test_connection_returns_ok(crm):
    result = crm.test_connection()
    assert result["ok"] is True
    assert "Local" in result["detail"]


# ── Person CRUD ─────────────────────────────────────────────────────


def test_create_person_returns_person_with_id(crm):
    p = crm.create_person(name="Jane Doe", emails=["jane@example.com"])
    assert isinstance(p, Person)
    assert p.id  # non-empty string
    assert p.name == "Jane Doe"
    assert "jane@example.com" in p.emails


def test_get_person_round_trip(crm):
    p = crm.create_person(name="Bob", emails=["bob@example.com"])
    fetched = crm.get_person(p.id)
    assert fetched.id == p.id
    assert fetched.name == "Bob"


def test_get_person_missing_raises_not_found(crm):
    with pytest.raises(CrmNotFoundError):
        crm.get_person("999999")


def test_find_person_by_email(crm):
    crm.create_person(name="Carol", emails=["carol@example.com"])
    found = crm.find_person(email="carol@example.com")
    assert found is not None
    assert found.name == "Carol"


def test_find_person_missing_returns_none(crm):
    assert crm.find_person(email="nobody@example.com") is None


def test_find_person_by_phone(crm):
    crm.create_person(name="Dan", phones=["+6591234567"])
    found = crm.find_person(phone="+6591234567")
    assert found is not None
    assert found.name == "Dan"


def test_update_person_changes_name(crm):
    p = crm.create_person(name="Old Name", emails=["upd@example.com"])
    updated = crm.update_person(p.id, name="New Name")
    assert updated.name == "New Name"


def test_update_person_missing_raises_not_found(crm):
    with pytest.raises(CrmNotFoundError):
        crm.update_person("999999", name="Whatever")


def test_search_people_matches_by_substring(crm):
    crm.create_person(name="Alice Anderson", emails=["alice@example.com"])
    crm.create_person(name="Bob Brown", emails=["bob@example.com"])
    results = crm.search_people("alice")
    names = [r.name for r in results]
    assert "Alice Anderson" in names
    assert "Bob Brown" not in names


# ── Organization ────────────────────────────────────────────────────


def test_create_org_round_trip(crm):
    o = crm.create_org(name="Acme Co")
    assert isinstance(o, Org)
    assert o.name == "Acme Co"
    assert o.id


def test_find_org_by_name(crm):
    crm.create_org(name="WidgetCo")
    found = crm.find_org(name="WidgetCo")
    assert found is not None
    assert found.name == "WidgetCo"


def test_find_org_missing_returns_none(crm):
    assert crm.find_org(name="DoesNotExist") is None


# ── Deal — local has no native deal model ─────────────────────────


def test_list_deals_returns_empty(crm):
    """LocalProvider has no deal model — list returns []."""
    assert crm.list_deals() == []


def test_create_deal_raises_not_implemented(crm):
    with pytest.raises(NotImplementedError):
        crm.create_deal(name="Sample deal")


def test_move_deal_stage_raises_not_implemented(crm):
    with pytest.raises(NotImplementedError):
        crm.move_deal_stage("1", "won")


# ── Notes & communications ─────────────────────────────────────────


def test_append_note_returns_id(crm):
    p = crm.create_person(name="Eve", emails=["eve@example.com"])
    note_id = crm.append_note(person_id=p.id, content="Met at conference", title="Intro")
    assert note_id  # non-empty string


def test_log_communication_returns_id(crm):
    p = crm.create_person(name="Frank", emails=["frank@example.com"])
    comm_id = crm.log_communication(
        person_id=p.id,
        channel="email",
        direction="outbound",
        content="Thanks for the chat",
        subject="Follow-up",
    )
    assert comm_id


# ── Custom fields ───────────────────────────────────────────────────


def test_set_custom_field_on_person_does_not_raise(crm):
    p = crm.create_person(name="Grace", emails=["grace@example.com"])
    crm.set_custom_field(person_id=p.id, key="vip", value="true")
    # Side-effect verified by re-reading the contact; the field is stored
    # in notes as "[vip] true (...)" per LocalProvider's impl.
    fresh = crm.get_person(p.id)
    raw_notes = fresh.raw.get("notes", "") or ""
    assert "[vip] true" in raw_notes


def test_set_custom_field_without_person_id_raises(crm):
    with pytest.raises(NotImplementedError):
        crm.set_custom_field(deal_id="1", key="x", value="y")
