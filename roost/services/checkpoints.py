"""Checkpoints — snapshot agent actions for rollback.

After each write tool call, saves a checkpoint with the tool name, args,
result, and a reverse action (where determinable). Users can /rollback
to undo the last N actions.

Size limit: checkpoint data is truncated to MAX_CHECKPOINT_BYTES to prevent
database bloat from large tool results.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from roost.database import db_connection

logger = logging.getLogger(__name__)

# Max size for stored args/result JSON (default 10KB each)
MAX_CHECKPOINT_BYTES = 10_000

# Tools that produce reversible actions (tool_name → reverse tool)
_REVERSE_MAP = {
    "create_task": "delete_task",
    "create_note": "delete_note",
    "create_project": "delete_project",
    "create_contact": "delete_contact",
    "create_entity": "delete_entity",
    "complete_task": "update_task",  # set status back to todo
    "update_task": "update_task",
    "update_note": "update_note",
    "update_project": "update_project",
    "update_contact": "update_contact",
    "update_entity": "update_entity",
    "add_routine_item": "remove_routine_item",
    "add_task_dependency": "remove_task_dependency",
    "add_contact_to_entity": "remove_contact_from_entity",
    "set_contact_identifier": "remove_contact_identifier",
    "calendar_create_event": "calendar_delete_event",
    "ms_calendar_create_event": "ms_calendar_delete_event",
    "notion_create_page": "notion_archive_page",
    "schedule_email": "cancel_scheduled_email",
}

# Tools worth checkpointing (writes, not reads)
_CHECKPOINT_TOOLS = set(_REVERSE_MAP.keys()) | {
    "send_email", "ms_send_email",
    "delete_task", "delete_note", "delete_project",
    "delete_contact", "delete_entity",
    "drive_upload", "ms_onedrive_upload",
    "notion_update_page", "notion_append_blocks",
    "docker_compose_up", "docker_compose_down",
    "ssh_exec", "scp_upload",
}


# Schema defined in database.py to avoid circular imports.


def save_checkpoint(
    run_id: str,
    tool_name: str,
    tool_args: dict,
    result: dict,
    user_id: str = "",
) -> int | None:
    """Save a checkpoint after a tool call. Returns checkpoint ID."""
    if tool_name not in _CHECKPOINT_TOOLS:
        return None

    try:
        args_json = json.dumps(tool_args, default=str)[:MAX_CHECKPOINT_BYTES]
        result_json = json.dumps(result, default=str)[:MAX_CHECKPOINT_BYTES]

        # Determine reverse action
        reverse_tool = _REVERSE_MAP.get(tool_name, "")
        reverse_args = _build_reverse_args(tool_name, tool_args, result)
        reverse_json = json.dumps(reverse_args, default=str)[:MAX_CHECKPOINT_BYTES] if reverse_args else ""

        with db_connection() as conn:
            cur = conn.execute(
                """INSERT INTO checkpoints
                   (run_id, tool_name, tool_args, result, reverse_tool,
                    reverse_args, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (run_id, tool_name, args_json, result_json,
                 reverse_tool, reverse_json, user_id),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        logger.debug("Failed to save checkpoint", exc_info=True)
        return None


def list_checkpoints(
    run_id: str = "",
    limit: int = 20,
    user_id: str = "",
) -> list[dict]:
    """List recent checkpoints, newest first."""
    try:
        with db_connection() as conn:
            if run_id:
                rows = conn.execute(
                    """SELECT * FROM checkpoints
                       WHERE run_id = ? ORDER BY created_at DESC LIMIT ?""",
                    (run_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM checkpoints
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [_checkpoint_to_dict(r) for r in rows]
    except Exception:
        logger.debug("Failed to list checkpoints", exc_info=True)
        return []


def rollback_checkpoint(checkpoint_id: int) -> dict:
    """Attempt to rollback a checkpoint by executing the reverse action.

    Returns the result of the reverse action, or an error.
    """
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT * FROM checkpoints WHERE id = ?",
                (checkpoint_id,),
            ).fetchone()

        if not row:
            return {"error": f"Checkpoint {checkpoint_id} not found"}

        cp = _checkpoint_to_dict(row)

        if cp.get("rolled_back"):
            return {"error": f"Checkpoint {checkpoint_id} already rolled back"}

        reverse_tool = cp.get("reverse_tool", "")
        if not reverse_tool:
            return {
                "error": f"No reverse action for '{cp['tool_name']}'. "
                         "This action cannot be automatically undone.",
                "checkpoint": cp,
            }

        reverse_args = cp.get("reverse_args", {})
        if isinstance(reverse_args, str):
            try:
                reverse_args = json.loads(reverse_args)
            except (json.JSONDecodeError, TypeError):
                reverse_args = {}

        if not reverse_args:
            return {
                "error": f"No reverse arguments for checkpoint {checkpoint_id}. "
                         "Manual rollback required.",
                "checkpoint": cp,
            }

        # Execute the reverse tool
        from roost.gemini_agent import _execute_tool
        result = _execute_tool(reverse_tool, reverse_args)

        # Mark as rolled back
        with db_connection() as conn:
            conn.execute(
                "UPDATE checkpoints SET rolled_back = 1 WHERE id = ?",
                (checkpoint_id,),
            )
            conn.commit()

        return {
            "ok": True,
            "checkpoint_id": checkpoint_id,
            "reverse_tool": reverse_tool,
            "reverse_args": reverse_args,
            "result": result,
        }

    except Exception as e:
        logger.exception("Rollback failed for checkpoint %d", checkpoint_id)
        return {"error": str(e)}


# ── Helpers ────────────────────────────────────────────────────────

def _build_reverse_args(tool_name: str, args: dict, result: dict) -> dict | None:
    """Build arguments for the reverse tool call."""

    # For create actions, the reverse is delete with the new ID
    if tool_name.startswith("create_") or tool_name.startswith("add_"):
        new_id = result.get("id") or result.get("task_id") or result.get("note_id")
        if new_id:
            # Map tool name to ID parameter name
            entity = tool_name.replace("create_", "").replace("add_", "")
            return {f"{entity}_id": new_id}

    # For update actions, store the original args to restore
    if tool_name.startswith("update_"):
        entity = tool_name.replace("update_", "")
        entity_id = args.get(f"{entity}_id") or args.get("id")
        if entity_id:
            # We can't perfectly reverse an update without the old values,
            # but we store what we can
            return {f"{entity}_id": entity_id, "_note": "Original values not captured"}

    # For complete_task, reverse is set status back
    if tool_name == "complete_task":
        task_id = args.get("task_id") or args.get("id")
        if task_id:
            return {"task_id": task_id, "status": "todo"}

    # For calendar events, reverse is delete with the event ID
    if tool_name in ("calendar_create_event", "ms_calendar_create_event"):
        event_id = result.get("id") or result.get("event_id")
        if event_id:
            return {"event_id": event_id}

    # For scheduled emails, reverse is cancel
    if tool_name == "schedule_email":
        email_id = result.get("id")
        if email_id:
            return {"scheduled_email_id": email_id}

    # For notion pages
    if tool_name == "notion_create_page":
        page_id = result.get("id") or result.get("page_id")
        if page_id:
            return {"page_id": page_id}

    return None


def _checkpoint_to_dict(row) -> dict:
    d = dict(row)
    for json_field in ("tool_args", "result", "reverse_args"):
        if json_field in d and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    if "rolled_back" in d:
        d["rolled_back"] = bool(d["rolled_back"])
    return d
