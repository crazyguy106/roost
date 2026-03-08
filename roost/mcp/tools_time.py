"""MCP tools for time tracking — start/stop timers linked to tasks."""

from roost.mcp.server import mcp


@mcp.tool()
def start_timer(task_id: int, note: str = "") -> dict:
    """Start a time tracking timer for a task.

    Stops any currently running timer first.

    Args:
        task_id: The task to track time against.
        note: Optional note describing what you're working on.
    """
    try:
        from roost.time_service import start_timer as _start
        return _start(task_id, note)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def stop_timer() -> dict:
    """Stop the currently running timer.

    Records the time entry with duration.
    """
    try:
        from roost.time_service import stop_timer as _stop
        return _stop()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_running_timer() -> dict:
    """Check if a timer is currently running.

    Returns the active timer details or a message if none is running.
    """
    try:
        from roost.time_service import get_running_timer as _get
        return _get()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_time_entries(
    task_id: int = 0,
    days: int = 7,
    limit: int = 50,
) -> dict:
    """Get time tracking entries.

    Args:
        task_id: Filter by task ID (0 = all tasks).
        days: Number of days to look back (default 7).
        limit: Maximum entries to return (default 50).
    """
    try:
        from roost.time_service import get_time_entries as _get
        return _get(task_id or None, days, limit)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_time_summary(days: int = 7) -> dict:
    """Get time tracking summary — total hours per task over a period.

    Args:
        days: Number of days to summarise (default 7).
    """
    try:
        from roost.time_service import get_time_summary as _summary
        return _summary(days)
    except Exception as e:
        return {"error": str(e)}
