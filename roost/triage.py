"""Smart triage engine — urgency scoring and today view.

Multi-tenant: urgency recalculation and today view are scoped by user_id.
Auto-resolved from current user context if not explicitly provided.

Computes urgency scores for tasks based on priority, deadline pressure,
staleness, and work-in-progress status. Designed for ADHD-friendly
task management where "everything feels urgent" needs smart ranking.
"""

import logging
from datetime import datetime, timedelta
from roost.database import get_connection

logger = logging.getLogger("roost.triage")

# Priority weights
PRIORITY_WEIGHTS = {
    "urgent": 100,
    "high": 60,
    "medium": 30,
    "low": 10,
}

# In-progress boost
IN_PROGRESS_BOOST = 25

# Staleness: +2/day for tasks untouched 7+ days, max 20
STALENESS_THRESHOLD_DAYS = 7
STALENESS_PER_DAY = 2
STALENESS_MAX = 20


def _resolve_uid(user_id: int | None = None) -> int:
    """Resolve user_id from context if not provided."""
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


def _deadline_pressure(deadline_str: str | None) -> float:
    """Calculate deadline pressure score from deadline string."""
    if not deadline_str:
        return 0.0

    try:
        deadline = datetime.strptime(deadline_str[:19], "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        try:
            deadline = datetime.strptime(deadline_str[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            return 0.0

    now = datetime.now()
    delta = deadline - now
    days = delta.total_seconds() / 86400

    if days < -7:
        return 150  # very overdue
    elif days < 0:
        return 100 + min(50, abs(days) * 7)  # overdue, escalating
    elif days < 1:
        return 90  # due today
    elif days < 2:
        return 70  # due tomorrow
    elif days < 7:
        return 50  # this week
    elif days < 14:
        return 25  # next week
    else:
        return 10  # later


def _staleness_bonus(updated_at: str | None, last_worked_at: str | None) -> float:
    """Bonus for tasks that haven't been touched in a while."""
    ref = last_worked_at or updated_at
    if not ref:
        return 0.0

    try:
        ref_dt = datetime.strptime(ref[:19], "%Y-%m-%dT%H:%M:%S")
    except (ValueError, TypeError):
        try:
            ref_dt = datetime.strptime(ref[:19], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return 0.0

    days_stale = (datetime.now() - ref_dt).days
    if days_stale < STALENESS_THRESHOLD_DAYS:
        return 0.0

    extra_days = days_stale - STALENESS_THRESHOLD_DAYS
    return min(STALENESS_MAX, extra_days * STALENESS_PER_DAY)


def compute_urgency(task_row: dict) -> float:
    """Compute urgency score for a single task.

    Args:
        task_row: dict with keys: priority, deadline, status, updated_at, last_worked_at

    Returns:
        float urgency score (higher = more urgent)
    """
    priority = task_row.get("priority", "medium")
    pw = PRIORITY_WEIGHTS.get(priority, 30)

    dp = _deadline_pressure(task_row.get("deadline"))
    sb = _staleness_bonus(task_row.get("updated_at"), task_row.get("last_worked_at"))

    ipb = IN_PROGRESS_BOOST if task_row.get("status") == "in_progress" else 0

    return pw + dp + sb + ipb


def recalculate_all_urgency_scores(user_id: int | None = None) -> int:
    """Batch recompute urgency scores for all active (non-done) tasks.

    Scoped to the current user. Returns number of tasks updated.
    """
    uid = _resolve_uid(user_id)
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, priority, deadline, status, updated_at, last_worked_at
           FROM tasks WHERE status != 'done' AND user_id = ?""",
        (uid,),
    ).fetchall()

    count = 0
    for row in rows:
        task_dict = dict(row)
        score = compute_urgency(task_dict)
        conn.execute(
            "UPDATE tasks SET urgency_score = ? WHERE id = ?",
            (score, task_dict["id"]),
        )
        count += 1

    conn.commit()
    conn.close()
    logger.info("Recalculated urgency scores for %d tasks (user=%d)", count, uid)
    return count


def get_today_tasks(exclude_paused_projects: bool = True,
                    user_id: int | None = None) -> dict:
    """Get today's focus view: overdue, due today, in progress, top urgent.

    Scoped to the current user.
    Returns dict with keys: overdue, due_today, in_progress, top_urgent
    Each value is a list of task dicts.
    """
    uid = _resolve_uid(user_id)
    conn = get_connection()
    now = datetime.now()
    today_start = now.strftime("%Y-%m-%d 00:00:00")
    today_end = now.strftime("%Y-%m-%d 23:59:59")

    base_filter = "t.status != 'done' AND (t.someday = 0 OR t.someday IS NULL) AND t.user_id = ?"
    if exclude_paused_projects:
        base_filter += " AND (p.status IS NULL OR p.status != 'paused')"

    base_query = f"""
        SELECT t.*, p.name as project_name
        FROM tasks t
        LEFT JOIN projects p ON t.project_id = p.id
        WHERE {base_filter}
    """

    # Overdue tasks
    overdue = conn.execute(
        base_query + " AND t.deadline IS NOT NULL AND t.deadline < ? ORDER BY t.deadline",
        (uid, today_start),
    ).fetchall()

    # Due today
    due_today = conn.execute(
        base_query + " AND t.deadline >= ? AND t.deadline <= ? ORDER BY t.deadline",
        (uid, today_start, today_end),
    ).fetchall()

    # In progress
    in_progress = conn.execute(
        base_query + " AND t.status = 'in_progress' ORDER BY t.urgency_score DESC",
        (uid,),
    ).fetchall()

    # Top urgent (excluding already shown)
    shown_ids = set()
    for row in list(overdue) + list(due_today) + list(in_progress):
        shown_ids.add(dict(row)["id"])

    all_urgent = conn.execute(
        base_query + " ORDER BY t.urgency_score DESC LIMIT 20",
        (uid,),
    ).fetchall()

    top_urgent = []
    for row in all_urgent:
        d = dict(row)
        if d["id"] not in shown_ids and len(top_urgent) < 5:
            top_urgent.append(row)

    conn.close()

    return {
        "overdue": [dict(r) for r in overdue],
        "due_today": [dict(r) for r in due_today],
        "in_progress": [dict(r) for r in in_progress],
        "top_urgent": [dict(r) for r in top_urgent],
    }
