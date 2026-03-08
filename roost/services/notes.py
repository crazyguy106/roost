"""Notes and command log operations.

Multi-tenant: notes are scoped by user_id. Auto-resolved from current
user context if not explicitly provided.
"""

import logging
from datetime import datetime
from roost.database import get_connection
from roost.models import Note, NoteCreate, CommandLogEntry

logger = logging.getLogger("roost.services.notes")

__all__ = [
    "create_note",
    "get_note",
    "list_notes",
    "delete_note",
    "log_command",
    "get_command_log",
]


def _resolve_uid(user_id: int | None = None) -> int:
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


def create_note(data: NoteCreate, source: str = "", user_id: int | None = None) -> Note:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO notes (title, content, tag, project_id, user_id) VALUES (?, ?, ?, ?, ?)",
        (data.title, data.content, data.tag, data.project_id, uid),
    )
    conn.commit()
    note = get_note(cur.lastrowid, user_id=uid)
    conn.close()

    try:
        from roost.events import emit, NOTE_CREATED
        emit(NOTE_CREATED, {"note": note, "source": source})
    except ImportError:
        pass

    return note


def get_note(note_id: int, user_id: int | None = None) -> Note | None:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    row = conn.execute(
        """SELECT n.*, p.name as project_name
           FROM notes n LEFT JOIN projects p ON n.project_id = p.id
           WHERE n.id = ? AND (n.user_id = ? OR n.user_id IS NULL)""",
        (note_id, uid),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Note(**dict(row))


def list_notes(tag: str | None = None, project: str | None = None,
               limit: int = 50, user_id: int | None = None) -> list[Note]:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    query = """SELECT n.*, p.name as project_name
               FROM notes n LEFT JOIN projects p ON n.project_id = p.id
               WHERE n.user_id = ?"""
    params: list = [uid]

    if tag:
        query += " AND n.tag = ?"
        params.append(tag)
    if project:
        query += " AND LOWER(p.name) = LOWER(?)"
        params.append(project)

    query += " ORDER BY n.created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [Note(**dict(r)) for r in rows]


def delete_note(note_id: int, user_id: int | None = None) -> bool:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    # Always scope delete to the current user (prevents cross-user deletion)
    cur = conn.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, uid))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# -- Command Log ------------------------------------------------------------------

def log_command(source: str, command: str, output: str = "",
                exit_code: int | None = None, user_id: int | None = None) -> None:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    conn.execute(
        """INSERT INTO command_log (source, command, output, exit_code, user_id)
           VALUES (?, ?, ?, ?, ?)""",
        (source, command, output, exit_code, uid),
    )
    conn.commit()
    conn.close()


def get_command_log(limit: int = 50, user_id: int | None = None) -> list[CommandLogEntry]:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM command_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (uid, limit),
    ).fetchall()
    conn.close()
    return [CommandLogEntry(**dict(r)) for r in rows]
