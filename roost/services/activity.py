"""System-of-record activity log — fast-path action audit trail.

Sits on top of the existing `activity_log` table (see
`roost.database.SCHEMA_V10` + the FA-edition migration that adds
`actor`, `ok`, `result_json`). The original task-coupled writers live in
`roost.services.tasks` (`log_activity`, `get_today_activity`,
`get_task_activity`) — those keep their semantics. This module is the
counterpart for *system* writes:

- Fire-and-forget — never raises. A failed audit insert must not break
  the surface that called it (a Telegram approve button, a webhook
  handler, an MCP tool).
- task-uncoupled by default — `task_id` stays NULL, and the
  productivity-stats counter in `stats_service.py` filters those out.
- Maps cleanly to existing columns: `entity_type` → `artifact_type`,
  `entity_id` → `artifact_ref`. No schema rename needed.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from roost.database import get_connection

logger = logging.getLogger("roost.services.activity")

# Cap the snippet so a stray giant payload can't bloat the row.
_SNIPPET_MAX = 500


def _truncate(text: str | None, limit: int = _SNIPPET_MAX) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def log_action(
    actor: str,
    action: str,
    *,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    ok: bool = True,
    result: Any = None,
    snippet: str | None = None,
    actor_ref: str | None = None,
) -> None:
    """Record a system-of-record event. Fire-and-forget — never raises.

    Args:
        actor: Surface that triggered the action — telegram / web / cli /
            scheduler / system.
        action: Short verb (e.g. ``cadence.approve``, ``draft.send``).
        entity_type: Category of the thing acted on (cadence_draft,
            guardian_draft, conversation, lead). Stored as `artifact_type`.
        entity_id: Identifier of the thing acted on. Stored as
            `artifact_ref` (stringified).
        ok: True on success, False on failure.
        result: Optional structured payload — JSON-serialised into
            ``result_json``.
        snippet: Short human-readable description for ``detail``.
            Truncated to 500 chars.
        actor_ref: Optional reference to *who* did it (e.g. telegram
            user id). Stored prefixed in ``tool_name`` for grep-ability.
    """
    try:
        ref_str = "" if entity_id is None else str(entity_id)
        type_str = entity_type or ""
        detail = _truncate(snippet)
        tool_name = actor_ref or ""
        result_json = ""
        if result is not None:
            try:
                result_json = json.dumps(result, default=str)[: _SNIPPET_MAX * 4]
            except (TypeError, ValueError):
                result_json = ""

        conn = get_connection()
        conn.execute(
            """INSERT INTO activity_log
               (task_id, action, detail, tool_name,
                artifact_type, artifact_ref,
                actor, ok, result_json, user_id)
               VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
            (
                action,
                detail,
                tool_name,
                type_str,
                ref_str,
                actor,
                1 if ok else 0,
                result_json,
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Fire-and-forget: an audit failure must never bubble up.
        logger.exception(
            "log_action swallow: actor=%s action=%s entity=%s/%s",
            actor, action, entity_type, entity_id,
        )


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("result_json"):
        try:
            d["result"] = json.loads(d["result_json"])
        except (TypeError, ValueError):
            d["result"] = None
    else:
        d["result"] = None
    d["ok"] = bool(d.get("ok", 1))
    return d


def recent(limit: int = 10, *, system_only: bool = False) -> list[dict]:
    """Return the newest audit rows, newest first.

    Args:
        limit: Max rows to return.
        system_only: If True, restrict to fast-path / system writes
            (``task_id IS NULL``). If False, return all rows.
    """
    conn = get_connection()
    where = "WHERE task_id IS NULL" if system_only else ""
    rows = conn.execute(
        f"""SELECT id, created_at, actor, action, artifact_type AS entity_type,
                  artifact_ref AS entity_id, detail AS snippet, ok, result_json,
                  tool_name AS actor_ref, task_id
           FROM activity_log
           {where}
           ORDER BY id DESC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def for_entity(entity_type: str, entity_id: str | int, limit: int = 20) -> list[dict]:
    """Return audit rows for a specific entity, newest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, created_at, actor, action, artifact_type AS entity_type,
                  artifact_ref AS entity_id, detail AS snippet, ok, result_json,
                  tool_name AS actor_ref, task_id
           FROM activity_log
           WHERE artifact_type = ? AND artifact_ref = ?
           ORDER BY id DESC
           LIMIT ?""",
        (entity_type, str(entity_id), limit),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def since(ts_iso: str, limit: int = 100) -> list[dict]:
    """Return audit rows newer than ``ts_iso`` (ISO 8601), oldest first."""
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, created_at, actor, action, artifact_type AS entity_type,
                  artifact_ref AS entity_id, detail AS snippet, ok, result_json,
                  tool_name AS actor_ref, task_id
           FROM activity_log
           WHERE created_at > ?
           ORDER BY id ASC
           LIMIT ?""",
        (ts_iso, limit),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
