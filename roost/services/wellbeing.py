"""Wellbeing: routines, spoons, shutdown/resume.

Multi-tenant: routines and shutdown/resume are scoped by user_id.
Spoons already work via get_setting/set_setting which are user-scoped.
"""

import json
import logging
from datetime import datetime
from roost.database import get_connection
from roost.services.settings import get_setting, set_setting, delete_setting

logger = logging.getLogger("roost.services.wellbeing")

__all__ = [
    "get_routine",
    "complete_routine_item",
    "uncomplete_routine_item",
    "add_routine_item",
    "remove_routine_item",
    "get_spoon_status",
    "spend_spoons",
    "set_spoon_budget",
    "reset_spoons",
    "is_shutdown_active",
    "get_shutdown_summary",
    "execute_shutdown",
    "execute_resume",
]


def _resolve_uid(user_id: int | None = None) -> int:
    """Resolve user_id from context if not provided."""
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


def get_routine(name: str, user_id: int | None = None) -> dict | None:
    """Return routine with items and today's completion status."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    routine = conn.execute(
        "SELECT * FROM routines WHERE name = ? AND user_id = ?", (name, uid)
    ).fetchone()
    if not routine:
        conn.close()
        return None

    today = datetime.now().strftime("%Y-%m-%d")
    items = conn.execute(
        """SELECT ri.*,
                  EXISTS(
                      SELECT 1 FROM routine_completions rc
                      WHERE rc.routine_item_id = ri.id AND rc.completed_date = ?
                  ) as completed
           FROM routine_items ri
           WHERE ri.routine_id = ?
           ORDER BY ri.sort_order""",
        (today, routine["id"]),
    ).fetchall()
    conn.close()

    return {
        "id": routine["id"],
        "name": routine["name"],
        "time_of_day": routine["time_of_day"],
        "items": [dict(i) for i in items],
    }


def complete_routine_item(item_id: int) -> bool:
    """Mark a routine item as done for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO routine_completions (routine_item_id, completed_date) VALUES (?, ?)",
            (item_id, today),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def uncomplete_routine_item(item_id: int) -> bool:
    """Uncheck a routine item for today."""
    today = datetime.now().strftime("%Y-%m-%d")
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM routine_completions WHERE routine_item_id = ? AND completed_date = ?",
        (item_id, today),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def add_routine_item(routine_name: str, title: str, time_of_day: str = "morning",
                     user_id: int | None = None) -> dict:
    """Add an item to a routine, creating the routine if needed."""
    uid = _resolve_uid(user_id)
    conn = get_connection()

    # Create routine if not exists (scoped by user_id)
    conn.execute(
        "INSERT OR IGNORE INTO routines (name, time_of_day, user_id) VALUES (?, ?, ?)",
        (routine_name, time_of_day, uid),
    )
    conn.commit()

    routine = conn.execute(
        "SELECT id FROM routines WHERE name = ? AND user_id = ?", (routine_name, uid)
    ).fetchone()
    routine_id = routine["id"]

    # Get next sort_order
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 as next_pos FROM routine_items WHERE routine_id = ?",
        (routine_id,),
    ).fetchone()

    conn.execute(
        "INSERT INTO routine_items (routine_id, title, sort_order) VALUES (?, ?, ?)",
        (routine_id, title, row["next_pos"]),
    )
    conn.commit()
    conn.close()

    return get_routine(routine_name, user_id=uid)


def remove_routine_item(item_id: int) -> bool:
    """Remove a routine item."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM routine_items WHERE id = ?", (item_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# -- Spoon Budget ----------------------------------------------------------------
# Spoons use get_setting/set_setting which are already user-scoped (Sprint 2).

SPOON_COSTS = {"light": 1, "moderate": 2, "heavy": 4}


def get_spoon_status() -> dict:
    """Return spoon budget status, auto-resetting on new day."""
    today = datetime.now().strftime("%Y-%m-%d")
    stored_date = get_setting("spoons_date")

    if stored_date != today:
        # Auto-reset for new day
        set_setting("spoons_spent_today", "0")
        set_setting("spoons_date", today)

    budget = int(get_setting("spoon_budget") or "15")
    spent = int(get_setting("spoons_spent_today") or "0")
    remaining = max(0, budget - spent)
    pct = round(remaining / budget * 100) if budget > 0 else 0

    return {
        "budget": budget,
        "spent": spent,
        "remaining": remaining,
        "percentage": pct,
    }


def spend_spoons(effort: str) -> dict:
    """Deduct spoons for a completed task. Returns updated status."""
    cost = SPOON_COSTS.get(effort, 2)
    today = datetime.now().strftime("%Y-%m-%d")
    stored_date = get_setting("spoons_date")

    if stored_date != today:
        set_setting("spoons_spent_today", "0")
        set_setting("spoons_date", today)

    spent = int(get_setting("spoons_spent_today") or "0")
    spent += cost
    set_setting("spoons_spent_today", str(spent))

    return get_spoon_status()


def set_spoon_budget(budget: int) -> dict:
    """Set daily spoon budget."""
    set_setting("spoon_budget", str(budget))
    return get_spoon_status()


def reset_spoons() -> dict:
    """Reset today's spoon count."""
    set_setting("spoons_spent_today", "0")
    return get_spoon_status()


# -- Shutdown Protocol ------------------------------------------------------------

def is_shutdown_active() -> bool:
    """Check if shutdown mode is currently active."""
    active = get_setting("shutdown_active")
    date = get_setting("shutdown_date")
    today = datetime.now().strftime("%Y-%m-%d")
    return active == "1" and date == today


def get_shutdown_summary() -> dict | None:
    """Get shutdown summary for resume prompt."""
    if not is_shutdown_active():
        return None
    paused_ids_raw = get_setting("shutdown_paused_ids")
    paused_ids = json.loads(paused_ids_raw) if paused_ids_raw else []
    return {
        "active": True,
        "date": get_setting("shutdown_date"),
        "paused_count": len(paused_ids),
        "paused_ids": paused_ids,
    }


def execute_shutdown(user_id: int | None = None) -> dict:
    """Shutdown protocol: pause all WIP, defer today's deadlines."""
    uid = _resolve_uid(user_id)
    from roost.services.tasks import log_activity

    conn = get_connection()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    now_str = now.isoformat(timespec="seconds")

    # 1. Find in_progress tasks for this user (not someday)
    wip_rows = conn.execute(
        """SELECT id, context_note FROM tasks
           WHERE status = 'in_progress' AND (someday = 0 OR someday IS NULL)
                 AND user_id = ?""",
        (uid,),
    ).fetchall()

    paused_ids = []
    for row in wip_rows:
        tid = row["id"]
        ctx = row["context_note"] or ""
        new_ctx = f"PAUSED: {ctx}" if ctx else "PAUSED"
        conn.execute(
            "UPDATE tasks SET status = 'todo', context_note = ?, updated_at = ? WHERE id = ?",
            (new_ctx, now_str, tid),
        )
        paused_ids.append(tid)

    # 2. Defer today's deadlines by +1 day (user's tasks only)
    today_start = f"{today} 00:00:00"
    today_end = f"{today} 23:59:59"
    deferred = conn.execute(
        """UPDATE tasks SET deadline = datetime(deadline, '+1 day'), updated_at = ?
           WHERE status != 'done' AND deadline >= ? AND deadline <= ?
                 AND (someday = 0 OR someday IS NULL) AND user_id = ?""",
        (now_str, today_start, today_end, uid),
    )
    deferred_count = deferred.rowcount

    conn.commit()
    conn.close()

    # 3. Store state in settings (already user-scoped)
    set_setting("shutdown_active", "1")
    set_setting("shutdown_date", today)
    set_setting("shutdown_paused_ids", json.dumps(paused_ids))

    # Log activity
    for tid in paused_ids:
        log_activity(tid, "paused", "shutdown", user_id=uid)

    return {
        "paused_count": len(paused_ids),
        "deferred_count": deferred_count,
        "task_ids": paused_ids,
    }


def execute_resume(user_id: int | None = None) -> dict:
    """Resume from shutdown: restore paused tasks."""
    uid = _resolve_uid(user_id)
    from roost.services.tasks import log_activity

    paused_ids_raw = get_setting("shutdown_paused_ids")
    paused_ids = json.loads(paused_ids_raw) if paused_ids_raw else []

    conn = get_connection()
    now_str = datetime.now().isoformat(timespec="seconds")
    resumed = 0

    for tid in paused_ids:
        row = conn.execute(
            "SELECT context_note FROM tasks WHERE id = ? AND user_id = ?", (tid, uid)
        ).fetchone()
        if row:
            ctx = row["context_note"] or ""
            # Strip PAUSED: prefix
            if ctx.startswith("PAUSED: "):
                ctx = ctx[8:]
            elif ctx == "PAUSED":
                ctx = ""
            conn.execute(
                "UPDATE tasks SET status = 'in_progress', context_note = ?, updated_at = ? WHERE id = ?",
                (ctx, now_str, tid),
            )
            resumed += 1

    conn.commit()
    conn.close()

    # Clear shutdown state (already user-scoped)
    set_setting("shutdown_active", "0")
    delete_setting("shutdown_paused_ids")

    # Log activity
    for tid in paused_ids:
        log_activity(tid, "resumed", "resume from shutdown", user_id=uid)

    return {"resumed_count": resumed, "task_ids": paused_ids}
