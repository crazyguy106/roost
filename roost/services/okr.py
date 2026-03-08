"""OKR service — cycles, objectives, key results, task links, progress, scoring."""

import logging
from datetime import datetime

from roost.database import get_connection
from roost.models import (
    OkrCycleCreate, OkrCycleUpdate, OkrCycle,
    OkrObjectiveCreate, OkrObjectiveUpdate,
    OkrKeyResultCreate, OkrKeyResultUpdate,
    CycleStatus,
)

logger = logging.getLogger("roost.services.okr")

__all__ = [
    "create_okr_cycle",
    "list_okr_cycles",
    "update_okr_cycle",
    "create_okr_objective",
    "get_okr_objective",
    "update_okr_objective",
    "create_okr_key_result",
    "get_okr_key_result",
    "update_okr_key_result",
    "link_task_to_kr",
    "unlink_task_from_kr",
    "get_okr_dashboard",
    "okr_checkin",
    "get_okr_scorecard",
]

# ── Cycle status lifecycle ────────────────────────────────────────
_CYCLE_TRANSITIONS = {
    "planning": "active",
    "active": "scoring",
    "scoring": "closed",
}


# ── Cycles ────────────────────────────────────────────────────────

def create_okr_cycle(data: OkrCycleCreate) -> dict:
    """Create a new OKR cycle (quarterly period)."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO okr_cycles (name, start_date, end_date, entity_id, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (data.name, data.start_date, data.end_date, data.entity_id, data.notes),
    )
    conn.commit()
    cycle_id = cur.lastrowid
    conn.close()
    return _get_cycle(cycle_id)


def list_okr_cycles(
    status: str | None = None,
    entity_id: int | None = None,
) -> list[dict]:
    """List OKR cycles with optional filters."""
    conn = get_connection()
    query = """SELECT c.*, e.name as entity_name
               FROM okr_cycles c
               LEFT JOIN entities e ON c.entity_id = e.id
               WHERE 1=1"""
    params: list = []
    if status:
        query += " AND c.status = ?"
        params.append(status)
    if entity_id is not None:
        query += " AND c.entity_id = ?"
        params.append(entity_id)
    query += " ORDER BY c.start_date DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_okr_cycle(cycle_id: int, data: OkrCycleUpdate) -> dict:
    """Update an OKR cycle. Enforces lifecycle transitions for status."""
    conn = get_connection()
    current = conn.execute("SELECT * FROM okr_cycles WHERE id = ?", (cycle_id,)).fetchone()
    if not current:
        conn.close()
        raise ValueError(f"Cycle {cycle_id} not found")

    # Enforce status lifecycle
    if data.status:
        new_status = data.status.value if isinstance(data.status, CycleStatus) else data.status
        cur_status = current["status"]
        expected_next = _CYCLE_TRANSITIONS.get(cur_status)
        if new_status != cur_status and new_status != expected_next:
            conn.close()
            raise ValueError(
                f"Cannot transition from '{cur_status}' to '{new_status}'. "
                f"Next valid status: '{expected_next}'"
            )

    updates = []
    params = []
    for field in ("name", "notes"):
        val = getattr(data, field)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)
    if data.status is not None:
        updates.append("status = ?")
        params.append(data.status.value if isinstance(data.status, CycleStatus) else data.status)

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(cycle_id)
        conn.execute(
            f"UPDATE okr_cycles SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    conn.close()
    return _get_cycle(cycle_id)


def _get_cycle(cycle_id: int) -> dict:
    """Fetch a single cycle by ID."""
    conn = get_connection()
    row = conn.execute(
        """SELECT c.*, e.name as entity_name
           FROM okr_cycles c
           LEFT JOIN entities e ON c.entity_id = e.id
           WHERE c.id = ?""",
        (cycle_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {}
    return dict(row)


# ── Objectives ────────────────────────────────────────────────────

def create_okr_objective(data: OkrObjectiveCreate) -> dict:
    """Create an objective under a cycle. Enforces max 3 per level per cycle."""
    conn = get_connection()

    # Verify cycle exists
    cycle = conn.execute("SELECT id FROM okr_cycles WHERE id = ?", (data.cycle_id,)).fetchone()
    if not cycle:
        conn.close()
        raise ValueError(f"Cycle {data.cycle_id} not found")

    # Enforce max 3 objectives per level per cycle
    level_val = data.level.value if hasattr(data.level, "value") else data.level
    count = conn.execute(
        """SELECT COUNT(*) as cnt FROM okr_objectives
           WHERE cycle_id = ? AND level = ? AND status != 'cancelled'""",
        (data.cycle_id, level_val),
    ).fetchone()["cnt"]
    if count >= 3:
        conn.close()
        raise ValueError(
            f"Max 3 objectives per level per cycle. "
            f"Already have {count} '{level_val}' objectives in cycle {data.cycle_id}"
        )

    # Get next sort_order
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) as mx FROM okr_objectives WHERE cycle_id = ?",
        (data.cycle_id,),
    ).fetchone()["mx"]

    cur = conn.execute(
        """INSERT INTO okr_objectives
           (cycle_id, title, description, level, okr_type, parent_objective_id,
            owner_contact_id, entity_id, project_id, department, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data.cycle_id, data.title, data.description,
         level_val,
         data.okr_type.value if hasattr(data.okr_type, "value") else data.okr_type,
         data.parent_objective_id, data.owner_contact_id,
         data.entity_id, data.project_id, data.department,
         max_order + 1),
    )
    conn.commit()
    obj_id = cur.lastrowid
    conn.close()
    return get_okr_objective(obj_id)


def get_okr_objective(obj_id: int) -> dict:
    """Fetch a single objective with owner/entity names."""
    conn = get_connection()
    row = conn.execute(
        """SELECT o.*,
                  c.name as owner_name,
                  e.name as entity_name,
                  p.name as project_name,
                  po.title as parent_objective_title
           FROM okr_objectives o
           LEFT JOIN contacts c ON o.owner_contact_id = c.id
           LEFT JOIN entities e ON o.entity_id = e.id
           LEFT JOIN projects p ON o.project_id = p.id
           LEFT JOIN okr_objectives po ON o.parent_objective_id = po.id
           WHERE o.id = ?""",
        (obj_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {}
    return dict(row)


def update_okr_objective(obj_id: int, data: OkrObjectiveUpdate) -> dict:
    """Update an objective's fields, including scoring."""
    conn = get_connection()
    current = conn.execute("SELECT id FROM okr_objectives WHERE id = ?", (obj_id,)).fetchone()
    if not current:
        conn.close()
        raise ValueError(f"Objective {obj_id} not found")

    updates = []
    params = []
    for field in ("title", "description", "department", "score_note", "sort_order",
                  "parent_objective_id", "owner_contact_id", "entity_id", "project_id"):
        val = getattr(data, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)

    for field in ("status", "okr_type"):
        val = getattr(data, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val.value if hasattr(val, "value") else val)

    if data.score is not None:
        if not (0.0 <= data.score <= 1.0):
            conn.close()
            raise ValueError("Score must be between 0.0 and 1.0")
        updates.append("score = ?")
        params.append(data.score)

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(obj_id)
        conn.execute(
            f"UPDATE okr_objectives SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    conn.close()
    return get_okr_objective(obj_id)


# ── Key Results ───────────────────────────────────────────────────

def create_okr_key_result(data: OkrKeyResultCreate) -> dict:
    """Create a KR under an objective. Enforces max 5 per objective."""
    conn = get_connection()

    # Verify objective exists
    obj = conn.execute("SELECT id FROM okr_objectives WHERE id = ?", (data.objective_id,)).fetchone()
    if not obj:
        conn.close()
        raise ValueError(f"Objective {data.objective_id} not found")

    # Enforce max 5 KRs per objective
    count = conn.execute(
        """SELECT COUNT(*) as cnt FROM okr_key_results
           WHERE objective_id = ? AND status != 'cancelled'""",
        (data.objective_id,),
    ).fetchone()["cnt"]
    if count >= 5:
        conn.close()
        raise ValueError(
            f"Max 5 key results per objective. "
            f"Already have {count} active KRs under objective {data.objective_id}"
        )

    # Get next sort_order
    max_order = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) as mx FROM okr_key_results WHERE objective_id = ?",
        (data.objective_id,),
    ).fetchone()["mx"]

    cur = conn.execute(
        """INSERT INTO okr_key_results
           (objective_id, title, description, metric_type,
            start_value, target_value, current_value, unit,
            owner_contact_id, sort_order)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data.objective_id, data.title, data.description,
         data.metric_type.value if hasattr(data.metric_type, "value") else data.metric_type,
         data.start_value, data.target_value, data.current_value, data.unit,
         data.owner_contact_id, max_order + 1),
    )
    conn.commit()
    kr_id = cur.lastrowid
    conn.close()
    return get_okr_key_result(kr_id)


def get_okr_key_result(kr_id: int) -> dict:
    """Fetch a single key result with computed progress."""
    conn = get_connection()
    row = conn.execute(
        """SELECT kr.*, c.name as owner_name, o.title as objective_title
           FROM okr_key_results kr
           LEFT JOIN contacts c ON kr.owner_contact_id = c.id
           LEFT JOIN okr_objectives o ON kr.objective_id = o.id
           WHERE kr.id = ?""",
        (kr_id,),
    ).fetchone()
    conn.close()
    if not row:
        return {}
    result = dict(row)
    result["progress"] = _compute_kr_progress(result)
    return result


def update_okr_key_result(kr_id: int, data: OkrKeyResultUpdate) -> dict:
    """Update a key result's fields, including values and scoring."""
    conn = get_connection()
    current = conn.execute("SELECT id FROM okr_key_results WHERE id = ?", (kr_id,)).fetchone()
    if not current:
        conn.close()
        raise ValueError(f"Key result {kr_id} not found")

    updates = []
    params = []
    for field in ("title", "description", "unit", "score_note", "sort_order",
                  "start_value", "target_value", "current_value", "owner_contact_id"):
        val = getattr(data, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val)

    for field in ("metric_type", "confidence", "status"):
        val = getattr(data, field, None)
        if val is not None:
            updates.append(f"{field} = ?")
            params.append(val.value if hasattr(val, "value") else val)

    if data.score is not None:
        if not (0.0 <= data.score <= 1.0):
            conn.close()
            raise ValueError("Score must be between 0.0 and 1.0")
        updates.append("score = ?")
        params.append(data.score)

    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(kr_id)
        conn.execute(
            f"UPDATE okr_key_results SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    conn.close()
    return get_okr_key_result(kr_id)


# ── Task Links ────────────────────────────────────────────────────

def link_task_to_kr(key_result_id: int, task_id: int) -> dict:
    """Link an existing task to a key result."""
    conn = get_connection()
    # Verify both exist
    kr = conn.execute("SELECT id FROM okr_key_results WHERE id = ?", (key_result_id,)).fetchone()
    if not kr:
        conn.close()
        raise ValueError(f"Key result {key_result_id} not found")
    task = conn.execute("SELECT id, title FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not task:
        conn.close()
        raise ValueError(f"Task {task_id} not found")

    try:
        conn.execute(
            "INSERT INTO okr_task_links (key_result_id, task_id) VALUES (?, ?)",
            (key_result_id, task_id),
        )
        conn.commit()
    except Exception:
        conn.close()
        raise ValueError(f"Task {task_id} is already linked to KR {key_result_id}")
    conn.close()
    return {"linked": True, "key_result_id": key_result_id, "task_id": task_id,
            "task_title": task["title"]}


def unlink_task_from_kr(key_result_id: int, task_id: int) -> dict:
    """Remove a task-KR link."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM okr_task_links WHERE key_result_id = ? AND task_id = ?",
        (key_result_id, task_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    if not deleted:
        raise ValueError(f"No link found between KR {key_result_id} and task {task_id}")
    return {"unlinked": True, "key_result_id": key_result_id, "task_id": task_id}


# ── Progress Computation (live, never stored) ─────────────────────

def _compute_kr_progress(kr: dict) -> float:
    """Compute KR progress from values. Clamped 0.0-1.0."""
    if kr["metric_type"] == "milestone":
        return 1.0 if kr["current_value"] >= kr["target_value"] else 0.0

    denominator = kr["target_value"] - kr["start_value"]
    if denominator == 0:
        return 1.0 if kr["current_value"] >= kr["target_value"] else 0.0

    progress = (kr["current_value"] - kr["start_value"]) / denominator
    return max(0.0, min(1.0, progress))


def _compute_objective_progress(objective_id: int, conn) -> float:
    """Average progress of active KRs under an objective."""
    krs = conn.execute(
        "SELECT * FROM okr_key_results WHERE objective_id = ? AND status = 'active'",
        (objective_id,),
    ).fetchall()
    if not krs:
        return 0.0
    total = sum(_compute_kr_progress(dict(kr)) for kr in krs)
    return total / len(krs)


# ── Dashboard ─────────────────────────────────────────────────────

def get_okr_dashboard(cycle_id: int | None = None, entity_id: int | None = None) -> dict:
    """Full OKR tree with computed progress. Defaults to active cycle."""
    conn = get_connection()

    # Find target cycle
    if cycle_id:
        cycle_row = conn.execute(
            """SELECT c.*, e.name as entity_name FROM okr_cycles c
               LEFT JOIN entities e ON c.entity_id = e.id
               WHERE c.id = ?""",
            (cycle_id,),
        ).fetchone()
    else:
        query = """SELECT c.*, e.name as entity_name FROM okr_cycles c
                   LEFT JOIN entities e ON c.entity_id = e.id
                   WHERE c.status = 'active'"""
        params = []
        if entity_id is not None:
            query += " AND c.entity_id = ?"
            params.append(entity_id)
        query += " ORDER BY c.start_date DESC LIMIT 1"
        cycle_row = conn.execute(query, params).fetchone()

    if not cycle_row:
        conn.close()
        return {"error": "No active cycle found. Create one first."}

    cycle = dict(cycle_row)
    cid = cycle["id"]

    # Fetch objectives
    obj_rows = conn.execute(
        """SELECT o.*,
                  c.name as owner_name,
                  e.name as entity_name,
                  p.name as project_name
           FROM okr_objectives o
           LEFT JOIN contacts c ON o.owner_contact_id = c.id
           LEFT JOIN entities e ON o.entity_id = e.id
           LEFT JOIN projects p ON o.project_id = p.id
           WHERE o.cycle_id = ? AND o.status != 'cancelled'
           ORDER BY o.level, o.sort_order""",
        (cid,),
    ).fetchall()

    objectives = []
    all_obj_progress = []
    for obj_row in obj_rows:
        obj = dict(obj_row)
        obj_id = obj["id"]

        # Fetch KRs
        kr_rows = conn.execute(
            """SELECT kr.*, c.name as owner_name
               FROM okr_key_results kr
               LEFT JOIN contacts c ON kr.owner_contact_id = c.id
               WHERE kr.objective_id = ? AND kr.status != 'cancelled'
               ORDER BY kr.sort_order""",
            (obj_id,),
        ).fetchall()

        key_results = []
        for kr_row in kr_rows:
            kr = dict(kr_row)
            kr["progress"] = _compute_kr_progress(kr)

            # Fetch linked tasks
            task_rows = conn.execute(
                """SELECT t.id, t.title, t.status, t.priority
                   FROM okr_task_links otl
                   JOIN tasks t ON otl.task_id = t.id
                   WHERE otl.key_result_id = ?""",
                (kr["id"],),
            ).fetchall()
            kr["linked_tasks"] = [dict(t) for t in task_rows]
            key_results.append(kr)

        obj["key_results"] = key_results
        obj_progress = _compute_objective_progress(obj_id, conn)
        obj["progress"] = round(obj_progress, 3)
        all_obj_progress.append(obj_progress)
        objectives.append(obj)

    cycle["objectives"] = objectives
    cycle["progress"] = round(
        sum(all_obj_progress) / len(all_obj_progress), 3
    ) if all_obj_progress else 0.0

    conn.close()
    return cycle


# ── Check-in ──────────────────────────────────────────────────────

def okr_checkin(kr_id: int, current_value: float, confidence: str = "green") -> dict:
    """Quick weekly check-in: update current_value + confidence on a KR."""
    if confidence not in ("green", "yellow", "red"):
        raise ValueError(f"Confidence must be green/yellow/red, got '{confidence}'")

    conn = get_connection()
    kr = conn.execute("SELECT * FROM okr_key_results WHERE id = ?", (kr_id,)).fetchone()
    if not kr:
        conn.close()
        raise ValueError(f"Key result {kr_id} not found")

    conn.execute(
        """UPDATE okr_key_results
           SET current_value = ?, confidence = ?, updated_at = datetime('now')
           WHERE id = ?""",
        (current_value, confidence, kr_id),
    )
    conn.commit()
    conn.close()

    result = get_okr_key_result(kr_id)
    return result


# ── Scorecard ─────────────────────────────────────────────────────

_ACHIEVEMENT_BANDS = [
    (0.0, 0.3, "Off track"),
    (0.3, 0.5, "Needs attention"),
    (0.5, 0.7, "Making progress"),
    (0.7, 1.0, "On track"),
    (1.0, 1.01, "Fully achieved"),
]


def _get_band(score: float | None) -> str:
    """Map a score to an achievement band."""
    if score is None:
        return "Not scored"
    for low, high, label in _ACHIEVEMENT_BANDS:
        if low <= score < high:
            return label
    return "Fully achieved" if score >= 1.0 else "Unknown"


def get_okr_scorecard(cycle_id: int) -> dict:
    """End-of-quarter scorecard with achievement bands."""
    conn = get_connection()

    cycle_row = conn.execute(
        """SELECT c.*, e.name as entity_name FROM okr_cycles c
           LEFT JOIN entities e ON c.entity_id = e.id
           WHERE c.id = ?""",
        (cycle_id,),
    ).fetchone()
    if not cycle_row:
        conn.close()
        raise ValueError(f"Cycle {cycle_id} not found")

    cycle = dict(cycle_row)

    obj_rows = conn.execute(
        """SELECT o.*, c.name as owner_name
           FROM okr_objectives o
           LEFT JOIN contacts c ON o.owner_contact_id = c.id
           WHERE o.cycle_id = ? AND o.status != 'cancelled'
           ORDER BY o.level, o.sort_order""",
        (cycle_id,),
    ).fetchall()

    scored_objectives = []
    all_scores = []
    committed_scores = []
    aspirational_scores = []

    for obj_row in obj_rows:
        obj = dict(obj_row)
        obj_id = obj["id"]

        kr_rows = conn.execute(
            """SELECT * FROM okr_key_results
               WHERE objective_id = ? AND status != 'cancelled'
               ORDER BY sort_order""",
            (obj_id,),
        ).fetchall()

        scored_krs = []
        for kr_row in kr_rows:
            kr = dict(kr_row)
            kr["progress"] = _compute_kr_progress(kr)
            kr["band"] = _get_band(kr["score"])
            scored_krs.append(kr)

        obj["key_results"] = scored_krs
        obj["progress"] = _compute_objective_progress(obj_id, conn)
        obj["band"] = _get_band(obj["score"])

        # Use manual score if set, otherwise use progress
        effective_score = obj["score"] if obj["score"] is not None else obj["progress"]
        all_scores.append(effective_score)
        if obj["okr_type"] == "committed":
            committed_scores.append(effective_score)
        else:
            aspirational_scores.append(effective_score)

        scored_objectives.append(obj)

    avg_all = sum(all_scores) / len(all_scores) if all_scores else 0
    avg_committed = sum(committed_scores) / len(committed_scores) if committed_scores else 0
    avg_aspirational = sum(aspirational_scores) / len(aspirational_scores) if aspirational_scores else 0

    conn.close()

    return {
        "cycle": cycle,
        "objectives": scored_objectives,
        "summary": {
            "total_objectives": len(scored_objectives),
            "average_score": round(avg_all, 2),
            "average_band": _get_band(avg_all),
            "committed_avg": round(avg_committed, 2),
            "committed_band": _get_band(avg_committed),
            "aspirational_avg": round(avg_aspirational, 2),
            "aspirational_band": _get_band(avg_aspirational),
            "note": (
                "Committed OKRs should hit 0.7-1.0. "
                "Aspirational OKRs at 0.4-0.6 is healthy."
            ),
        },
    }
