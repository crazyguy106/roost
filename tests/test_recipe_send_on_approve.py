"""Send-on-approve: approving a recipe run delivers the draft to the channel.

Recipe runs are draft-first — `execute_recipe` only drafts and parks the run
as `awaiting_approval`. Historically `approve_run` just marked the run
completed, so an approved reply never reached the customer. It now also
dispatches the approved draft back to the channel recorded in the run's
`trigger_data` (chatwoot conversation / whatsapp sender), behind the human
approval gate. These tests stub the channel senders and assert the dispatch.
"""

from __future__ import annotations

import pytest

from roost.database import get_connection
from roost.services import recipes as recipes_svc


@pytest.fixture
def clean_runs():
    for _ in range(1):  # before + after
        conn = get_connection()
        conn.execute("DELETE FROM automation_runs")
        conn.execute("DELETE FROM automation_recipes")
        conn.commit()
        conn.close()
    yield
    conn = get_connection()
    conn.execute("DELETE FROM automation_runs")
    conn.execute("DELETE FROM automation_recipes")
    conn.commit()
    conn.close()


def _held_run(trigger_data: dict, draft: str = "Hi! Thanks for reaching out.") -> int:
    """Create a recipe + awaiting-approval run carrying `trigger_data`."""
    recipe = recipes_svc.create_recipe(
        name="cw auto-reply", instructions="reply", risk_tier="external_write",
    )
    run = recipes_svc.create_run(recipe["id"], trigger_data=trigger_data)
    recipes_svc.update_run(
        run["id"], status="awaiting_approval", draft_output=draft,
    )
    return run["id"]


def test_approve_sends_to_chatwoot(clean_runs, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(
        cw, "send_message",
        lambda conv, content, **kw: sent.append((conv, content)) or {"ok": True},
    )

    run_id = _held_run({"source": "chatwoot", "conversation_id": 2,
                        "sender": "+6591234567"})
    res = recipes_svc.approve_run(run_id)

    assert res["ok"] is True
    assert res["status"] == "completed"
    assert res["dispatch"] == {
        "sent": True, "channel": "chatwoot", "conversation_id": 2,
    }
    assert sent == [(2, "Hi! Thanks for reaching out.")]


def test_approve_sends_to_whatsapp(clean_runs, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.whatsapp as wa
    monkeypatch.setattr(
        wa, "send_text_message",
        lambda to, body: sent.append((to, body)) or {"ok": True},
    )

    run_id = _held_run({"source": "whatsapp", "sender": "+6599999999"})
    res = recipes_svc.approve_run(run_id)

    assert res["dispatch"]["sent"] is True
    assert res["dispatch"]["channel"] == "whatsapp"
    assert sent == [("+6599999999", "Hi! Thanks for reaching out.")]


def test_approve_uses_edited_final_output_over_draft(clean_runs, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(
        cw, "send_message",
        lambda conv, content, **kw: sent.append((conv, content)) or {"ok": True},
    )
    run_id = _held_run({"source": "chatwoot", "conversation_id": 9})
    recipes_svc.approve_run(run_id, final_output="Operator edit.")
    assert sent == [(9, "Operator edit.")]


def test_approve_chatwoot_missing_conversation_id_is_noop(clean_runs, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(cw, "send_message", lambda *a, **k: sent.append(a))

    run_id = _held_run({"source": "chatwoot"})  # no conversation_id
    res = recipes_svc.approve_run(run_id)
    assert res["dispatch"] == {"sent": False, "reason": "no_conversation_id"}
    assert sent == []


def test_approve_unsupported_source_is_noop(clean_runs):
    run_id = _held_run({"source": "email"})
    res = recipes_svc.approve_run(run_id)
    assert res["dispatch"]["sent"] is False
    assert "unsupported_source" in res["dispatch"]["reason"]


def test_approve_empty_draft_does_not_send(clean_runs, monkeypatch):
    sent = []
    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(cw, "send_message", lambda *a, **k: sent.append(a))

    run_id = _held_run({"source": "chatwoot", "conversation_id": 2}, draft="")
    res = recipes_svc.approve_run(run_id)
    assert res["dispatch"] == {"sent": False, "reason": "empty_draft"}
    assert sent == []


def test_approve_missing_run_errors(clean_runs):
    res = recipes_svc.approve_run(999_999)
    assert "error" in res


def test_send_failure_does_not_unwind_approval(clean_runs, monkeypatch):
    """A channel send raising must not crash approve_run — the run is still
    completed, the failure is reported in `dispatch`."""
    import roost.extras.messaging_external.services.chatwoot as cw

    def _boom(*a, **k):
        raise RuntimeError("chatwoot down")

    monkeypatch.setattr(cw, "send_message", _boom)
    run_id = _held_run({"source": "chatwoot", "conversation_id": 2})
    res = recipes_svc.approve_run(run_id)
    assert res["status"] == "completed"
    assert res["dispatch"]["sent"] is False
    assert "chatwoot down" in res["dispatch"]["error"]
