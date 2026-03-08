"""MCP tools for extended task features — dependencies, subtasks, reorder, assignments, helpers."""

from roost.mcp.server import mcp


# ── Dependencies ─────────────────────────────────────────────────────

@mcp.tool()
def add_task_dependency(task_id: int, depends_on_id: int) -> dict:
    """Add a dependency: task_id is blocked by depends_on_id.

    The blocking task must be completed before the dependent task can proceed.

    Args:
        task_id: The task that is blocked (depends on the other).
        depends_on_id: The task that blocks (must be done first).
    """
    try:
        from roost.task_service import add_dependency, get_task
        task = get_task(task_id)
        blocker = get_task(depends_on_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        if not blocker:
            return {"error": f"Task #{depends_on_id} not found"}
        if task_id == depends_on_id:
            return {"error": "A task cannot depend on itself"}
        ok = add_dependency(task_id, depends_on_id)
        return {
            "ok": ok,
            "message": f"#{task_id} '{task.title}' now blocked by #{depends_on_id} '{blocker.title}'",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def remove_task_dependency(task_id: int, depends_on_id: int) -> dict:
    """Remove a dependency between two tasks.

    Args:
        task_id: The task that was blocked.
        depends_on_id: The task that was blocking it.
    """
    try:
        from roost.task_service import remove_dependency
        ok = remove_dependency(task_id, depends_on_id)
        if not ok:
            return {"error": f"No dependency found: #{task_id} → #{depends_on_id}"}
        return {"ok": True, "message": f"Removed dependency: #{task_id} no longer blocked by #{depends_on_id}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_task_blockers(task_id: int) -> dict:
    """Get all tasks that block a given task (incomplete dependencies).

    Args:
        task_id: The task to check blockers for.
    """
    try:
        from roost.task_service import get_blockers, get_dependents, get_task
        task = get_task(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}

        blockers = get_blockers(task_id)
        dependents = get_dependents(task_id)
        return {
            "task_id": task_id,
            "task_title": task.title,
            "blocked_by": [_task_brief(t) for t in blockers],
            "blocks": [_task_brief(t) for t in dependents],
            "is_blocked": len(blockers) > 0,
        }
    except Exception as e:
        return {"error": str(e)}


# ── Subtasks ─────────────────────────────────────────────────────────

@mcp.tool()
def list_subtasks(parent_task_id: int, limit: int = 50) -> dict:
    """List all subtasks of a parent task.

    Args:
        parent_task_id: The parent task ID.
        limit: Maximum subtasks to return (default 50).
    """
    try:
        from roost.task_service import list_tasks as _list, get_task
        parent = get_task(parent_task_id)
        if not parent:
            return {"error": f"Task #{parent_task_id} not found"}

        subtasks = _list(parent_id=parent_task_id, limit=limit)
        return {
            "parent_task_id": parent_task_id,
            "parent_title": parent.title,
            "subtask_count": parent.subtask_count,
            "subtask_done": parent.subtask_done,
            "subtasks": [_task_brief(t) for t in subtasks],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Reordering ───────────────────────────────────────────────────────

@mcp.tool()
def reorder_task(task_id: int, new_position: int) -> dict:
    """Move a task to a new position in the active list.

    Other tasks shift accordingly. Position 1 = top of list.

    Args:
        task_id: The task to reorder.
        new_position: Target position (1-based). Clamped to valid range.
    """
    try:
        from roost.task_service import reorder_task as _reorder
        task = _reorder(task_id, new_position)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return {
            "ok": True,
            "task_id": task.id,
            "title": task.title,
            "new_position": task.sort_order,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def recalculate_task_positions() -> dict:
    """Renumber all active task positions 1..N.

    Useful after bulk operations to clean up gaps in sort_order.
    """
    try:
        from roost.task_service import recalculate_positions
        count = recalculate_positions()
        return {"ok": True, "tasks_renumbered": count}
    except Exception as e:
        return {"error": str(e)}


# ── Task Helpers ─────────────────────────────────────────────────────

@mcp.tool()
def mark_task_wip(task_id: int, context: str = "") -> dict:
    """Start working on a task — sets status to in_progress, stamps last_worked_at, logs activity.

    Args:
        task_id: The task to start.
        context: Optional context breadcrumb (e.g. "debugging the auth flow").
    """
    try:
        from roost.task_service import mark_wip
        task = mark_wip(task_id, context)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return _task_full(task)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_next_task() -> dict:
    """Get the single most important task to work on right now.

    Returns focused tasks first, then highest urgency score.
    """
    try:
        from roost.task_service import get_next_task as _next
        task = _next()
        if not task:
            return {"message": "No open tasks found"}
        return _task_full(task)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def pick_random_task(energy_budget: str | None = None) -> dict:
    """Pick a random task weighted by urgency score.

    Useful when you can't decide what to work on.

    Args:
        energy_budget: Optional filter — "low" (light tasks only),
            "moderate" (light + moderate), or "high"/omit (all tasks).
    """
    try:
        from roost.task_service import pick_task
        task = pick_task(energy_budget)
        if not task:
            return {"message": "No matching tasks found"}
        return _task_full(task)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def set_task_last_worked(task_id: int) -> dict:
    """Stamp a task with the current time as last_worked_at.

    Updates urgency score (recency factor).

    Args:
        task_id: The task to stamp.
    """
    try:
        from roost.task_service import set_last_worked_at
        task = set_last_worked_at(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return _task_full(task)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_task_progress(project: str | None = None) -> dict:
    """Get progress summary — total tasks, done count, percentage, breakdown by status.

    Args:
        project: Optional project name to scope the progress report.
    """
    try:
        from roost.task_service import get_progress
        return get_progress(project)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_today_activity() -> dict:
    """Get all activity logged today — actions, tools used, artifacts produced."""
    try:
        from roost.task_service import get_today_activity as _today
        entries = _today()
        return {"count": len(entries), "activity": entries}
    except Exception as e:
        return {"error": str(e)}


# ── Task Assignments (RACI) ──────────────────────────────────────────

@mcp.tool()
def assign_task(task_id: int, contact_id: int, role: str = "R", notes: str = "") -> dict:
    """Assign a contact to a task with a RACI role.

    Args:
        task_id: The task to assign.
        contact_id: The contact to assign.
        role: Role code — R (Responsible), A (Accountable), C (Consulted),
            I (Informed), V (Verifier), S (Signatory). Default: R.
        notes: Optional assignment notes.
    """
    try:
        from roost.task_service import create_task_assignment, get_task, get_contact
        from roost.models import TaskAssignmentCreate

        task = get_task(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        contact = get_contact(contact_id)
        if not contact:
            return {"error": f"Contact #{contact_id} not found"}

        data = TaskAssignmentCreate(
            task_id=task_id,
            contact_id=contact_id,
            role=role.upper(),
            notes=notes,
        )
        assignment = create_task_assignment(data)
        return _assignment_dict(assignment)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def unassign_task(assignment_id: int) -> dict:
    """Remove a task assignment.

    Args:
        assignment_id: The assignment ID to remove.
    """
    try:
        from roost.task_service import delete_task_assignment
        ok = delete_task_assignment(assignment_id)
        if not ok:
            return {"error": f"Assignment #{assignment_id} not found"}
        return {"ok": True, "message": f"Assignment #{assignment_id} removed"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_task_assignments_mcp(
    task_id: int | None = None,
    contact_id: int | None = None,
    role: str | None = None,
) -> dict:
    """List task assignments with optional filters.

    Args:
        task_id: Filter by task.
        contact_id: Filter by contact.
        role: Filter by role code (R, A, C, I, V, S).
    """
    try:
        from roost.task_service import list_task_assignments
        assignments = list_task_assignments(
            task_id=task_id,
            contact_id=contact_id,
            role=role,
        )
        return {
            "count": len(assignments),
            "assignments": [_assignment_dict(a) for a in assignments],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Helpers ──────────────────────────────────────────────────────────

def _task_brief(task) -> dict:
    """Compact task representation for lists."""
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        "urgency_score": task.urgency_score,
        "effort_estimate": task.effort_estimate,
        "context_note": task.context_note,
    }


def _task_full(task) -> dict:
    """Full task representation — mirrors tools_tasks._task_dict."""
    return {
        "id": task.id,
        "position": task.sort_order,
        "title": task.title,
        "description": task.description,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        "deadline": str(task.deadline) if task.deadline else None,
        "project_id": task.project_id,
        "project_name": task.project_name,
        "parent_task_id": task.parent_task_id,
        "task_type": task.task_type,
        "energy_level": task.energy_level,
        "effort_estimate": task.effort_estimate,
        "someday": bool(task.someday),
        "focus_date": task.focus_date,
        "urgency_score": task.urgency_score,
        "context_note": task.context_note,
        "last_worked_at": task.last_worked_at,
        "subtask_count": task.subtask_count,
        "subtask_done": task.subtask_done,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }


def _assignment_dict(assignment) -> dict:
    """Convert a TaskAssignment model to a plain dict."""
    return {
        "id": assignment.id,
        "task_id": assignment.task_id,
        "task_title": assignment.task_title,
        "contact_id": assignment.contact_id,
        "contact_name": assignment.contact_name,
        "entity_name": assignment.entity_name,
        "role": assignment.role,
        "role_label": assignment.role_label,
        "notes": assignment.notes,
        "created_at": assignment.created_at,
    }


# ── Focus Mode ──────────────────────────────────────────────────────

@mcp.tool()
def get_focus_tasks() -> dict:
    """Get today's focused tasks (max 3).

    Returns tasks pinned for today's focus session.
    """
    try:
        from roost.services.tasks import get_focus_tasks as _get

        tasks = _get()
        return {
            "count": len(tasks),
            "tasks": [_task_dict(t) for t in tasks],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def set_focus(task_id: int) -> dict:
    """Pin a task as today's focus (max 3 at a time).

    Args:
        task_id: The task ID to focus on.
    """
    try:
        from roost.services.tasks import set_focus as _set

        result = _set(task_id)
        resp = {"ok": result["ok"], "message": result["message"]}
        if result.get("task"):
            resp["task"] = _task_dict(result["task"])
        return resp
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def clear_focus(task_id: int | None = None) -> dict:
    """Remove a task from today's focus, or clear all focus tasks.

    Args:
        task_id: Specific task to unfocus. If omitted, clears ALL focus tasks for today.
    """
    try:
        from roost.services.tasks import clear_focus as _clear

        count = _clear(task_id)
        return {"ok": True, "cleared": count}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def suggest_focus(limit: int = 3) -> dict:
    """Suggest top tasks for today's focus when none are pinned.

    Args:
        limit: Number of suggestions (default 3).
    """
    try:
        from roost.services.tasks import suggest_focus as _suggest

        tasks = _suggest(limit=limit)
        return {
            "count": len(tasks),
            "tasks": [_task_dict(t) for t in tasks],
        }
    except Exception as e:
        return {"error": str(e)}


# ── Shelve / Someday ────────────────────────────────────────────────

@mcp.tool()
def shelve_task(task_id: int) -> dict:
    """Move a task to the someday pile (hidden from default views).

    Args:
        task_id: The task ID to shelve.
    """
    try:
        from roost.services.tasks import shelve_task as _shelve

        task = _shelve(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return {"ok": True, "task": _task_dict(task)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def unshelve_task(task_id: int) -> dict:
    """Bring a task back from someday to the active list.

    Args:
        task_id: The task ID to unshelve.
    """
    try:
        from roost.services.tasks import unshelve_task as _unshelve

        task = _unshelve(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return {"ok": True, "task": _task_dict(task)}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_someday_tasks(limit: int = 20) -> dict:
    """List tasks in the someday pile.

    Args:
        limit: Maximum tasks to return (default 20).
    """
    try:
        from roost.services.tasks import list_someday_tasks as _list

        tasks = _list(limit=limit)
        return {
            "count": len(tasks),
            "tasks": [_task_dict(t) for t in tasks],
        }
    except Exception as e:
        return {"error": str(e)}
