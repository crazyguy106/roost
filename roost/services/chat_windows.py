"""Chat windows — mapping between tmux windows in a per-user roost session
and the lead / task / Chatwoot conversation each one is "about".

A "window" here is a row in ``chat_windows``. The tmux side is named
``roost-<user_id>:<tmux_window_name>``; the web tty page renders a tab
per row and the WebSocket bridge attaches to the named tmux window when
the operator clicks a tab.

Phase 1.6a (this module) is data-only: no tmux side-effects, no web
routes — those land in 1.6b and 1.6c. The interface is small on
purpose so the UI layer above it stays thin.

Eviction model (used by 1.6c picker)
------------------------------------
``recommend_evictee(user_id)`` returns the *coldest* window — the one
the operator is least likely to miss:

1. NULL ``last_inbound_at`` (no external event ever landed here) ranks
   colder than any non-NULL inbound.
2. Then oldest ``last_inbound_at`` wins.
3. Tie-broken by oldest ``last_active_at``.

It returns ``None`` when the user is below the cap — the picker only
needs a recommendation when forced to evict.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

from roost.database import db_connection

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_CAP = 5


# ── Data classes ─────────────────────────────────────────────────────


@dataclass
class ChatWindow:
    id: int
    user_id: int
    tmux_window_name: str
    title: str
    linked_entity_type: str
    linked_entity_id: Optional[int]
    last_topic: str
    last_active_at: str
    last_inbound_at: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ChatWindow":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            tmux_window_name=row["tmux_window_name"],
            title=row["title"],
            linked_entity_type=row["linked_entity_type"] or "",
            linked_entity_id=row["linked_entity_id"],
            last_topic=row["last_topic"] or "",
            last_active_at=row["last_active_at"],
            last_inbound_at=row["last_inbound_at"],
            created_at=row["created_at"],
        )


class ChatWindowError(Exception):
    """Service-level error — caller should surface to the operator."""


# ── Queries ──────────────────────────────────────────────────────────


def list_windows(user_id: int) -> list[ChatWindow]:
    """Return all windows for ``user_id``, most-recently-active first."""
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM chat_windows
            WHERE user_id = ?
            ORDER BY last_active_at DESC, id DESC
            """,
            (user_id,),
        ).fetchall()
    return [ChatWindow.from_row(r) for r in rows]


def get_window(window_id: int) -> Optional[ChatWindow]:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT * FROM chat_windows WHERE id = ?", (window_id,)
        ).fetchone()
    return ChatWindow.from_row(row) if row else None


def get_window_by_tmux_name(user_id: int, tmux_window_name: str) -> Optional[ChatWindow]:
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM chat_windows
            WHERE user_id = ? AND tmux_window_name = ?
            """,
            (user_id, tmux_window_name),
        ).fetchone()
    return ChatWindow.from_row(row) if row else None


def count_windows(user_id: int) -> int:
    with db_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM chat_windows WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


# ── Mutations ────────────────────────────────────────────────────────


def create_window(
    user_id: int,
    *,
    tmux_window_name: str,
    title: str,
    linked_entity_type: str = "",
    linked_entity_id: Optional[int] = None,
    last_topic: str = "",
) -> ChatWindow:
    """Insert a new window row.

    Raises ``ChatWindowError`` if ``(user_id, tmux_window_name)`` collides
    or any required field is empty.
    """
    if not tmux_window_name or not title:
        raise ChatWindowError("tmux_window_name and title are required")

    with db_connection() as conn:
        try:
            cur = conn.execute(
                """
                INSERT INTO chat_windows (
                    user_id, tmux_window_name, title,
                    linked_entity_type, linked_entity_id, last_topic
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id, tmux_window_name, title,
                    linked_entity_type, linked_entity_id, last_topic,
                ),
            )
            conn.commit()
            new_id = cur.lastrowid
        except sqlite3.IntegrityError as exc:
            raise ChatWindowError(
                f"window {tmux_window_name!r} already exists for user {user_id}"
            ) from exc

    window = get_window(int(new_id))
    assert window is not None
    return window


def touch_active(window_id: int) -> None:
    """Bump ``last_active_at`` to now — call on attach / tab focus."""
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE chat_windows
               SET last_active_at = datetime('now')
             WHERE id = ?
            """,
            (window_id,),
        )
        conn.commit()


def mark_inbound(window_id: int) -> None:
    """Stamp ``last_inbound_at`` (and bump active) — call when an
    external event (inbound msg, agent reply) lands in this window."""
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE chat_windows
               SET last_inbound_at = datetime('now'),
                   last_active_at  = datetime('now')
             WHERE id = ?
            """,
            (window_id,),
        )
        conn.commit()


def link_to_entity(
    window_id: int, entity_type: str, entity_id: Optional[int]
) -> None:
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE chat_windows
               SET linked_entity_type = ?,
                   linked_entity_id   = ?
             WHERE id = ?
            """,
            (entity_type or "", entity_id, window_id),
        )
        conn.commit()


def set_topic(window_id: int, topic: str) -> None:
    with db_connection() as conn:
        conn.execute(
            "UPDATE chat_windows SET last_topic = ? WHERE id = ?",
            (topic or "", window_id),
        )
        conn.commit()


def delete_window(window_id: int) -> None:
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows WHERE id = ?", (window_id,))
        conn.commit()


# ── Cap-and-evict recommendation ─────────────────────────────────────


def recommend_evictee(
    user_id: int, cap: int = DEFAULT_WINDOW_CAP
) -> Optional[ChatWindow]:
    """Return the coldest window when the user is at or above ``cap``.

    See module docstring for the ordering. Returns ``None`` when under
    the cap (the picker has no work to do).
    """
    with db_connection() as conn:
        row = conn.execute(
            """
            SELECT * FROM chat_windows
             WHERE user_id = ?
               AND (SELECT COUNT(*) FROM chat_windows WHERE user_id = ?) >= ?
             ORDER BY
                CASE WHEN last_inbound_at IS NULL THEN 0 ELSE 1 END ASC,
                last_inbound_at ASC,
                last_active_at ASC,
                id ASC
             LIMIT 1
            """,
            (user_id, user_id, cap),
        ).fetchone()
    return ChatWindow.from_row(row) if row else None
