"""MCP tools for productivity statistics and analytics."""

from roost.mcp.server import mcp


@mcp.tool()
def get_completed_task_history(days: int = 30, project: str = "") -> dict:
    """Get history of completed tasks over a period.

    Args:
        days: Number of days to look back (default 30).
        project: Optional project name filter.
    """
    try:
        from roost.stats_service import get_completed_history
        return get_completed_history(days, project or None)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_daily_completion_counts(days: int = 14) -> dict:
    """Get daily task completion counts for trend analysis.

    Returns a list of {date, count} pairs for the last N days.

    Args:
        days: Number of days to look back (default 14).
    """
    try:
        from roost.stats_service import get_daily_completions
        return get_daily_completions(days)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_productivity_summary(days: int = 7) -> dict:
    """Get a comprehensive productivity summary.

    Includes: tasks completed, average per day, by priority breakdown,
    by project breakdown, spoon usage, streak info, and time tracked.

    Args:
        days: Number of days to summarise (default 7).
    """
    try:
        from roost.stats_service import get_productivity_summary
        return get_productivity_summary(days)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_weekly_review() -> dict:
    """Get a weekly review summary — what was done, what's pending, what's blocked.

    Covers the last 7 days with actionable insights.
    """
    try:
        from roost.stats_service import get_weekly_review
        return get_weekly_review()
    except Exception as e:
        return {"error": str(e)}
