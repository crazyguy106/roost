"""Time tracking service — start/stop timers linked to tasks.

Uses the SQLite database for persistence. Timer entries track
task_id, start_time, end_time, duration_seconds, and notes.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("roost.time")


def _ensure_table():
    """Create time_entries table if it doesn't exist."""
    from roost.database import get_connection
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
            note TEXT DEFAULT '',
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds INTEGER DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_time_entries_task ON time_entries(task_id);
        CREATE INDEX IF NOT EXISTS idx_time_entries_started ON time_entries(started_at);
    """)
    conn.commit()
    conn.close()


def start_timer(task_id: int, note: str = "") -> dict:
    """Start a timer for a task. Stops any running timer first."""
    from roost.database import get_connection
    from roost.task_service import get_task

    _ensure_table()

    task = get_task(task_id)
    if not task:
        return {"error": f"Task #{task_id} not found"}

    # Stop any running timer first
    running = _get_running_entry()
    stopped_info = None
    if running:
        stopped_info = _stop_entry(running)

    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO time_entries (task_id, note, started_at) VALUES (?, ?, ?)",
        (task_id, note, now),
    )
    conn.commit()
    conn.close()

    result = {
        "ok": True,
        "task_id": task_id,
        "task_title": task.title,
        "started_at": now,
        "note": note,
    }
    if stopped_info:
        result["stopped_previous"] = stopped_info

    logger.info("Started timer for task #%d: %s", task_id, task.title)
    return result


def stop_timer() -> dict:
    """Stop the currently running timer."""
    _ensure_table()

    running = _get_running_entry()
    if not running:
        return {"message": "No timer is currently running"}

    return _stop_entry(running)


def get_running_timer() -> dict:
    """Get the currently running timer, or None."""
    _ensure_table()

    running = _get_running_entry()
    if not running:
        return {"running": False, "message": "No timer is currently running"}

    started = datetime.fromisoformat(running["started_at"])
    elapsed = (datetime.now() - started).total_seconds()

    return {
        "running": True,
        "entry_id": running["id"],
        "task_id": running["task_id"],
        "note": running["note"],
        "started_at": running["started_at"],
        "elapsed_seconds": int(elapsed),
        "elapsed_formatted": _format_duration(int(elapsed)),
    }


def get_time_entries(
    task_id: int | None = None,
    days: int = 7,
    limit: int = 50,
) -> dict:
    """Get completed time entries."""
    from roost.database import get_connection

    _ensure_table()

    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    if task_id:
        rows = conn.execute(
            """SELECT te.*, t.title as task_title
               FROM time_entries te
               LEFT JOIN tasks t ON te.task_id = t.id
               WHERE te.task_id = ? AND te.ended_at IS NOT NULL
                     AND te.started_at >= ?
               ORDER BY te.started_at DESC LIMIT ?""",
            (task_id, cutoff, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT te.*, t.title as task_title
               FROM time_entries te
               LEFT JOIN tasks t ON te.task_id = t.id
               WHERE te.ended_at IS NOT NULL AND te.started_at >= ?
               ORDER BY te.started_at DESC LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
    conn.close()

    entries = []
    for row in rows:
        entries.append({
            "id": row["id"],
            "task_id": row["task_id"],
            "task_title": row["task_title"],
            "note": row["note"],
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
            "duration_seconds": row["duration_seconds"],
            "duration_formatted": _format_duration(row["duration_seconds"]),
        })

    total_seconds = sum(e["duration_seconds"] for e in entries)
    return {
        "count": len(entries),
        "total_seconds": total_seconds,
        "total_formatted": _format_duration(total_seconds),
        "entries": entries,
    }


def get_time_summary(days: int = 7) -> dict:
    """Get time summary grouped by task."""
    from roost.database import get_connection

    _ensure_table()

    conn = get_connection()
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")

    rows = conn.execute(
        """SELECT te.task_id, t.title as task_title,
                  COUNT(*) as entry_count,
                  SUM(te.duration_seconds) as total_seconds
           FROM time_entries te
           LEFT JOIN tasks t ON te.task_id = t.id
           WHERE te.ended_at IS NOT NULL AND te.started_at >= ?
           GROUP BY te.task_id
           ORDER BY total_seconds DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()

    tasks = []
    grand_total = 0
    for row in rows:
        total = row["total_seconds"] or 0
        grand_total += total
        tasks.append({
            "task_id": row["task_id"],
            "task_title": row["task_title"],
            "entry_count": row["entry_count"],
            "total_seconds": total,
            "total_formatted": _format_duration(total),
        })

    return {
        "days": days,
        "task_count": len(tasks),
        "grand_total_seconds": grand_total,
        "grand_total_formatted": _format_duration(grand_total),
        "tasks": tasks,
    }


# ── Helpers ───────────────────────────────────────────────────────────


def _get_running_entry():
    """Get the currently running time entry (ended_at IS NULL)."""
    from roost.database import get_connection
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM time_entries WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    return row


def _stop_entry(entry) -> dict:
    """Stop a running time entry and record duration."""
    from roost.database import get_connection
    from roost.task_service import get_task

    now = datetime.now()
    started = datetime.fromisoformat(entry["started_at"])
    duration = int((now - started).total_seconds())

    conn = get_connection()
    conn.execute(
        "UPDATE time_entries SET ended_at = ?, duration_seconds = ? WHERE id = ?",
        (now.isoformat(timespec="seconds"), duration, entry["id"]),
    )
    conn.commit()
    conn.close()

    task = get_task(entry["task_id"]) if entry["task_id"] else None
    task_title = task.title if task else "Unknown"

    logger.info("Stopped timer for task #%s: %s (%s)",
                entry["task_id"], task_title, _format_duration(duration))

    return {
        "ok": True,
        "entry_id": entry["id"],
        "task_id": entry["task_id"],
        "task_title": task_title,
        "started_at": entry["started_at"],
        "ended_at": now.isoformat(timespec="seconds"),
        "duration_seconds": duration,
        "duration_formatted": _format_duration(duration),
    }


def _format_duration(seconds: int) -> str:
    """Format seconds as human-readable duration."""
    if seconds < 60:
        return f"{seconds}s"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"
