"""Cross-channel conversation memory — context that persists across interfaces.

Stores key facts, decisions, and preferences mentioned in any channel
(Telegram, Web, MCP) so the agent has context regardless of which
interface the user is currently on.

Memory entries are short-lived (auto-expire after 7 days by default)
to avoid stale context. Important items can be pinned to persist longer.
"""

from __future__ import annotations

import json
import logging

from roost.database import db_connection

logger = logging.getLogger(__name__)

# Max entries per user to prevent bloat
MAX_MEMORY_ENTRIES = 100


def remember(
    content: str,
    channel: str = "",
    category: str = "fact",
    user_id: str = "",
    pinned: bool = False,
) -> int | None:
    """Store a memory entry.

    Args:
        content: The thing to remember (max 500 chars).
        channel: Source channel (telegram, web, mcp, api).
        category: One of: fact, decision, preference, context.
        user_id: User the memory belongs to.
        pinned: Pinned entries don't auto-expire.
    """
    content = content[:500]

    try:
        with db_connection() as conn:
            # Enforce max entries (remove oldest unpinned if over limit)
            count = conn.execute(
                "SELECT COUNT(*) as cnt FROM conversation_memory WHERE user_id = ?",
                (user_id,),
            ).fetchone()["cnt"]

            if count >= MAX_MEMORY_ENTRIES:
                conn.execute(
                    """DELETE FROM conversation_memory
                       WHERE id IN (
                           SELECT id FROM conversation_memory
                           WHERE user_id = ? AND pinned = 0
                           ORDER BY created_at ASC LIMIT ?
                       )""",
                    (user_id, count - MAX_MEMORY_ENTRIES + 1),
                )

            cur = conn.execute(
                """INSERT INTO conversation_memory
                   (content, channel, category, user_id, pinned)
                   VALUES (?, ?, ?, ?, ?)""",
                (content, channel, category, user_id, 1 if pinned else 0),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        logger.debug("Failed to save memory", exc_info=True)
        return None


def recall(
    query: str = "",
    category: str = "",
    limit: int = 20,
    user_id: str = "",
    include_expired: bool = False,
) -> list[dict]:
    """Recall memory entries matching a query.

    By default, only returns entries from the last 7 days (unless pinned).
    """
    try:
        with db_connection() as conn:
            conditions = ["user_id = ?"]
            params: list = [user_id]

            if category:
                conditions.append("category = ?")
                params.append(category)

            if not include_expired:
                conditions.append(
                    "(pinned = 1 OR created_at > datetime('now', '-7 days'))"
                )

            if query:
                conditions.append("content LIKE ?")
                params.append(f"%{query}%")

            where = " AND ".join(conditions)
            params.append(limit)

            rows = conn.execute(
                f"""SELECT * FROM conversation_memory
                    WHERE {where}
                    ORDER BY pinned DESC, created_at DESC LIMIT ?""",
                params,
            ).fetchall()
            return [_memory_to_dict(r) for r in rows]
    except Exception:
        logger.debug("Failed to recall memory", exc_info=True)
        return []


def forget(memory_id: int) -> dict:
    """Delete a specific memory entry."""
    try:
        with db_connection() as conn:
            conn.execute(
                "DELETE FROM conversation_memory WHERE id = ?",
                (memory_id,),
            )
            conn.commit()
            return {"ok": True, "deleted": memory_id}
    except Exception as e:
        return {"error": str(e)}


def pin_memory(memory_id: int, pinned: bool = True) -> dict:
    """Pin or unpin a memory entry."""
    try:
        with db_connection() as conn:
            conn.execute(
                "UPDATE conversation_memory SET pinned = ? WHERE id = ?",
                (1 if pinned else 0, memory_id),
            )
            conn.commit()
            return {"ok": True, "memory_id": memory_id, "pinned": pinned}
    except Exception as e:
        return {"error": str(e)}


def get_context_prompt(user_id: str = "") -> str:
    """Build a context section from recent memories for the agent prompt.

    Returns empty string if no relevant memories.
    """
    memories = recall(limit=15, user_id=user_id)
    if not memories:
        return ""

    lines = [
        "\n## Recent Context (from previous conversations)\n",
    ]

    for m in memories:
        pin = "[pinned] " if m.get("pinned") else ""
        channel = f"({m.get('channel', '?')})" if m.get("channel") else ""
        lines.append(f"- {pin}{m['content']} {channel}")

    return "\n".join(lines) + "\n"


def cleanup_expired(days: int = 7) -> int:
    """Remove expired (unpinned, older than N days) memory entries."""
    try:
        with db_connection() as conn:
            cur = conn.execute(
                """DELETE FROM conversation_memory
                   WHERE pinned = 0 AND created_at < datetime('now', ?)""",
                (f"-{days} days",),
            )
            conn.commit()
            return cur.rowcount
    except Exception:
        return 0


def _memory_to_dict(row) -> dict:
    d = dict(row)
    if "pinned" in d:
        d["pinned"] = bool(d["pinned"])
    return d
