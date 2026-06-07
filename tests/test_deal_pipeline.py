"""Lead → CRM deal: configurable pipeline stages + dedup.

`ingest_lead` opens a deal at the configured `new` stage, promotes it to
`hot` when a message scores hot (using stage names that exist in the
workspace's pipeline — not the old hardcoded "Hot Lead"), and reuses a
contact's existing deal instead of spawning duplicates on every inbound.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from roost.database import get_connection

_PHONES = ("+6590000010", "+6590000011", "+6590000012", "+6590000013")


class FakeProvider:
    name = "fake"

    def __init__(self):
        self.created: list = []        # (name, stage)
        self.moves: list = []          # (deal_id, stage)
        self.existing_deals: list = []

    def find_person(self, **k):
        return None

    def create_person(self, **k):
        return SimpleNamespace(id="p1", name=k.get("name") or "")

    def update_person(self, *a, **k):
        return SimpleNamespace(id="p1", name="")

    def append_note(self, **k):
        return "note1"

    def set_custom_field(self, **k):
        return None

    def set_ai_attribute(self, **k):
        return None

    def list_deals(self, *, person_id=None, stage=None, limit=50):
        return list(self.existing_deals)

    def create_deal(self, *, name, stage=None, value=None, person_id=None, **k):
        self.created.append((name, stage))
        return SimpleNamespace(id="d1", name=name, stage=stage)

    def move_deal_stage(self, deal_id, stage):
        self.moves.append((deal_id, stage))
        return SimpleNamespace(id=deal_id, stage=stage)


@pytest.fixture
def fake_crm(monkeypatch):
    fp = FakeProvider()
    import roost.extras.crm.services as crm
    monkeypatch.setattr(crm, "get_provider", lambda *a, **k: fp)
    from roost.extras.lead_nurture.services.cadences import seed_library
    seed_library()

    def _wipe():
        c = get_connection()
        c.execute(
            "DELETE FROM nurture_enrollments WHERE contact_phone IN (?,?,?,?)", _PHONES
        )
        c.commit()
        c.close()
    _wipe()
    yield fp
    _wipe()


def _ingest(monkeypatch, *, urgency: str, phone: str):
    from roost.extras.lead_nurture.services import leads
    monkeypatch.setattr(
        leads, "_classify_if_text",
        lambda txt: {"urgency": urgency, "intent": "enquiry",
                     "confidence": 0.8, "reasoning": "", "extracted_fields": {}},
    )
    return leads.ingest_lead(
        channel="whatsapp", phone=phone, name="Test", message_text="hi",
        vertical="financial_advisor", qualifying_identifier=phone,
        mirror_local_task=False,
    )


def test_new_lead_opens_deal_at_new_stage(fake_crm, monkeypatch):
    _ingest(monkeypatch, urgency="cold", phone=_PHONES[0])
    assert fake_crm.created == [("Lead: Test", "Lead")]
    assert fake_crm.moves == []


def test_hot_lead_opens_deal_at_hot_stage(fake_crm, monkeypatch):
    _ingest(monkeypatch, urgency="hot", phone=_PHONES[1])
    # configured 'hot' stage — "In Progress", NOT the old invalid "Hot Lead"
    assert fake_crm.created == [("Lead: Test", "In Progress")]


def test_returning_lead_reuses_deal_no_duplicate(fake_crm, monkeypatch):
    fake_crm.existing_deals = [SimpleNamespace(id="existing", name="x", stage="Lead")]
    _ingest(monkeypatch, urgency="cold", phone=_PHONES[2])
    assert fake_crm.created == []          # no duplicate deal
    assert fake_crm.moves == []            # cold → not promoted


def test_returning_hot_lead_promotes_existing_deal(fake_crm, monkeypatch):
    fake_crm.existing_deals = [SimpleNamespace(id="existing", name="x", stage="Lead")]
    _ingest(monkeypatch, urgency="hot", phone=_PHONES[3])
    assert fake_crm.created == []
    assert fake_crm.moves == [("existing", "In Progress")]
