"""Claude Code session management — SQLite-backed CRUD with tmux integration."""

import logging
import shutil
import subprocess
from datetime import datetime, timezone

from roost.database import get_connection
from roost.models import ClaudeSession, ClaudeSessionCreate

logger = logging.getLogger("roost.session_service")

# ── Schema ────────────────────────────────────────────────────────

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS claude_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    tmux_session    TEXT NOT NULL DEFAULT 'claude-dev',
    tmux_window     INTEGER,
    project_dir     TEXT NOT NULL DEFAULT '/home/dev/projects',
    status          TEXT NOT NULL DEFAULT 'active',
    last_connected_at TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
)
"""


def _ensure_table():
    conn = get_connection()
    try:
        conn.execute(_TABLE_SQL)
        conn.commit()
    finally:
        conn.close()


_ensure_table()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_model(row) -> ClaudeSession:
    return ClaudeSession(**dict(row))


# ── tmux helpers ──────────────────────────────────────────────────

def _has_tmux() -> bool:
    return shutil.which("tmux") is not None


def _tmux_run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["tmux"] + args,
        capture_output=True, text=True, timeout=10,
        check=check,
    )


def _tmux_create_window(session: str, name: str, project_dir: str) -> int | None:
    """Create a tmux window running Claude Code, return window index."""
    if not _has_tmux():
        return None
    try:
        # Create new window in the target session
        result = _tmux_run([
            "new-window", "-t", session, "-n", name,
            "-P", "-F", "#{window_index}",
            "-c", project_dir,
            "claude",
        ], check=True)
        return int(result.stdout.strip())
    except Exception:
        logger.warning("Failed to create tmux window for session '%s'", name, exc_info=True)
        return None


def _tmux_select_window(session: str, window: int) -> bool:
    if not _has_tmux():
        return False
    try:
        _tmux_run(["select-window", "-t", f"{session}:{window}"])
        return True
    except Exception:
        return False


def _tmux_kill_window(session: str, window: int) -> bool:
    if not _has_tmux():
        return False
    try:
        _tmux_run(["kill-window", "-t", f"{session}:{window}"], check=False)
        return True
    except Exception:
        return False


# ── CRUD ──────────────────────────────────────────────────────────

def list_sessions(status: str | None = None) -> list[ClaudeSession]:
    """List sessions, optionally filtered by status."""
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM claude_sessions WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM claude_sessions ORDER BY updated_at DESC",
            ).fetchall()
        return [_row_to_model(r) for r in rows]
    finally:
        conn.close()


def get_session(session_id: int) -> ClaudeSession | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM claude_sessions WHERE id = ?", (session_id,),
        ).fetchone()
        return _row_to_model(row) if row else None
    finally:
        conn.close()


def create_session(
    data: ClaudeSessionCreate,
    user_id: int | None = None,
    user_email: str = "",
) -> ClaudeSession:
    """Create a new Claude Code session (with optional tmux window)."""
    now = _now()
    tmux_window = _tmux_create_window(data.tmux_session, data.name, data.project_dir)

    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO claude_sessions (name, tmux_session, tmux_window, project_dir, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?)""",
            (data.name, data.tmux_session, tmux_window, data.project_dir, now, now),
        )
        conn.commit()
        return get_session(cur.lastrowid)  # type: ignore[return-value]
    finally:
        conn.close()


def connect_session(session_id: int) -> ClaudeSession | None:
    """Mark session as connected and select its tmux window."""
    session = get_session(session_id)
    if not session or session.status == "closed":
        return None

    now = _now()
    if session.tmux_window is not None:
        _tmux_select_window(session.tmux_session, session.tmux_window)

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE claude_sessions SET last_connected_at = ?, updated_at = ? WHERE id = ?",
            (now, now, session_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id)


def close_session(session_id: int) -> ClaudeSession | None:
    """Close a session — kill tmux window and mark as closed."""
    session = get_session(session_id)
    if not session:
        return None

    if session.tmux_window is not None:
        _tmux_kill_window(session.tmux_session, session.tmux_window)

    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE claude_sessions SET status = 'closed', tmux_window = NULL, updated_at = ? WHERE id = ?",
            (now, session_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_session(session_id)


def get_ttyd_status() -> dict:
    """Check if ttyd (web terminal) is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "ttyd"], capture_output=True, text=True, timeout=5,
        )
        running = result.returncode == 0
    except Exception:
        running = False
    return {"running": running}
