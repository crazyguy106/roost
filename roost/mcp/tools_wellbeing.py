"""MCP tools for neurodivergent-friendly features — routines, spoons, shutdown/resume, streaks."""

from roost.mcp.server import mcp


# ── Routines ─────────────────────────────────────────────────────────

@mcp.tool()
def get_routine(name: str) -> dict:
    """Get a routine with its items and today's completion status.

    Args:
        name: Routine name (e.g. "morning", "evening", "shutdown").
    """
    try:
        from roost.task_service import get_routine as _get
        routine = _get(name)
        if not routine:
            return {"error": f"Routine '{name}' not found"}
        return routine
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def add_routine_item(routine_name: str, title: str, time_of_day: str = "morning") -> dict:
    """Add an item to a routine, creating the routine if it doesn't exist.

    Args:
        routine_name: Routine name (e.g. "morning", "evening").
        title: The routine item title (e.g. "Review email", "Drink water").
        time_of_day: When this routine runs — morning, afternoon, evening. Default: morning.
    """
    try:
        from roost.task_service import add_routine_item as _add
        routine = _add(routine_name, title, time_of_day)
        return routine
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def remove_routine_item(item_id: int) -> dict:
    """Remove an item from a routine.

    Args:
        item_id: The routine item ID to remove.
    """
    try:
        from roost.task_service import remove_routine_item as _remove
        ok = _remove(item_id)
        if not ok:
            return {"error": f"Routine item #{item_id} not found"}
        return {"ok": True, "message": f"Routine item #{item_id} removed"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def complete_routine_item(item_id: int) -> dict:
    """Mark a routine item as done for today.

    Args:
        item_id: The routine item ID to complete.
    """
    try:
        from roost.task_service import complete_routine_item as _complete
        ok = _complete(item_id)
        return {"ok": ok, "item_id": item_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def uncomplete_routine_item(item_id: int) -> dict:
    """Uncheck a routine item for today.

    Args:
        item_id: The routine item ID to uncomplete.
    """
    try:
        from roost.task_service import uncomplete_routine_item as _uncomplete
        ok = _uncomplete(item_id)
        if not ok:
            return {"error": f"No completion found for item #{item_id} today"}
        return {"ok": True, "item_id": item_id}
    except Exception as e:
        return {"error": str(e)}


# ── Spoon / Energy Budget ───────────────────────────────────────────

@mcp.tool()
def get_spoon_status() -> dict:
    """Get current spoon/energy budget status.

    Returns budget, spent today, remaining, and percentage.
    Auto-resets on a new day.
    """
    try:
        from roost.task_service import get_spoon_status as _status
        return _status()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def set_spoon_budget(budget: int) -> dict:
    """Set the daily spoon budget.

    Spoon costs: light task = 1, moderate = 2, heavy = 4.

    Args:
        budget: Daily spoon count (e.g. 15 for a normal day, 8 for a low day).
    """
    try:
        from roost.task_service import set_spoon_budget as _set
        return _set(budget)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def spend_spoons(effort: str) -> dict:
    """Manually deduct spoons for an effort level.

    Costs: light = 1, moderate = 2, heavy = 4.
    Usually called automatically by complete_task, but can be used manually.

    Args:
        effort: One of: light, moderate, heavy.
    """
    try:
        from roost.task_service import spend_spoons as _spend
        return _spend(effort)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def reset_spoons() -> dict:
    """Reset today's spoon count to zero (manual reset)."""
    try:
        from roost.task_service import reset_spoons as _reset
        return _reset()
    except Exception as e:
        return {"error": str(e)}


# ── Shutdown / Resume ────────────────────────────────────────────────

@mcp.tool()
def check_shutdown_status() -> dict:
    """Check if shutdown mode is currently active.

    Returns whether shutdown is active and summary details if so.
    """
    try:
        from roost.task_service import is_shutdown_active, get_shutdown_summary
        active = is_shutdown_active()
        result = {"shutdown_active": active}
        if active:
            summary = get_shutdown_summary()
            if summary:
                result.update(summary)
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def execute_shutdown() -> dict:
    """Execute the shutdown protocol — end of day wind-down.

    This will:
    1. Pause all in-progress tasks (set to todo with PAUSED: context)
    2. Defer today's deadlines by +1 day
    3. Log activity against all paused tasks
    """
    try:
        from roost.task_service import execute_shutdown as _shutdown
        return _shutdown()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def execute_resume() -> dict:
    """Resume from shutdown — start of day.

    This will:
    1. Restore all paused tasks to in_progress
    2. Strip PAUSED: prefix from context notes
    3. Clear shutdown state
    """
    try:
        from roost.task_service import execute_resume as _resume
        return _resume()
    except Exception as e:
        return {"error": str(e)}


# ── Streaks ──────────────────────────────────────────────────────────

@mcp.tool()
def get_streak_status() -> dict:
    """Get current task completion streak.

    Returns current streak, best streak, and whether today is a milestone.
    """
    try:
        from roost.task_service import get_streak, get_celebration
        streak = get_streak()
        streak["celebration"] = get_celebration() if streak["is_milestone"] else None
        return streak
    except Exception as e:
        return {"error": str(e)}
