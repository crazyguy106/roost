"""Productivity statistics service — analytics over tasks, time, and activity.

Queries the SQLite database for completed task history, daily counts,
and comprehensive productivity summaries.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("roost.stats")


def get_completed_history(days: int = 30, project: str | None = None) -> dict:
    """Get history of completed tasks over a period."""
    from roost.database import get_connection

    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    if project:
        rows = conn.execute(
            """SELECT t.id, t.title, t.priority, t.effort_estimate,
                      t.updated_at as completed_at, p.name as project_name
               FROM tasks t
               LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.status = 'done' AND t.updated_at >= ? AND p.name = ?
               ORDER BY t.updated_at DESC""",
            (cutoff, project),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT t.id, t.title, t.priority, t.effort_estimate,
                      t.updated_at as completed_at, p.name as project_name
               FROM tasks t
               LEFT JOIN projects p ON t.project_id = p.id
               WHERE t.status = 'done' AND t.updated_at >= ?
               ORDER BY t.updated_at DESC""",
            (cutoff,),
        ).fetchall()
    conn.close()

    tasks = []
    for row in rows:
        tasks.append({
            "id": row["id"],
            "title": row["title"],
            "priority": row["priority"],
            "effort": row["effort_estimate"],
            "completed_at": row["completed_at"],
            "project": row["project_name"],
        })

    return {
        "days": days,
        "count": len(tasks),
        "tasks": tasks,
    }


def get_daily_completions(days: int = 14) -> dict:
    """Get daily task completion counts."""
    from roost.database import get_connection

    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    rows = conn.execute(
        """SELECT DATE(updated_at) as date, COUNT(*) as count
           FROM tasks
           WHERE status = 'done' AND updated_at >= ?
           GROUP BY DATE(updated_at)
           ORDER BY date""",
        (cutoff,),
    ).fetchall()
    conn.close()

    # Fill in zero-count days
    daily = {}
    for row in rows:
        daily[row["date"]] = row["count"]

    result = []
    for i in range(days):
        date = (datetime.now() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        result.append({"date": date, "count": daily.get(date, 0)})

    total = sum(d["count"] for d in result)
    avg = total / days if days > 0 else 0

    return {
        "days": days,
        "total_completed": total,
        "average_per_day": round(avg, 1),
        "daily": result,
    }


def get_productivity_summary(days: int = 7) -> dict:
    """Comprehensive productivity summary."""
    from roost.database import get_connection

    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    # Completed tasks
    completed = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'done' AND updated_at >= ?",
        (cutoff,),
    ).fetchone()["cnt"]

    # By priority
    priority_rows = conn.execute(
        """SELECT priority, COUNT(*) as cnt FROM tasks
           WHERE status = 'done' AND updated_at >= ?
           GROUP BY priority ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    by_priority = {row["priority"]: row["cnt"] for row in priority_rows}

    # By project
    project_rows = conn.execute(
        """SELECT COALESCE(p.name, 'No project') as project, COUNT(*) as cnt
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.status = 'done' AND t.updated_at >= ?
           GROUP BY project ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    by_project = {row["project"]: row["cnt"] for row in project_rows}

    # By effort
    effort_rows = conn.execute(
        """SELECT effort_estimate, COUNT(*) as cnt FROM tasks
           WHERE status = 'done' AND updated_at >= ?
           GROUP BY effort_estimate ORDER BY cnt DESC""",
        (cutoff,),
    ).fetchall()
    by_effort = {row["effort_estimate"]: row["cnt"] for row in effort_rows}

    # Current open tasks
    open_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE status != 'done' AND (someday = 0 OR someday IS NULL)"
    ).fetchone()["cnt"]

    blocked_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE status = 'blocked'"
    ).fetchone()["cnt"]

    # Activity log count
    activity_count = conn.execute(
        "SELECT COUNT(*) as cnt FROM activity_log WHERE created_at >= ?",
        (cutoff,),
    ).fetchone()["cnt"]

    conn.close()

    # Spoon status
    spoon_info = {}
    try:
        from roost.task_service import get_spoon_status
        spoon_info = get_spoon_status()
    except Exception:
        logger.debug("Failed to fetch spoon status", exc_info=True)

    # Streak
    streak_info = {}
    try:
        from roost.task_service import get_streak
        streak_info = get_streak()
    except Exception:
        logger.debug("Failed to fetch streak info", exc_info=True)

    # Time tracking
    time_info = {}
    try:
        from roost.time_service import get_time_summary
        time_info = get_time_summary(days)
    except Exception:
        logger.debug("Failed to fetch time tracking summary", exc_info=True)

    avg_per_day = completed / days if days > 0 else 0

    return {
        "period_days": days,
        "tasks_completed": completed,
        "average_per_day": round(avg_per_day, 1),
        "open_tasks": open_count,
        "blocked_tasks": blocked_count,
        "activity_actions": activity_count,
        "by_priority": by_priority,
        "by_project": by_project,
        "by_effort": by_effort,
        "spoons": spoon_info,
        "streak": streak_info,
        "time_tracked": time_info,
    }


def get_weekly_review() -> dict:
    """Weekly review — completed, in progress, blocked, upcoming deadlines."""
    from roost.database import get_connection

    conn = get_connection()
    week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    next_week = (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d")

    # Completed this week
    completed = conn.execute(
        """SELECT t.id, t.title, t.priority, p.name as project_name, t.updated_at
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.status = 'done' AND t.updated_at >= ?
           ORDER BY t.updated_at DESC""",
        (week_ago,),
    ).fetchall()

    # Currently in progress
    in_progress = conn.execute(
        """SELECT t.id, t.title, t.priority, p.name as project_name, t.context_note
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.status = 'in_progress'
           ORDER BY t.urgency_score DESC""",
    ).fetchall()

    # Blocked
    blocked = conn.execute(
        """SELECT t.id, t.title, t.priority, p.name as project_name, t.context_note
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.status = 'blocked'
           ORDER BY t.priority DESC""",
    ).fetchall()

    # Upcoming deadlines (next 7 days)
    upcoming = conn.execute(
        """SELECT t.id, t.title, t.priority, t.deadline, p.name as project_name
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.status != 'done' AND t.deadline IS NOT NULL
                 AND t.deadline <= ?
           ORDER BY t.deadline""",
        (next_week,),
    ).fetchall()

    # Overdue
    today = datetime.now().strftime("%Y-%m-%d")
    overdue = conn.execute(
        """SELECT t.id, t.title, t.priority, t.deadline, p.name as project_name
           FROM tasks t LEFT JOIN projects p ON t.project_id = p.id
           WHERE t.status != 'done' AND t.deadline IS NOT NULL
                 AND t.deadline < ?
           ORDER BY t.deadline""",
        (today,),
    ).fetchall()

    conn.close()

    def _task_list(rows):
        return [dict(r) for r in rows]

    return {
        "completed": {"count": len(completed), "tasks": _task_list(completed)},
        "in_progress": {"count": len(in_progress), "tasks": _task_list(in_progress)},
        "blocked": {"count": len(blocked), "tasks": _task_list(blocked)},
        "upcoming_deadlines": {"count": len(upcoming), "tasks": _task_list(upcoming)},
        "overdue": {"count": len(overdue), "tasks": _task_list(overdue)},
    }
