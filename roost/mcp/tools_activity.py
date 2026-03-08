"""MCP tools for task-linked activity tracking."""

from roost.mcp.server import mcp


@mcp.tool()
def set_active_task(task_id: int) -> dict:
    """Set the active task for the current work session.

    All subsequent log_activity calls will default to this task.
    Validates the task exists and is not already done.

    Args:
        task_id: The task ID to set as active.
    """
    try:
        from roost.task_service import (
            get_task as _get_task,
            set_active_task as _set_active,
        )

        task = _get_task(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}
        if task.status == "done":
            return {"error": f"Task #{task_id} is already done — pick an open task"}

        _set_active(task_id)
        return {
            "active_task": {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "project_id": task.project_id,
            },
            "message": f"Active task set to #{task_id}: {task.title}",
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_active_task() -> dict:
    """Return the current active task (id, title, status, project) or null if unset."""
    try:
        from roost.task_service import (
            get_active_task as _get_active,
            get_task as _get_task,
        )

        task_id = _get_active()
        if task_id is None:
            return {"active_task": None}

        task = _get_task(task_id)
        if not task:
            # Stale reference — clean up
            from roost.task_service import clear_active_task as _clear
            _clear()
            return {"active_task": None, "note": f"Cleared stale reference to #{task_id}"}

        return {
            "active_task": {
                "id": task.id,
                "title": task.title,
                "status": task.status,
                "project_id": task.project_id,
            }
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def log_activity(
    action: str,
    detail: str = "",
    task_id: int | None = None,
    tool_name: str = "",
    artifact_type: str = "",
    artifact_ref: str = "",
) -> dict:
    """Log an activity entry against a task.

    If task_id is not provided, falls back to the active task.

    Args:
        action: What was done (e.g. 'sent_email', 'uploaded_file').
        detail: Human-readable description of the action.
        task_id: Optional task ID. Falls back to active task if omitted.
        tool_name: The MCP tool that performed the action.
        artifact_type: Category of artifact (email, file, notion_page, etc.).
        artifact_ref: Identifier for the artifact (thread_id, path, page_id).
    """
    try:
        from roost.task_service import (
            log_activity as _log,
            get_active_task as _get_active,
        )

        resolved_id = task_id if task_id is not None else _get_active()

        _log(
            task_id=resolved_id,
            action=action,
            detail=detail,
            tool_name=tool_name,
            artifact_type=artifact_type,
            artifact_ref=artifact_ref,
        )
        return {
            "logged": True,
            "task_id": resolved_id,
            "action": action,
            "tool_name": tool_name,
            "artifact_type": artifact_type,
            "artifact_ref": artifact_ref,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_task_activity(task_id: int, limit: int = 50) -> dict:
    """Return the activity trail for a task — actions taken, tools used, artifacts produced.

    Args:
        task_id: The task to retrieve activity for.
        limit: Maximum entries to return (default 50, newest first).
    """
    try:
        from roost.task_service import (
            get_task as _get_task,
            get_task_activity as _get_activity,
        )

        task = _get_task(task_id)
        if not task:
            return {"error": f"Task #{task_id} not found"}

        entries = _get_activity(task_id, limit=limit)
        return {
            "task_id": task_id,
            "task_title": task.title,
            "count": len(entries),
            "activity": entries,
        }
    except Exception as e:
        return {"error": str(e)}
