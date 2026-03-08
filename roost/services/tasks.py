"""Task CRUD, triage, focus, streaks, activity logging, assignments.

Multi-tenant: task operations are scoped by user_id. The user_id is
auto-resolved from the current user context (set at MCP startup) if not
explicitly provided. This means MCP tools need zero changes.
"""

import json
import logging
import random
from datetime import datetime

from roost.database import get_connection
from roost.models import (
    Task, TaskCreate, TaskUpdate,
    ProjectAssignment, ProjectAssignmentCreate,
    TaskAssignment, TaskAssignmentCreate,
)
from roost.services.settings import get_setting, set_setting, delete_setting

logger = logging.getLogger("roost.services.tasks")


def _resolve_uid(user_id: int | None = None) -> int:
    """Resolve user_id from context if not provided."""
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()

__all__ = [
    # Core CRUD
    "create_task",
    "get_task",
    "list_tasks",
    "update_task",
    "complete_task",
    "delete_task",
    # Triage helpers
    "set_last_worked_at",
    "set_context_note",
    "mark_wip",
    "list_low_energy_tasks",
    # Sort order / reorder
    "recalculate_positions",
    "reorder_task",
    # Someday / shelving
    "shelve_task",
    "unshelve_task",
    "list_someday_tasks",
    # Focus mode
    "get_focus_tasks",
    "set_focus",
    "clear_focus",
    "suggest_focus",
    # Energy mode
    "get_energy_mode",
    "set_energy_mode",
    "list_matching_effort_tasks",
    # Next / pick
    "get_next_task",
    "pick_task",
    # Streaks
    "get_streak",
    "update_streak",
    # Celebrations
    "CELEBRATIONS",
    "STREAK_MILESTONES",
    "get_celebration",
    # Activity log
    "log_activity",
    "get_today_activity",
    "get_task_activity",
    # Active task
    "set_active_task",
    "get_active_task",
    "clear_active_task",
    # Subtasks / dependencies
    "list_subtasks",
    "add_dependency",
    "remove_dependency",
    "get_blockers",
    "get_dependents",
    "get_progress",
    # Project assignments
    "create_project_assignment",
    "get_project_assignment",
    "list_project_assignments",
    "delete_project_assignment",
    # Task assignments (RACI)
    "create_task_assignment",
    "get_task_assignment",
    "list_task_assignments",
    "delete_task_assignment",
    # Cross-entity queries
    "list_assignments_by_contact",
    "get_project_tree",
]


# ── Tasks ────────────────────────────────────────────────────────────

def create_task(data: TaskCreate, source: str = "", user_id: int | None = None) -> Task:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    deadline = data.deadline.strftime("%Y-%m-%d %H:%M:%S") if data.deadline else None
    task_type = data.task_type.value if hasattr(data.task_type, "value") else data.task_type
    energy = data.energy_level.value if hasattr(data.energy_level, "value") else data.energy_level
    effort = data.effort_estimate.value if hasattr(data.effort_estimate, "value") else data.effort_estimate
    cur = conn.execute(
        """INSERT INTO tasks (title, description, status, priority, deadline,
                             project_id, parent_task_id, task_type, sort_order,
                             energy_level, context_note,
                             effort_estimate, someday, focus_date, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data.title, data.description, data.status.value,
         data.priority.value, deadline, data.project_id,
         data.parent_task_id, task_type, data.sort_order,
         energy, data.context_note,
         effort, int(data.someday), data.focus_date, uid),
    )
    conn.commit()
    task_id = cur.lastrowid
    conn.close()

    # Assign sort_order position (append to end of active list)
    if not data.someday:
        _assign_next_position(task_id)

    # Compute urgency for the new task
    _recompute_urgency(task_id)

    task = get_task(task_id)

    # Emit event
    try:
        from roost.events import emit, TASK_CREATED
        emit(TASK_CREATED, {"task": task, "source": source})
    except ImportError:
        pass

    return task


def get_task(task_id: int, user_id: int | None = None) -> Task | None:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    row = conn.execute(
        """SELECT t.*, p.name as project_name,
                  (SELECT COUNT(*) FROM tasks WHERE parent_task_id = t.id) as subtask_count,
                  (SELECT COUNT(*) FROM tasks WHERE parent_task_id = t.id AND status = 'done') as subtask_done
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.id = ? AND (t.user_id = ? OR t.user_id IS NULL)""",
        (task_id, uid),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Task(**dict(row))


def list_tasks(
    status: str | None = None,
    project: str | None = None,
    priority: str | None = None,
    parent_id: int | None = None,
    top_level_only: bool = False,
    deadline_filter: str | None = None,
    order_by: str | None = None,
    limit: int | None = None,
    energy_level: str | None = None,
    exclude_paused_projects: bool = False,
    include_someday: bool = False,
    focus_only: bool = False,
    effort_estimate: str | None = None,
    assigned_to: int | None = None,
    visible_to_user_id: int | None = None,
    search: str | None = None,
    user_id: int | None = None,
) -> list[Task]:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    query = """SELECT t.*, p.name as project_name,
                      (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id) as subtask_count,
                      (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id AND sub.status = 'done') as subtask_done
               FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.user_id = ?"""
    params: list = [uid]

    if status:
        query += " AND t.status = ?"
        params.append(status)
    if project:
        query += " AND LOWER(p.name) = LOWER(?)"
        params.append(project)
    if priority:
        query += " AND t.priority = ?"
        params.append(priority)
    if parent_id is not None:
        query += " AND t.parent_task_id = ?"
        params.append(parent_id)
    if top_level_only:
        query += " AND t.parent_task_id IS NULL"
    if energy_level:
        query += " AND t.energy_level = ?"
        params.append(energy_level)
    if exclude_paused_projects:
        query += " AND (p.status IS NULL OR p.status != 'paused')"

    # Someday filter: exclude by default unless explicitly included
    if not include_someday:
        query += " AND (t.someday = 0 OR t.someday IS NULL)"

    # Focus-only filter
    if focus_only:
        today_str = datetime.now().strftime("%Y-%m-%d")
        query += " AND t.focus_date = ?"
        params.append(today_str)

    # Effort estimate filter
    if effort_estimate:
        query += " AND t.effort_estimate = ?"
        params.append(effort_estimate)

    # Assigned-to filter
    if assigned_to is not None:
        query += " AND t.assigned_to = ?"
        params.append(assigned_to)

    # Full-text search on title + description (SQL LIKE)
    if search:
        query += " AND (t.title LIKE ? OR t.description LIKE ?)"
        pattern = f"%{search}%"
        params.append(pattern)
        params.append(pattern)

    # Legacy visibility scoping (for web app backward compat)
    if visible_to_user_id is not None:
        query += """ AND (
            t.project_id IS NULL
            OR t.assigned_to = ?
            OR t.project_id IN (SELECT pm.project_id FROM project_members pm WHERE pm.user_id = ?)
        )"""
        params.append(visible_to_user_id)
        params.append(visible_to_user_id)

    # Deadline filters
    if deadline_filter:
        now = datetime.now()
        today_start = now.strftime("%Y-%m-%d 00:00:00")
        today_end = now.strftime("%Y-%m-%d 23:59:59")
        if deadline_filter == "overdue":
            query += " AND t.deadline IS NOT NULL AND t.deadline < ?"
            params.append(today_start)
        elif deadline_filter == "today":
            query += " AND t.deadline >= ? AND t.deadline <= ?"
            params.append(today_start)
            params.append(today_end)
        elif deadline_filter == "this_week":
            from datetime import timedelta
            week_end = (now + timedelta(days=7)).strftime("%Y-%m-%d 23:59:59")
            query += " AND t.deadline >= ? AND t.deadline <= ?"
            params.append(today_start)
            params.append(week_end)
        elif deadline_filter == "has_deadline":
            query += " AND t.deadline IS NOT NULL"
        elif deadline_filter == "no_deadline":
            query += " AND t.deadline IS NULL"

    # Order by
    if order_by == "urgency":
        query += " ORDER BY t.urgency_score DESC"
    elif order_by == "deadline":
        query += " ORDER BY CASE WHEN t.deadline IS NULL THEN 1 ELSE 0 END, t.deadline ASC"
    elif order_by == "updated":
        query += " ORDER BY t.updated_at DESC"
    elif order_by == "position":
        query += " ORDER BY t.sort_order ASC"
    else:
        query += " ORDER BY t.sort_order ASC, t.created_at DESC"

    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [Task(**dict(r)) for r in rows]


def update_task(task_id: int, data: TaskUpdate, source: str = "", user_id: int | None = None) -> Task | None:
    # Ownership check: only update tasks belonging to current user (or unowned)
    uid = _resolve_uid(user_id)
    conn = get_connection()
    owner_row = conn.execute(
        "SELECT user_id FROM tasks WHERE id = ? AND (user_id = ? OR user_id IS NULL)",
        (task_id, uid),
    ).fetchone()
    conn.close()
    if not owner_row:
        return None

    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return get_task(task_id)

    # Convert enums to their values
    for key in ("status", "priority", "task_type", "energy_level", "effort_estimate"):
        if key in updates and hasattr(updates[key], "value"):
            updates[key] = updates[key].value

    # Convert someday bool to int
    if "someday" in updates:
        updates["someday"] = 1 if updates["someday"] else 0

    if "deadline" in updates and isinstance(updates["deadline"], datetime):
        updates["deadline"] = updates["deadline"].strftime("%Y-%m-%d %H:%M:%S")

    updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [task_id]

    conn = get_connection()
    conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()

    # Recompute urgency after update
    _recompute_urgency(task_id)

    task = get_task(task_id)

    try:
        from roost.events import emit, TASK_UPDATED, TASK_COMPLETED
        if task and task.status.value == "done":
            emit(TASK_COMPLETED, {"task": task, "source": source})
        else:
            emit(TASK_UPDATED, {"task": task, "source": source})
    except ImportError:
        pass

    return task


def complete_task(task_id: int, source: str = "") -> Task | None:
    task = update_task(task_id, TaskUpdate(status="done"), source=source)
    if task:
        recalculate_positions()
        # ND Phase 2: streak, spoons, activity log
        update_streak()
        from roost.services.wellbeing import spend_spoons
        spend_spoons(task.effort_estimate or "moderate")
        log_activity(task_id, "completed")
    return task


def delete_task(task_id: int, user_id: int | None = None) -> bool:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    # Always scope delete to the current user (prevents cross-user deletion)
    cur = conn.execute("DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, uid))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def _recompute_urgency(task_id: int) -> None:
    """Recompute urgency score for a single task."""
    try:
        from roost.triage import compute_urgency
        conn = get_connection()
        row = conn.execute(
            "SELECT id, priority, deadline, status, updated_at, last_worked_at FROM tasks WHERE id = ?",
            (task_id,),
        ).fetchone()
        if row:
            score = compute_urgency(dict(row))
            conn.execute("UPDATE tasks SET urgency_score = ? WHERE id = ?", (score, task_id))
            conn.commit()
        conn.close()
    except Exception:
        logger.debug("Failed to recompute urgency for task %d", task_id, exc_info=True)


# ── Triage helpers ───────────────────────────────────────────────────

def set_last_worked_at(task_id: int) -> Task | None:
    """Stamp when you last touched a task."""
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE tasks SET last_worked_at = ?, updated_at = ? WHERE id = ?",
        (now, now, task_id),
    )
    conn.commit()
    conn.close()
    _recompute_urgency(task_id)
    return get_task(task_id)


def set_context_note(task_id: int, note: str) -> Task | None:
    """Set a context breadcrumb on a task."""
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE tasks SET context_note = ?, updated_at = ? WHERE id = ?",
        (note, now, task_id),
    )
    conn.commit()
    conn.close()
    return get_task(task_id)


def mark_wip(task_id: int, context: str = "") -> Task | None:
    """Mark task as in_progress, stamp last_worked_at, optionally set context."""
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    sql = "UPDATE tasks SET status = 'in_progress', last_worked_at = ?, updated_at = ?"
    params = [now, now]
    if context:
        sql += ", context_note = ?"
        params.append(context)
    sql += " WHERE id = ?"
    params.append(task_id)
    conn.execute(sql, params)
    conn.commit()
    conn.close()
    _recompute_urgency(task_id)
    log_activity(task_id, "started", context)
    return get_task(task_id)


def list_low_energy_tasks(limit: int = 10) -> list[Task]:
    """Get low-energy tasks for bad days — excludes paused projects."""
    return list_tasks(
        energy_level="low",
        status="todo",
        exclude_paused_projects=True,
        order_by="urgency",
        limit=limit,
    )


# ── Sort Order / Reorder ─────────────────────────────────────────────

def _assign_next_position(task_id: int, user_id: int | None = None) -> None:
    """Assign the next available sort_order to a newly created task."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    row = conn.execute(
        """SELECT COALESCE(MAX(sort_order), 0) + 1 as next_pos FROM tasks
           WHERE user_id = ? AND status != 'done' AND (someday = 0 OR someday IS NULL)""",
        (uid,),
    ).fetchone()
    conn.execute(
        "UPDATE tasks SET sort_order = ? WHERE id = ?",
        (row["next_pos"], task_id),
    )
    conn.commit()
    conn.close()


def recalculate_positions(user_id: int | None = None) -> int:
    """Reassign sort_order 1..N for all active (non-done, non-someday) tasks.

    Order is preserved from current sort_order, ties broken by created_at DESC.
    Returns the number of tasks renumbered.
    """
    uid = _resolve_uid(user_id)
    conn = get_connection()
    rows = conn.execute(
        """SELECT id FROM tasks
           WHERE user_id = ? AND status != 'done' AND (someday = 0 OR someday IS NULL)
           ORDER BY CASE WHEN sort_order > 0 THEN sort_order ELSE 999999 END,
                    created_at DESC""",
        (uid,),
    ).fetchall()
    for i, row in enumerate(rows, 1):
        conn.execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (i, row["id"]))
    conn.commit()
    conn.close()
    return len(rows)


def reorder_task(task_id: int, new_position: int, user_id: int | None = None) -> Task | None:
    """Move a task to a new sort_order position, shifting others accordingly.

    If new_position is out of range, it's clamped to valid bounds.
    """
    uid = _resolve_uid(user_id)
    conn = get_connection()

    # Get current position
    row = conn.execute(
        "SELECT sort_order FROM tasks WHERE id = ? AND user_id = ?", (task_id, uid)
    ).fetchone()
    if not row:
        conn.close()
        return None
    old_pos = row["sort_order"]

    # Get max position among active tasks (scoped to user)
    max_row = conn.execute(
        """SELECT MAX(sort_order) as mx FROM tasks
           WHERE user_id = ? AND status != 'done' AND (someday = 0 OR someday IS NULL)""",
        (uid,),
    ).fetchone()
    max_pos = max_row["mx"] or 1

    # Clamp
    new_position = max(1, min(new_position, max_pos))

    if new_position == old_pos:
        conn.close()
        return get_task(task_id)

    if new_position < old_pos:
        # Moving up: shift tasks between new_pos and old_pos-1 down by 1
        conn.execute(
            """UPDATE tasks SET sort_order = sort_order + 1
               WHERE user_id = ? AND sort_order >= ? AND sort_order < ?
               AND status != 'done' AND (someday = 0 OR someday IS NULL)
               AND id != ?""",
            (uid, new_position, old_pos, task_id),
        )
    else:
        # Moving down: shift tasks between old_pos+1 and new_pos up by 1
        conn.execute(
            """UPDATE tasks SET sort_order = sort_order - 1
               WHERE user_id = ? AND sort_order > ? AND sort_order <= ?
               AND status != 'done' AND (someday = 0 OR someday IS NULL)
               AND id != ?""",
            (uid, old_pos, new_position, task_id),
        )

    conn.execute(
        "UPDATE tasks SET sort_order = ? WHERE id = ?",
        (new_position, task_id),
    )
    conn.commit()
    conn.close()
    return get_task(task_id)


# ── Someday / Shelving ───────────────────────────────────────────────

def shelve_task(task_id: int) -> Task | None:
    """Move task to someday — hidden from default views."""
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE tasks SET someday = 1, sort_order = 0, updated_at = ? WHERE id = ?",
        (now, task_id),
    )
    conn.commit()
    conn.close()
    recalculate_positions()
    return get_task(task_id)


def unshelve_task(task_id: int) -> Task | None:
    """Bring task back from someday to active."""
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE tasks SET someday = 0, updated_at = ? WHERE id = ?",
        (now, task_id),
    )
    conn.commit()
    conn.close()
    _assign_next_position(task_id)
    return get_task(task_id)


def list_someday_tasks(limit: int = 20) -> list[Task]:
    """List tasks in the someday pile."""
    return list_tasks(include_someday=True, limit=limit)


# ── Focus Mode (Daily 3) ────────────────────────────────────────────

def get_focus_tasks(date_str: str | None = None, user_id: int | None = None) -> list[Task]:
    """Get today's focused tasks (max 3)."""
    uid = _resolve_uid(user_id)
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, p.name as project_name,
                  (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id) as subtask_count,
                  (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id AND sub.status = 'done') as subtask_done
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.user_id = ? AND t.focus_date = ? AND t.status != 'done'
                 AND (t.someday = 0 OR t.someday IS NULL)
           ORDER BY t.urgency_score DESC LIMIT 3""",
        (uid, date_str),
    ).fetchall()
    conn.close()
    return [Task(**dict(r)) for r in rows]


def set_focus(task_id: int) -> dict:
    """Pin a task as today's focus. Max 3. Returns {ok, message, task}."""
    today = datetime.now().strftime("%Y-%m-%d")
    current = get_focus_tasks(today)
    if len(current) >= 3:
        already = any(t.id == task_id for t in current)
        if already:
            return {"ok": True, "message": "Already focused", "task": get_task(task_id)}
        return {"ok": False, "message": "Max 3 focus tasks. Remove one first.", "task": None}

    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE tasks SET focus_date = ?, updated_at = ? WHERE id = ?",
        (today, now, task_id),
    )
    conn.commit()
    conn.close()
    task = get_task(task_id)
    return {"ok": True, "message": f"#{task_id} focused for today", "task": task}


def clear_focus(task_id: int | None = None, user_id: int | None = None) -> int:
    """Clear focus from one task or all tasks for today. Returns count cleared."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    if task_id:
        cur = conn.execute(
            "UPDATE tasks SET focus_date = NULL, updated_at = ? WHERE id = ? AND focus_date = ? AND user_id = ?",
            (now, task_id, today, uid),
        )
    else:
        cur = conn.execute(
            "UPDATE tasks SET focus_date = NULL, updated_at = ? WHERE focus_date = ? AND user_id = ?",
            (now, today, uid),
        )
    conn.commit()
    count = cur.rowcount
    conn.close()
    return count


def suggest_focus(limit: int = 3, user_id: int | None = None) -> list[Task]:
    """Suggest top tasks for focus when none are pinned."""
    return list_tasks(
        order_by="urgency",
        limit=limit,
        exclude_paused_projects=True,
        user_id=user_id,
    )


# ── Energy Mode ──────────────────────────────────────────────────────

def get_energy_mode() -> str | None:
    """Get today's energy mode if set today."""
    raw = get_setting("energy_mode")
    date = get_setting("energy_mode_date")
    today = datetime.now().strftime("%Y-%m-%d")
    if date == today and raw:
        return raw
    return None


def set_energy_mode(level: str) -> None:
    """Set today's energy mode (low/medium/high)."""
    today = datetime.now().strftime("%Y-%m-%d")
    set_setting("energy_mode", level)
    set_setting("energy_mode_date", today)


def list_matching_effort_tasks(budget: str, limit: int = 10) -> list[Task]:
    """List tasks matching an energy budget.

    low -> light only
    medium -> light + moderate
    high -> all
    """
    if budget == "low":
        return list_tasks(effort_estimate="light", status="todo",
                          exclude_paused_projects=True, order_by="urgency", limit=limit)
    elif budget == "medium":
        # light + moderate — two queries merged
        light = list_tasks(effort_estimate="light", status="todo",
                           exclude_paused_projects=True, order_by="urgency", limit=limit)
        moderate = list_tasks(effort_estimate="moderate", status="todo",
                              exclude_paused_projects=True, order_by="urgency", limit=limit)
        combined = light + moderate
        combined.sort(key=lambda t: t.urgency_score, reverse=True)
        return combined[:limit]
    else:
        return list_tasks(status="todo", exclude_paused_projects=True,
                          order_by="urgency", limit=limit)


# ── /next — Just One Thing ───────────────────────────────────────────

def get_next_task() -> Task | None:
    """Return the single most important task to work on right now.

    Priority: focused tasks first, then highest urgency.
    """
    focused = get_focus_tasks()
    if focused:
        return focused[0]
    tasks = list_tasks(
        order_by="urgency",
        limit=1,
        exclude_paused_projects=True,
    )
    active = [t for t in tasks if t.status.value != "done"]
    return active[0] if active else None


# ── /pick — Smart Picker ────────────────────────────────────────────

def pick_task(energy_budget: str | None = None) -> Task | None:
    """Pick a random task weighted by urgency score.

    If energy_budget is set, filters by matching effort level.
    """
    if energy_budget and energy_budget in ("low", "light"):
        tasks = list_tasks(
            effort_estimate="light", status="todo",
            exclude_paused_projects=True, order_by="urgency", limit=20,
        )
    elif energy_budget == "moderate":
        light = list_tasks(
            effort_estimate="light", status="todo",
            exclude_paused_projects=True, order_by="urgency", limit=20,
        )
        moderate = list_tasks(
            effort_estimate="moderate", status="todo",
            exclude_paused_projects=True, order_by="urgency", limit=20,
        )
        tasks = light + moderate
    else:
        tasks = list_tasks(
            status="todo",
            exclude_paused_projects=True, order_by="urgency", limit=20,
        )

    if not tasks:
        return None

    # Weighted random: urgency_score as weight (minimum 1)
    weights = [max(1, t.urgency_score) for t in tasks]
    return random.choices(tasks, weights=weights, k=1)[0]


# ── Streak Tracking ─────────────────────────────────────────────────

def get_streak() -> dict:
    """Return current streak info: {current, best, is_milestone}."""
    current = int(get_setting("streak_count") or "0")
    best = int(get_setting("streak_best") or "0")
    milestones = {3, 7, 14, 30, 60, 100}
    return {
        "current": current,
        "best": best,
        "is_milestone": current in milestones,
    }


def update_streak() -> dict:
    """Update streak after completing a task. Returns streak info."""
    today = datetime.now().strftime("%Y-%m-%d")
    last_date = get_setting("streak_last_date")
    current = int(get_setting("streak_count") or "0")
    best = int(get_setting("streak_best") or "0")

    if last_date == today:
        # Already counted today
        return get_streak()

    # Calculate yesterday's date
    from datetime import timedelta
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    if last_date == yesterday:
        current += 1
    else:
        current = 1  # Reset

    if current > best:
        best = current

    set_setting("streak_count", str(current))
    set_setting("streak_last_date", today)
    set_setting("streak_best", str(best))

    is_new_best = current == best and current > 1
    milestones = {3, 7, 14, 30, 60, 100}
    return {
        "current": current,
        "best": best,
        "is_new_best": is_new_best,
        "is_milestone": current in milestones,
    }


# ── Completion Celebrations ─────────────────────────────────────────

CELEBRATIONS = [
    "Done! One less thing on your plate.",
    "Crushed it!",
    "That's off the list. Nice work.",
    "Another one bites the dust.",
    "Look at you being productive!",
    "Boom. Done.",
    "One down. You've got this.",
    "Nailed it.",
    "Progress! Keep rolling.",
    "Check. What's next?",
    "Task slayer.",
    "That's a wrap on that one.",
    "Knocked it out!",
    "Cleared. Well done.",
    "And it's gone. Nice.",
]

STREAK_MILESTONES = {
    3: "3-day streak! You're building momentum.",
    7: "A full week streak! Consistency is your superpower.",
    14: "Two weeks straight! That's real discipline.",
    30: "30-day streak! You're unstoppable.",
    60: "60 days! Absolute machine.",
    100: "100-day streak! Legend status.",
}


def get_celebration() -> str:
    """Return a celebration message, checking for streak milestones first."""
    streak = get_streak()
    if streak["is_milestone"] and streak["current"] in STREAK_MILESTONES:
        return STREAK_MILESTONES[streak["current"]]
    return random.choice(CELEBRATIONS)


# ── Activity Log ────────────────────────────────────────────────────

def log_activity(
    task_id: int | None,
    action: str,
    detail: str = "",
    tool_name: str = "",
    artifact_type: str = "",
    artifact_ref: str = "",
    user_id: int | None = None,
) -> None:
    """Record an activity event, optionally with tool/artifact metadata."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    conn.execute(
        """INSERT INTO activity_log
           (task_id, action, detail, tool_name, artifact_type, artifact_ref, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (task_id, action, detail, tool_name, artifact_type, artifact_ref, uid),
    )
    conn.commit()
    conn.close()


def get_today_activity(user_id: int | None = None) -> list[dict]:
    """Get today's activity timeline with task titles."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    rows = conn.execute(
        """SELECT al.*, t.title as task_title
           FROM activity_log al
           LEFT JOIN tasks t ON al.task_id = t.id
           WHERE al.user_id = ? AND date(al.created_at) = date('now')
           ORDER BY al.created_at""",
        (uid,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_task_activity(task_id: int, limit: int = 50) -> list[dict]:
    """Return all activity entries for a given task, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT al.*, t.title as task_title
           FROM activity_log al
           LEFT JOIN tasks t ON al.task_id = t.id
           WHERE al.task_id = ?
           ORDER BY al.created_at DESC
           LIMIT ?""",
        (task_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Active Task (session concept via user_settings) ────────────────

def set_active_task(task_id: int) -> None:
    """Set the active task for the current work session."""
    set_setting("active_task_id", str(task_id))


def get_active_task() -> int | None:
    """Return the active task ID, or None if unset."""
    val = get_setting("active_task_id")
    return int(val) if val else None


def clear_active_task() -> None:
    """Clear the active task."""
    delete_setting("active_task_id")


# ── Subtasks / Dependencies ─────────────────────────────────────────

def list_subtasks(parent_id: int) -> list[Task]:
    """List all subtasks of a parent task, ordered by sort_order."""
    return list_tasks(parent_id=parent_id)


def add_dependency(task_id: int, depends_on_id: int) -> bool:
    """Add a dependency: task_id depends on depends_on_id."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_id) VALUES (?, ?)",
            (task_id, depends_on_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def remove_dependency(task_id: int, depends_on_id: int) -> bool:
    """Remove a dependency: task_id no longer depends on depends_on_id."""
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM task_dependencies WHERE task_id = ? AND depends_on_id = ?",
        (task_id, depends_on_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_blockers(task_id: int) -> list[Task]:
    """Get tasks that block this task (not yet done)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, p.name as project_name,
                  (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id) as subtask_count,
                  (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id AND sub.status = 'done') as subtask_done
           FROM task_dependencies d
           JOIN tasks t ON d.depends_on_id = t.id
           LEFT JOIN projects p ON t.project_id = p.id
           WHERE d.task_id = ? AND t.status != 'done'""",
        (task_id,),
    ).fetchall()
    conn.close()
    return [Task(**dict(r)) for r in rows]


def get_dependents(task_id: int) -> list[Task]:
    """Get tasks that depend on this task (tasks this one blocks)."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT t.*, p.name as project_name,
                  (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id) as subtask_count,
                  (SELECT COUNT(*) FROM tasks sub WHERE sub.parent_task_id = t.id AND sub.status = 'done') as subtask_done
           FROM task_dependencies d
           JOIN tasks t ON d.task_id = t.id
           LEFT JOIN projects p ON t.project_id = p.id
           WHERE d.depends_on_id = ? AND t.status != 'done'""",
        (task_id,),
    ).fetchall()
    conn.close()
    return [Task(**dict(r)) for r in rows]


def get_progress(project: str | None = None) -> dict:
    """Get progress summary with counts and percentages."""
    tasks = list_tasks(project=project, top_level_only=True)
    if not tasks:
        return {"total": 0, "done": 0, "pct": 0, "by_status": {}}

    total = len(tasks)
    by_status = {}
    for t in tasks:
        s = t.status.value
        by_status[s] = by_status.get(s, 0) + 1

    done = by_status.get("done", 0)
    pct = round(done / total * 100) if total > 0 else 0

    return {"total": total, "done": done, "pct": pct, "by_status": by_status}


# ── Project Assignments ─────────────────────────────────────────────

def create_project_assignment(data: ProjectAssignmentCreate) -> ProjectAssignment:
    """Assign a contact to a project with a role."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO project_assignments (contact_id, project_id, role, notes)
           VALUES (?, ?, ?, ?)""",
        (data.contact_id, data.project_id, data.role.upper(), data.notes),
    )
    conn.commit()
    assignment_id = cur.lastrowid
    conn.close()
    return get_project_assignment(assignment_id)


def get_project_assignment(assignment_id: int) -> ProjectAssignment | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT pa.*,
                  c.name as contact_name,
                  e.name as entity_name,
                  rd.label as role_label,
                  p.name as project_name
           FROM project_assignments pa
           JOIN contacts c ON pa.contact_id = c.id
           LEFT JOIN contact_entities ce_primary ON c.id = ce_primary.contact_id AND ce_primary.is_primary = 1
           LEFT JOIN entities e ON ce_primary.entity_id = e.id
           LEFT JOIN role_definitions rd ON pa.role = rd.code
           JOIN projects p ON pa.project_id = p.id
           WHERE pa.id = ?""",
        (assignment_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return ProjectAssignment(**dict(row))


def list_project_assignments(
    project_id: int | None = None,
    contact_id: int | None = None,
    role: str | None = None,
) -> list[ProjectAssignment]:
    """List project assignments with optional filters."""
    conn = get_connection()
    query = """SELECT pa.*,
                      c.name as contact_name,
                      e.name as entity_name,
                      rd.label as role_label,
                      p.name as project_name
               FROM project_assignments pa
               JOIN contacts c ON pa.contact_id = c.id
               LEFT JOIN contact_entities ce_primary ON c.id = ce_primary.contact_id AND ce_primary.is_primary = 1
               LEFT JOIN entities e ON ce_primary.entity_id = e.id
               LEFT JOIN role_definitions rd ON pa.role = rd.code
               JOIN projects p ON pa.project_id = p.id
               WHERE 1=1"""
    params: list = []
    if project_id is not None:
        query += " AND pa.project_id = ?"
        params.append(project_id)
    if contact_id is not None:
        query += " AND pa.contact_id = ?"
        params.append(contact_id)
    if role:
        query += " AND pa.role = ?"
        params.append(role.upper())
    query += " ORDER BY rd.sort_order, c.name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [ProjectAssignment(**dict(r)) for r in rows]


def delete_project_assignment(assignment_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM project_assignments WHERE id = ?", (assignment_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Task Assignments (RACI) ─────────────────────────────────────────

def create_task_assignment(data: TaskAssignmentCreate) -> TaskAssignment:
    """Assign a contact to a task with a role."""
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO raci_task_assignments (contact_id, task_id, role, notes)
           VALUES (?, ?, ?, ?)""",
        (data.contact_id, data.task_id, data.role.upper(), data.notes),
    )
    conn.commit()
    assignment_id = cur.lastrowid
    conn.close()
    return get_task_assignment(assignment_id)


def get_task_assignment(assignment_id: int) -> TaskAssignment | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT ta.*,
                  c.name as contact_name,
                  e.name as entity_name,
                  rd.label as role_label,
                  t.title as task_title
           FROM raci_task_assignments ta
           JOIN contacts c ON ta.contact_id = c.id
           LEFT JOIN contact_entities ce_primary ON c.id = ce_primary.contact_id AND ce_primary.is_primary = 1
           LEFT JOIN entities e ON ce_primary.entity_id = e.id
           LEFT JOIN role_definitions rd ON ta.role = rd.code
           JOIN tasks t ON ta.task_id = t.id
           WHERE ta.id = ?""",
        (assignment_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return TaskAssignment(**dict(row))


def list_task_assignments(
    task_id: int | None = None,
    contact_id: int | None = None,
    role: str | None = None,
) -> list[TaskAssignment]:
    """List task assignments with optional filters."""
    conn = get_connection()
    query = """SELECT ta.*,
                      c.name as contact_name,
                      e.name as entity_name,
                      rd.label as role_label,
                      t.title as task_title
               FROM raci_task_assignments ta
               JOIN contacts c ON ta.contact_id = c.id
               LEFT JOIN contact_entities ce_primary ON c.id = ce_primary.contact_id AND ce_primary.is_primary = 1
               LEFT JOIN entities e ON ce_primary.entity_id = e.id
               LEFT JOIN role_definitions rd ON ta.role = rd.code
               JOIN tasks t ON ta.task_id = t.id
               WHERE 1=1"""
    params: list = []
    if task_id is not None:
        query += " AND ta.task_id = ?"
        params.append(task_id)
    if contact_id is not None:
        query += " AND ta.contact_id = ?"
        params.append(contact_id)
    if role:
        query += " AND ta.role = ?"
        params.append(role.upper())
    query += " ORDER BY rd.sort_order, c.name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [TaskAssignment(**dict(r)) for r in rows]


def delete_task_assignment(assignment_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM raci_task_assignments WHERE id = ?", (assignment_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Cross-entity queries ─────────────────────────────────────────────

def list_assignments_by_contact(contact_id: int) -> dict:
    """Get all assignments for a contact — both project and task level."""
    return {
        "project_assignments": list_project_assignments(contact_id=contact_id),
        "task_assignments": list_task_assignments(contact_id=contact_id),
    }


def get_project_tree(project_id: int) -> dict:
    """Get a project with its children (one level deep)."""
    from roost.task_service import get_project, list_child_projects
    project = get_project(project_id)
    if not project:
        return {}
    children = list_child_projects(project_id)
    assignments = list_project_assignments(project_id=project_id)
    return {
        "project": project,
        "children": children,
        "assignments": assignments,
    }
