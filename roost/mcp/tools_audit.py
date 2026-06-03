"""MCP tools for the system-of-record audit trail.

This is the *system* counterpart to `tools_activity.py`, which is the
task-coupled trail (active task, productivity logging). These tools
surface fast-path events written via `roost.services.activity.log_action`
— Telegram approve/edit/skip taps, web fast-path button presses,
scheduler runs, etc.

Use these when an agent needs to know "what just happened on the
operator surface" without having to scrape conversation history.
"""

from __future__ import annotations

from roost.mcp.server import mcp
from roost.services import activity


@mcp.tool()
def audit_recent(limit: int = 10, system_only: bool = True) -> dict:
    """Return the newest audit-log rows from the system-of-record trail.

    These are fast-path events (Telegram inline buttons, web one-click
    actions, scheduler ticks) written via `log_action`. By default only
    system writes (`task_id IS NULL`) are returned, so the result is not
    polluted by task-coupled activity from `tools_activity.log_activity`.

    Args:
        limit: Max rows to return (default 10, newest first).
        system_only: If True (default), restrict to system writes.
            Pass False to include task-coupled rows too.
    """
    try:
        rows = activity.recent(limit=limit, system_only=system_only)
        return {"ok": True, "count": len(rows), "entries": rows}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def audit_for_entity(entity_type: str, entity_id: str, limit: int = 20) -> dict:
    """Return the audit trail for a specific entity (cadence_draft, lead, etc.).

    Args:
        entity_type: Category of the thing (cadence_draft, guardian_draft,
            conversation, lead, …). Maps to `artifact_type`.
        entity_id: Identifier of the thing. Maps to `artifact_ref` —
            pass as a string.
        limit: Max rows to return (default 20, newest first).
    """
    try:
        rows = activity.for_entity(entity_type, entity_id, limit=limit)
        return {
            "ok": True,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "count": len(rows),
            "entries": rows,
        }
    except Exception as e:
        return {"error": str(e)}
