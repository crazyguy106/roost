"""meeting_memo: voice memo transcript → client's CRM record."""

from __future__ import annotations

from types import SimpleNamespace

from roost.extras.crm.services import meeting_memo as mm


def test_extract_memo_no_key(monkeypatch):
    monkeypatch.setattr("roost.config.GEMINI_API_KEY", "", raising=False)
    out = mm.extract_memo("Met Marcus, send proposal Friday.")
    assert out["client_name"] == ""
    assert out["next_actions"] == []
    assert "Marcus" in out["summary"]


def test_log_to_crm_no_client(monkeypatch):
    monkeypatch.setattr(mm, "extract_memo",
                        lambda t: {"client_name": "", "summary": "x", "next_actions": []})
    res = mm.log_to_crm("blah")
    assert res["ok"] is False
    assert res["reason"] == "no_client_identified"


def test_log_to_crm_writes_note(monkeypatch):
    monkeypatch.setattr(mm, "extract_memo", lambda t: {
        "client_name": "Marcus Tan",
        "summary": "Discussed retirement planning; wants to proceed.",
        "next_actions": ["Send proposal by Friday", "Review existing AIA policy"],
    })
    notes = []

    class FakeProvider:
        name = "fake"

        def search_people(self, q, limit=20):
            return [SimpleNamespace(id="p-marcus", name="Marcus Tan")] if "Marcus" in q else []

        def append_note(self, *, person_id, content, title=""):
            notes.append({"person_id": person_id, "title": title, "content": content})
            return "n1"

    import roost.extras.crm.services as crm
    monkeypatch.setattr(crm, "get_provider", lambda *a, **k: FakeProvider())

    res = mm.log_to_crm("Met Marcus Tan today...")
    assert res["ok"] is True
    assert res["client"] == "Marcus Tan"
    assert res["actions"] == ["Send proposal by Friday", "Review existing AIA policy"]
    assert len(notes) == 1
    assert "Next actions:" in notes[0]["content"]
    assert "Send proposal by Friday" in notes[0]["content"]


def test_log_to_crm_client_not_found(monkeypatch):
    monkeypatch.setattr(mm, "extract_memo",
                        lambda t: {"client_name": "Ghost", "summary": "s", "next_actions": []})

    class FakeProvider:
        name = "fake"

        def search_people(self, q, limit=20):
            return []

        def append_note(self, **k):
            return None

    import roost.extras.crm.services as crm
    monkeypatch.setattr(crm, "get_provider", lambda *a, **k: FakeProvider())

    res = mm.log_to_crm("met ghost")
    assert res["ok"] is False
    assert "client_not_found" in res["reason"]


def test_client_hint_overrides_extraction(monkeypatch):
    monkeypatch.setattr(mm, "extract_memo",
                        lambda t: {"client_name": "", "summary": "s", "next_actions": []})
    captured = {}

    class FakeProvider:
        name = "fake"

        def search_people(self, q, limit=20):
            captured["q"] = q
            return [SimpleNamespace(id="p1", name="Sarah Wong")]

        def append_note(self, **k):
            return "n1"

    import roost.extras.crm.services as crm
    monkeypatch.setattr(crm, "get_provider", lambda *a, **k: FakeProvider())

    res = mm.log_to_crm("vague memo", client_hint="Sarah Wong")
    assert res["ok"] is True
    assert captured["q"] == "Sarah Wong"
