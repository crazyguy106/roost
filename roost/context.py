"""Dynamic context assembly for the CAGE framework.

Builds a per-request system prompt by injecting persistent context:
- Context: active task, today's calendar, recent notes
- Align: user preferences (stored in DB)
- Goals: tasks due this week, active projects
- Examples: (future — not yet implemented)

The assembled context is appended to the static system prompt,
bounded by a token budget to avoid overloading the context window.
"""

import json
import logging
from datetime import datetime, timedelta

from roost.database import get_connection

logger = logging.getLogger("roost.context")

# Max characters of injected context (~500 tokens)
MAX_CONTEXT_CHARS = 2000


# ── User preferences (Align layer) ──────────────────────────────────

def get_preferences(user_id: str) -> dict[str, str]:
    """Load all user preferences from DB."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value FROM user_preferences WHERE user_id = ?",
            (str(user_id),),
        ).fetchall()
        return {row["key"]: row["value"] for row in rows}
    finally:
        conn.close()


def set_preference(user_id: str, key: str, value: str) -> None:
    """Set a user preference (upsert)."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO user_preferences (user_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, key) DO UPDATE SET
                   value = excluded.value,
                   updated_at = datetime('now')""",
            (str(user_id), key, value),
        )
        conn.commit()
    finally:
        conn.close()


def delete_preference(user_id: str, key: str) -> bool:
    """Delete a user preference. Returns True if deleted."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "DELETE FROM user_preferences WHERE user_id = ? AND key = ?",
            (str(user_id), key),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


# ── Chat history persistence ────────────────────────────────────────

def save_chat_history(session_id: str, role: str, content: str,
                      user_id: str = "") -> None:
    """Persist a single chat message to the database."""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO chat_history (session_id, user_id, role, content)
               VALUES (?, ?, ?, ?)""",
            (session_id, str(user_id), role, content[:10000]),
        )
        conn.commit()
    finally:
        conn.close()


def load_chat_history(session_id: str, limit: int = 40) -> list[dict]:
    """Load recent chat messages for a session from DB.

    Returns list of {"role": str, "content": str} dicts, oldest first.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT role, content FROM chat_history
               WHERE session_id = ?
               ORDER BY id DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        # Reverse to get chronological order
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
    finally:
        conn.close()


def prune_chat_history(days: int = 7) -> int:
    """Delete chat messages older than N days. Returns count deleted."""
    conn = get_connection()
    try:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        cursor = conn.execute(
            "DELETE FROM chat_history WHERE created_at < ?", (cutoff,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ── Context assembly (the CAGE builder) ─────────────────────────────

def build_agent_context(user_id: str, base_prompt: str) -> str:
    """Build a dynamic system prompt with CAGE context injected.

    Args:
        user_id: Telegram user ID.
        base_prompt: Static system prompt (behavior rules, tool guidance).

    Returns:
        Enhanced system prompt with injected context, bounded by token budget.
    """
    sections = []

    # ── Align: user preferences ──
    prefs = get_preferences(user_id)
    if prefs:
        pref_lines = [f"- {k}: {v}" for k, v in prefs.items()]
        sections.append(
            "## User Preferences\n" + "\n".join(pref_lines)
        )

    # ── Context: active state ──
    context_parts = []

    # Active/focus task
    try:
        from roost import task_service
        active = task_service.get_active_task(user_id=str(user_id))
        if active:
            context_parts.append(f"Active task: #{active['id']} {active['title']}")
    except Exception:
        pass

    # Today's calendar (brief)
    try:
        from roost.calendar_service import get_merged_today
        result = get_merged_today()
        events = result.get("events", [])
        if events:
            event_strs = [
                f"- {e.get('start', '??')}: {e['summary']}"
                for e in events[:5]
            ]
            context_parts.append("Today's calendar:\n" + "\n".join(event_strs))
    except Exception:
        pass

    # Recent notes (last 3)
    try:
        conn = get_connection()
        notes = conn.execute(
            """SELECT content FROM notes
               WHERE (user_id = ? OR user_id IS NULL)
               ORDER BY created_at DESC LIMIT 3""",
            (str(user_id),),
        ).fetchall()
        conn.close()
        if notes:
            note_strs = [f"- {n['content'][:100]}" for n in notes]
            context_parts.append("Recent notes:\n" + "\n".join(note_strs))
    except Exception:
        pass

    if context_parts:
        sections.append("## Current Context\n" + "\n".join(context_parts))

    # ── Goals: upcoming tasks ──
    try:
        from roost import task_service
        tasks = task_service.list_tasks(status="todo", user_id=str(user_id))
        # Get tasks due soon (this week) or high priority
        urgent = [
            t for t in (tasks or [])
            if t.get("priority") in ("urgent", "high") or t.get("deadline")
        ][:5]
        if urgent:
            task_strs = [
                f"- #{t['id']} {t['title']}"
                + (f" (due: {t['deadline']})" if t.get("deadline") else "")
                + (f" [{t['priority']}]" if t.get("priority") in ("urgent", "high") else "")
                for t in urgent
            ]
            sections.append("## Upcoming Tasks\n" + "\n".join(task_strs))
    except Exception:
        pass

    # ── Assemble ──
    if not sections:
        return base_prompt

    context_block = "\n\n".join(sections)

    # Truncate to budget
    if len(context_block) > MAX_CONTEXT_CHARS:
        context_block = context_block[:MAX_CONTEXT_CHARS] + "\n... [context truncated]"

    return base_prompt + "\n\n---\n\n" + context_block
