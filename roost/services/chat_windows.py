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
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Optional

from roost.database import db_connection

logger = logging.getLogger(__name__)

DEFAULT_WINDOW_CAP = 5

# Random suffix length for auto-generated tmux window names. 6 hex chars
# = 16M combinations, collision-free in practice for the small per-user
# row counts we expect.
_AUTO_NAME_RETRIES = 5
_AUTO_NAME_BYTES = 3


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
    tmux_window_alive: int = 1
    last_resume_cmd: Optional[str] = None

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
            tmux_window_alive=row["tmux_window_alive"],
            last_resume_cmd=row["last_resume_cmd"],
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


def auto_create(
    user_id: int,
    *,
    title: str,
    linked_entity_type: str = "",
    linked_entity_id: Optional[int] = None,
    last_topic: str = "",
) -> ChatWindow:
    """Create a window with a random unique ``tmux_window_name``.

    Used by the web API so callers don't have to invent names. Retries
    a few times if a random collision happens (overwhelmingly unlikely
    for the row counts we expect).
    """
    last_exc: Optional[Exception] = None
    for _ in range(_AUTO_NAME_RETRIES):
        name = "w-" + secrets.token_hex(_AUTO_NAME_BYTES)
        try:
            return create_window(
                user_id,
                tmux_window_name=name,
                title=title,
                linked_entity_type=linked_entity_type,
                linked_entity_id=linked_entity_id,
                last_topic=last_topic,
            )
        except ChatWindowError as exc:
            last_exc = exc
            continue
    raise ChatWindowError(
        "could not generate a unique window name after retries"
    ) from last_exc


def create_window_if_under_cap(
    user_id: int,
    cap: int,
    *,
    title: str,
    linked_entity_type: str = "",
    linked_entity_id: Optional[int] = None,
    last_topic: str = "",
) -> Optional[ChatWindow]:
    """Atomic check-and-insert: returns the new row, or ``None`` when the
    user is already at ``cap``. Closes the race between two concurrent
    ``POST /api/tty/windows`` requests both reading ``count == cap-1``.

    Uses ``BEGIN IMMEDIATE`` so the second connection blocks until the
    first commits; both then see the post-insert count.
    """
    last_exc: Optional[Exception] = None
    for _ in range(_AUTO_NAME_RETRIES):
        name = "w-" + secrets.token_hex(_AUTO_NAME_BYTES)
        with db_connection() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM chat_windows WHERE user_id = ?",
                    (user_id,),
                ).fetchone()
                if int(row["n"]) >= cap:
                    conn.execute("ROLLBACK")
                    return None
                try:
                    cur = conn.execute(
                        """
                        INSERT INTO chat_windows (
                            user_id, tmux_window_name, title,
                            linked_entity_type, linked_entity_id, last_topic
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            user_id, name, title,
                            linked_entity_type, linked_entity_id, last_topic,
                        ),
                    )
                    new_id = cur.lastrowid
                    conn.execute("COMMIT")
                except sqlite3.IntegrityError as exc:
                    conn.execute("ROLLBACK")
                    last_exc = exc
                    continue
            except sqlite3.OperationalError as exc:
                # busy / locked — retry from the top.
                last_exc = exc
                continue
        window = get_window(int(new_id))
        assert window is not None
        return window
    raise ChatWindowError(
        "could not generate a unique window name after retries"
    ) from last_exc


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


def mark_window_killed(window_id: int, resume_cmd: Optional[str] = None) -> None:
    """Idle-sweep killed the tmux window — keep the row so the operator
    can resume from the paused drawer."""
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE chat_windows
               SET tmux_window_alive = 0,
                   last_resume_cmd   = ?
             WHERE id = ?
            """,
            (resume_cmd, window_id),
        )
        conn.commit()


def mark_window_alive(window_id: int) -> None:
    """The tmux window exists again (just created / resumed)."""
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE chat_windows
               SET tmux_window_alive = 1,
                   last_active_at    = datetime('now')
             WHERE id = ?
            """,
            (window_id,),
        )
        conn.commit()


def list_paused_windows(user_id: int, limit: int = 15) -> list[ChatWindow]:
    """Paused windows (tmux dead, row still there), newest first."""
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM chat_windows
             WHERE user_id = ? AND tmux_window_alive = 0
             ORDER BY last_active_at DESC, id DESC
             LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [ChatWindow.from_row(r) for r in rows]


def list_idle_for_sweep(
    idle_minutes: int, *, limit: Optional[int] = None
) -> list[ChatWindow]:
    """All ALIVE windows whose `last_active_at` is older than the TTL,
    across users — caller (sweeper) iterates and kills tmux.

    ``limit`` (when set) caps the per-tick batch so the scheduler doesn't
    spend the whole tick driving tmux. Oldest first so the most-idle
    rows get reaped first."""
    with db_connection() as conn:
        sql = (
            "SELECT * FROM chat_windows "
            "WHERE tmux_window_alive = 1 "
            "  AND last_active_at <= datetime('now', ?) "
            "ORDER BY last_active_at ASC, id ASC"
        )
        params: tuple = (f"-{int(idle_minutes)} minutes",)
        if limit is not None and limit > 0:
            sql += " LIMIT ?"
            params = (*params, int(limit))
        rows = conn.execute(sql, params).fetchall()
    return [ChatWindow.from_row(r) for r in rows]


def list_coldest_alive(limit: int) -> list[ChatWindow]:
    """Coldest live windows globally — memory-pressure sweep target.

    Same ordering as recommend_evictee but cross-user and capped to
    `limit` rows."""
    with db_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM chat_windows
             WHERE tmux_window_alive = 1
             ORDER BY
                CASE WHEN last_inbound_at IS NULL THEN 0 ELSE 1 END ASC,
                last_inbound_at ASC,
                last_active_at ASC,
                id ASC
             LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [ChatWindow.from_row(r) for r in rows]


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
