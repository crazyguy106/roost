"""MCP tools for task CRUD operations."""

import logging

from roost.mcp.server import mcp

logger = logging.getLogger("roost.mcp.tools_tasks")


@mcp.tool()
def create_task(
    title: str,
    description: str = "",
    priority: str = "medium",
    status: str = "todo",
    deadline: str = "",
    project_id: int | None = None,
    project_name: str = "",
    parent_task_id: int | None = None,
    energy_level: str = "medium",
    context_note: str = "",
    effort_estimate: str = "moderate",
    someday: bool = False,
) -> dict:
    """Create a new task.

    Accepts natural language for dates and shorthand for enums — these are
    normalized automatically (e.g. "tomorrow", "h" for high, "wip").

    Args:
        title: Task title (required).
        description: Longer description of what needs to be done.
        priority: low, medium, high, urgent (or shorthand: l, m, h, u).
        status: todo, in_progress, done, blocked (or shorthand: wip, done).
        deadline: Deadline — accepts "tomorrow", "next friday", "+3d", "eow",
            or ISO format YYYY-MM-DD.
        project_id: Project ID (int). Alternative: use project_name instead.
        project_name: Project name (fuzzy matched). Resolves to project_id.
        parent_task_id: Optional parent task ID for subtasks.
        energy_level: low, medium, high (or shorthand: l, m, h).
        context_note: Quick context breadcrumb.
        effort_estimate: light, moderate, heavy (or shorthand: ez, mod, hard).
        someday: If true, shelve the task (hidden from default views).
    """
    try:
        from roost.task_service import create_task as _create
        from roost.models import TaskCreate
        from roost.mcp.normalize import normalize

        # Normalize terse inputs
        fields = normalize({
            "deadline": deadline,
            "priority": priority,
            "status": status,
            "energy_level": energy_level,
            "effort_estimate": effort_estimate,
            **({"project_name": project_name} if project_name and not project_id else {}),
        })

        resolved_project = fields.get("project_id") or project_id

        data = TaskCreate(
            title=title,
            description=description,
            priority=fields.get("priority", priority),
            status=fields.get("status", status),
            deadline=fields.get("deadline") or None,
            project_id=resolved_project,
            parent_task_id=parent_task_id,
            energy_level=fields.get("energy_level", energy_level),
            context_note=context_note,
            effort_estimate=fields.get("effort_estimate", effort_estimate),
            someday=someday,
        )
        task = _create(data, source="mcp")
        return _task_dict(task)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_tasks(
    status: str | None = None,
    project: str | None = None,
    priority: str | None = None,
    deadline_filter: str | None = None,
    order_by: str | None = None,
    limit: int = 20,
    top_level_only: bool = True,
    energy_level: str | None = None,
    include_someday: bool = False,
    focus_only: bool = False,
    effort_estimate: str | None = None,
) -> dict:
    """List tasks with optional filters.

    Args:
        status: Filter by status: todo, in_progress, done, blocked.
        project: Filter by project name (exact match).
        priority: Filter by priority: low, medium, high, urgent.
        deadline_filter: One of: overdue, today, this_week, has_deadline, no_deadline.
        order_by: Sort order: urgency, deadline, updated. Default is priority.
        limit: Maximum number of tasks to return (default 20).
        top_level_only: If true, exclude subtasks (default true).
        energy_level: Filter by energy level: low, medium, high.
        include_someday: If true, include shelved/someday tasks (default false).
        focus_only: If true, only return tasks focused for today (default false).
        effort_estimate: Filter by effort: light, moderate, heavy.
    """
    try:
        from roost.task_service import list_tasks as _list

        tasks = _list(
            status=status,
            project=project,
            priority=priority,
            deadline_filter=deadline_filter,
            order_by=order_by,
            limit=limit,
            top_level_only=top_level_only,
            energy_level=energy_level,
            exclude_paused_projects=True,
            include_someday=include_someday,
            focus_only=focus_only,
            effort_estimate=effort_estimate,
        )
        result = {
            "count": len(tasks),
            "tasks": [_task_dict(t) for t in tasks],
        }
        # Include streak and spoon context
        try:
            from roost.task_service import get_streak, get_spoon_status
            result["streak"] = get_streak()
            result["spoon_status"] = get_spoon_status()
        except Exception:
            logger.debug("Failed to enrich response with streak/spoon data", exc_info=True)
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_task(
    task_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    deadline: str | None = None,
    project_id: int | None = None,
    energy_level: str | None = None,
    context_note: str | None = None,
    effort_estimate: str | None = None,
    someday: bool | None = None,
    focus_date: str | None = None,
) -> dict:
    """Update an existing task's fields.

    Accepts natural language for dates and shorthand for enums.

    Args:
        task_id: The task ID to update (required).
        title: New title.
        description: New description.
        status: New status: todo, in_progress, done, blocked (or: wip, done).
        priority: New priority: low, medium, high, urgent (or: l, m, h, u).
        deadline: New deadline — "tomorrow", "next friday", "+3d", ISO format,
            or empty string to clear.
        project_id: New project ID.
        energy_level: New energy level: low, medium, high (or: l, m, h).
        context_note: New context breadcrumb.
        effort_estimate: New effort: light, moderate, heavy (or: ez, mod, hard).
        someday: Set to true to shelve, false to unshelve.
        focus_date: Focus date — "today", "tomorrow", ISO format, or empty to clear.
    """
    try:
        from roost.task_service import update_task as _update
        from roost.models import TaskUpdate
        from roost.mcp.normalize import normalize

        # Collect fields that need normalization
        to_normalize = {}
        if status is not None and status:
            to_normalize["status"] = status
        if priority is not None and priority:
            to_normalize["priority"] = priority
        if deadline is not None and deadline:
            to_normalize["deadline"] = deadline
        if energy_level is not None and energy_level:
            to_normalize["energy_level"] = energy_level
        if effort_estimate is not None and effort_estimate:
            to_normalize["effort_estimate"] = effort_estimate
        if focus_date is not None and focus_date:
            to_normalize["focus_date"] = focus_date

        if to_normalize:
            normalized = normalize(to_normalize)
        else:
            normalized = {}

        kwargs = {}
        if title is not None:
            kwargs["title"] = title
        if description is not None:
            kwargs["description"] = description
        if status is not None:
            kwargs["status"] = normalized.get("status", status)
        if priority is not None:
            kwargs["priority"] = normalized.get("priority", priority)
        if deadline is not None:
            kwargs["deadline"] = normalized.get("deadline", deadline) or None
        if project_id is not None:
            kwargs["project_id"] = project_id
        if energy_level is not None:
            kwargs["energy_level"] = normalized.get("energy_level", energy_level)
        if context_note is not None:
            kwargs["context_note"] = context_note
        if effort_estimate is not None:
            kwargs["effort_estimate"] = normalized.get("effort_estimate", effort_estimate)
        if someday is not None:
            kwargs["someday"] = someday
        if focus_date is not None:
            kwargs["focus_date"] = normalized.get("focus_date", focus_date) or None

        data = TaskUpdate(**kwargs)
        task = _update(task_id, data, source="mcp")
        if not task:
            return {"error": f"Task {task_id} not found"}
        return _task_dict(task)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def complete_task(task_id: int) -> dict:
    """Mark a task as done.

    Args:
        task_id: The task ID to complete.
    """
    try:
        from roost.task_service import complete_task as _complete

        task = _complete(task_id, source="mcp")
        if not task:
            return {"error": f"Task {task_id} not found"}
        return _task_dict(task)
    except Exception as e:
        return {"error": str(e)}


def _task_dict(task) -> dict:
    """Convert a Task model to a plain dict for MCP response."""
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
        "subtask_count": task.subtask_count,
        "subtask_done": task.subtask_done,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
    }
