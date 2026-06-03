"""Tests for the system-of-record audit log (`roost.services.activity`).

Covers:
- `log_action` persists with the right column mapping (entity_type →
  artifact_type, entity_id → artifact_ref, etc.).
- `log_action` is fire-and-forget — exceptions never bubble up.
- `recent()` orders newest-first; `system_only=True` filters out
  task-coupled rows.
- `for_entity()` filters by (type, id).
- `stats_service.get_user_stats()` excludes system writes from the
  activity counter — regression pin so fast-path writes never inflate
  productivity stats.
- `tools.get_today_activity` (task-coupled) is unaffected by system
  writes (user_id NULL → filtered by uid match).
- MCP `audit_recent` returns rows via the FastMCP `.fn` attribute.
- Long snippets get truncated.
"""
from __future__ import annotations

import json

import pytest

from roost.database import get_connection
from roost.services import activity


# ────────────────────────────────────────────────────────────────────
# Per-test cleanup — activity_log is not cleaned by conftest because
# task-coupled writers leave rows behind. We wipe it here.
# ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_activity_log():
    conn = get_connection()
    conn.execute("DELETE FROM activity_log")
    conn.commit()
    conn.close()
    yield
    conn = get_connection()
    conn.execute("DELETE FROM activity_log")
    conn.commit()
    conn.close()


# ────────────────────────────────────────────────────────────────────
# log_action — persistence & robustness
# ────────────────────────────────────────────────────────────────────


def test_log_action_persists_with_expected_columns():
    activity.log_action(
        "telegram",
        "cadence.approve",
        entity_type="cadence_draft",
        entity_id=42,
        ok=True,
        result={"draft_id": 42, "status": "approved"},
        snippet="Approved cadence draft #42 via Telegram",
        actor_ref="tg:12345",
    )

    rows = activity.recent(limit=5, system_only=True)
    assert len(rows) == 1
    row = rows[0]
    assert row["actor"] == "telegram"
    assert row["action"] == "cadence.approve"
    assert row["entity_type"] == "cadence_draft"
    assert row["entity_id"] == "42"
    assert row["ok"] is True
    assert row["result"] == {"draft_id": 42, "status": "approved"}
    assert row["actor_ref"] == "tg:12345"
    assert row["task_id"] is None
    assert "Approved cadence draft" in row["snippet"]


def test_log_action_swallows_db_exception(monkeypatch):
    """A failed insert must not raise — fire-and-forget invariant."""
    def boom():
        raise RuntimeError("simulated DB down")

    monkeypatch.setattr("roost.services.activity.get_connection", boom)

    # Should not raise:
    activity.log_action("telegram", "x.test", entity_type="t", entity_id=1)


def test_log_action_handles_unserialisable_result():
    """A non-JSON-serialisable result is stored as empty result_json
    rather than crashing the write."""

    class Weird:
        pass

    activity.log_action(
        "web",
        "x.test",
        entity_type="t",
        entity_id=1,
        result=Weird(),
    )

    rows = activity.recent(limit=1, system_only=True)
    assert len(rows) == 1
    # default=str will stringify the object, so it serialises.
    # The point is it does NOT raise — either populated or empty is fine.
    assert "result" in rows[0]


def test_log_action_truncates_long_snippet():
    big = "x" * 2000
    activity.log_action("cli", "x.test", snippet=big)

    rows = activity.recent(limit=1, system_only=True)
    snip = rows[0]["snippet"]
    assert len(snip) <= 500
    assert snip.endswith("…")


# ────────────────────────────────────────────────────────────────────
# recent() — ordering, filters
# ────────────────────────────────────────────────────────────────────


def test_recent_orders_newest_first():
    for n in range(3):
        activity.log_action("telegram", f"x.step{n}", entity_id=n)

    rows = activity.recent(limit=10, system_only=True)
    actions = [r["action"] for r in rows]
    assert actions == ["x.step2", "x.step1", "x.step0"]


def test_recent_system_only_excludes_task_coupled():
    # Task-coupled write (the existing surface)
    from roost.services.tasks import log_activity as task_log
    task_log(task_id=None, action="legacy.write", user_id=1)
    # Also a true task-coupled one (task_id != NULL):
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (task_id, action, user_id) VALUES (?, ?, ?)",
        (123, "task.coupled", 1),
    )
    conn.commit()
    conn.close()

    # System write
    activity.log_action("web", "fast.path")

    sys_only = activity.recent(limit=10, system_only=True)
    actions_sys = [r["action"] for r in sys_only]
    assert "fast.path" in actions_sys
    assert "task.coupled" not in actions_sys

    all_rows = activity.recent(limit=10, system_only=False)
    actions_all = [r["action"] for r in all_rows]
    assert "fast.path" in actions_all
    assert "task.coupled" in actions_all


# ────────────────────────────────────────────────────────────────────
# for_entity() — filters by (type, id)
# ────────────────────────────────────────────────────────────────────


def test_for_entity_filters_by_type_and_id():
    activity.log_action("telegram", "approve", entity_type="cadence_draft", entity_id=1)
    activity.log_action("telegram", "edit", entity_type="cadence_draft", entity_id=1)
    activity.log_action("telegram", "approve", entity_type="cadence_draft", entity_id=2)
    activity.log_action("telegram", "approve", entity_type="other", entity_id=1)

    rows = activity.for_entity("cadence_draft", 1)
    assert len(rows) == 2
    actions = sorted(r["action"] for r in rows)
    assert actions == ["approve", "edit"]


# ────────────────────────────────────────────────────────────────────
# Regression pins — preserve existing readers' semantics
# ────────────────────────────────────────────────────────────────────


def test_stats_counter_excludes_system_writes():
    """`get_productivity_summary()` must not count fast-path writes in
    its activity counter, or productivity stats would inflate."""
    from roost.stats_service import get_productivity_summary

    # Baseline
    base = get_productivity_summary(days=7)["activity_actions"]

    # System write — should NOT bump counter
    activity.log_action("telegram", "fast.path")

    after_sys = get_productivity_summary(days=7)["activity_actions"]
    assert after_sys == base, "system writes leaked into productivity stats"

    # Task-coupled write — SHOULD bump counter
    conn = get_connection()
    conn.execute(
        "INSERT INTO activity_log (task_id, action, user_id) VALUES (?, ?, ?)",
        (999, "task.thing", 1),
    )
    conn.commit()
    conn.close()

    after_task = get_productivity_summary(days=7)["activity_actions"]
    assert after_task == base + 1, "task-coupled write was not counted"


def test_get_today_activity_unaffected_by_system_writes():
    """The task-coupled reader filters by `user_id`. System writes leave
    user_id NULL, so they're naturally excluded."""
    from roost.services.tasks import get_today_activity

    base = len(get_today_activity(user_id=1))

    activity.log_action("telegram", "fast.path")  # user_id NULL

    after = len(get_today_activity(user_id=1))
    assert after == base, "system write leaked into get_today_activity"


# ────────────────────────────────────────────────────────────────────
# MCP tool surface
# ────────────────────────────────────────────────────────────────────


def test_audit_recent_mcp_returns_records():
    from roost.mcp.tools_audit import audit_recent

    activity.log_action(
        "telegram",
        "cadence.approve",
        entity_type="cadence_draft",
        entity_id=7,
    )

    result = audit_recent.fn(limit=5)
    assert result["ok"] is True
    assert result["count"] >= 1
    assert any(r["action"] == "cadence.approve" for r in result["entries"])


def test_audit_for_entity_mcp_filters():
    from roost.mcp.tools_audit import audit_for_entity

    activity.log_action("web", "approve", entity_type="cadence_draft", entity_id=99)
    activity.log_action("web", "noise", entity_type="other", entity_id=99)

    result = audit_for_entity.fn("cadence_draft", "99")
    assert result["ok"] is True
    assert result["count"] == 1
    assert result["entries"][0]["action"] == "approve"
