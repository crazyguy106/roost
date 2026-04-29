"""Guardian AI — pre-flight safety checks on every tool call.

Rules-based engine that checks tool calls before execution.
Returns allow/warn/block decisions with reasons.

Enable via GUARDIAN_ENABLED=true in .env.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime

from roost.database import db_connection

logger = logging.getLogger(__name__)


# ── Decision types ───────────────────────────────────────────────────

ALLOW = "allow"
WARN = "warn"
BLOCK = "block"


# ── Destructive tool patterns ────────────────────────────────────────

# Tools that delete or remove data permanently
_DELETE_TOOLS = {
    "delete_task", "delete_project", "delete_note", "delete_contact",
    "delete_entity", "delete_communication",
    "docker_compose_down", "kubectl_delete",
    "remove_server", "remove_contact_from_entity",
    "remove_contact_identifier", "remove_routine_item",
    "remove_task_dependency",
    "cancel_scheduled_email", "cancel_lead_emails",
    "cloudflare_delete_dns_record", "namecheap_set_dns_records",
    "improvmx_delete_alias",
}

# Tools that send data externally (email, messaging, uploads)
_EXTERNAL_SEND_TOOLS = {
    "send_email", "ms_send_email", "schedule_email",
    "telegram_send_message",
    "ms_teams_send_message", "ms_teams_send_chat",
    "ms_teams_reply_channel_message",
    "drive_upload", "ms_onedrive_upload", "ms_sharepoint_upload",
    "scp_upload",
    "notion_create_page", "notion_update_page", "notion_append_blocks",
}

# Tools that execute arbitrary commands
_EXEC_TOOLS = {
    "ssh_exec", "docker_compose_up", "docker_compose_down",
    "kubectl_apply", "kubectl_delete",
}

# Dangerous shell patterns
_DANGEROUS_PATTERNS = [
    re.compile(r"\brm\s+-r", re.IGNORECASE),
    re.compile(r"\brm\s+-f", re.IGNORECASE),
    re.compile(r"\brmdir\b", re.IGNORECASE),
    re.compile(r"\bdd\s+if=", re.IGNORECASE),
    re.compile(r"\bmkfs\b", re.IGNORECASE),
    re.compile(r"\bchmod\s+777\b"),
    re.compile(r"\bchown\s+-R\b"),
    re.compile(r">\s*/dev/", re.IGNORECASE),
    re.compile(r"\bdrop\s+table\b", re.IGNORECASE),
    re.compile(r"\btruncate\b", re.IGNORECASE),
    re.compile(r"\bcurl\b.*\|\s*bash", re.IGNORECASE),
    re.compile(r"\bwget\b.*\|\s*sh", re.IGNORECASE),
]

# Email patterns that suggest mass/spam sending
_BULK_EMAIL_PATTERNS = [
    re.compile(r"(,\s*){3,}"),  # 4+ comma-separated recipients
    re.compile(r"@.*@.*@.*@"),  # 4+ @ signs (multiple recipients in one field)
]


# Schema defined in database.py (SCHEMA_V24) to avoid circular imports.


# ── Core check function ─────────────────────────────────────────────

def guardian_check(tool_name: str, tool_args: dict,
                   user_id: str = "") -> dict:
    """Run pre-flight safety checks on a tool call.

    Returns:
        {"decision": "allow"|"warn"|"block",
         "reason": str,
         "rule": str}
    """
    checks = [
        _check_delete_without_confirmation,
        _check_bulk_email,
        _check_unknown_recipient,
        _check_dangerous_command,
        _check_tool_call_burst,
        _check_sensitive_file_access,
    ]

    for check_fn in checks:
        result = check_fn(tool_name, tool_args, user_id)
        if result["decision"] != ALLOW:
            _log_decision(tool_name, tool_args, result, user_id)
            return result

    return {"decision": ALLOW, "reason": "", "rule": ""}


# ── Individual check rules ──────────────────────────────────────────

def _check_delete_without_confirmation(name: str, args: dict,
                                        user_id: str) -> dict:
    """Block bulk deletes and warn on single deletes."""
    if name not in _DELETE_TOOLS:
        return {"decision": ALLOW, "reason": "", "rule": ""}

    # Check if this is a bulk operation (e.g., deleting multiple items)
    args_str = json.dumps(args, default=str)
    if "all" in args_str.lower() or "bulk" in args_str.lower():
        return {
            "decision": BLOCK,
            "reason": f"Bulk delete operation blocked: {name}. "
                      "Delete items individually for safety.",
            "rule": "no_bulk_delete",
        }

    return {
        "decision": WARN,
        "reason": f"Delete operation: {name}. This action is irreversible.",
        "rule": "delete_warning",
    }


def _check_bulk_email(name: str, args: dict, user_id: str) -> dict:
    """Block emails with too many recipients."""
    if name not in ("send_email", "ms_send_email", "schedule_email"):
        return {"decision": ALLOW, "reason": "", "rule": ""}

    to_field = str(args.get("to", "") or args.get("to_addr", ""))
    cc_field = str(args.get("cc", ""))
    all_recipients = f"{to_field},{cc_field}"

    for pattern in _BULK_EMAIL_PATTERNS:
        if pattern.search(all_recipients):
            return {
                "decision": BLOCK,
                "reason": f"Bulk email blocked: too many recipients in '{name}'. "
                          "Send to individuals or small groups.",
                "rule": "no_bulk_email",
            }

    return {"decision": ALLOW, "reason": "", "rule": ""}


def _check_unknown_recipient(name: str, args: dict, user_id: str) -> dict:
    """Warn when sending email to addresses not in contacts."""
    if name not in ("send_email", "ms_send_email", "schedule_email"):
        return {"decision": ALLOW, "reason": "", "rule": ""}

    to_field = str(args.get("to", "") or args.get("to_addr", ""))
    if not to_field:
        return {"decision": ALLOW, "reason": "", "rule": ""}

    # Check if recipient is in contacts
    try:
        from roost.services.contacts import find_contact_by_identifier
        emails = [e.strip() for e in to_field.split(",") if e.strip()]
        unknown = []
        for email in emails:
            contact = find_contact_by_identifier("email", email)
            if not contact:
                unknown.append(email)

        if unknown:
            return {
                "decision": WARN,
                "reason": f"Sending to unknown recipient(s): {', '.join(unknown)}. "
                          "Not found in your contacts.",
                "rule": "unknown_recipient",
            }
    except Exception:
        pass  # If contact lookup fails, don't block

    return {"decision": ALLOW, "reason": "", "rule": ""}


def _check_dangerous_command(name: str, args: dict, user_id: str) -> dict:
    """Block dangerous shell commands in ssh_exec and similar."""
    if name not in _EXEC_TOOLS:
        return {"decision": ALLOW, "reason": "", "rule": ""}

    command = str(args.get("command", "") or args.get("cmd", ""))
    if not command:
        return {"decision": ALLOW, "reason": "", "rule": ""}

    for pattern in _DANGEROUS_PATTERNS:
        if pattern.search(command):
            return {
                "decision": BLOCK,
                "reason": f"Dangerous command blocked in {name}: "
                          f"matched pattern '{pattern.pattern}'. "
                          "Review the command manually.",
                "rule": "dangerous_command",
            }

    return {"decision": ALLOW, "reason": "", "rule": ""}


def _check_tool_call_burst(name: str, args: dict, user_id: str) -> dict:
    """Warn if too many tool calls in a short window (possible loop)."""
    try:
        with db_connection() as conn:
            one_minute_ago = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            count = conn.execute(
                """SELECT COUNT(*) as cnt FROM guardian_log
                   WHERE user_id = ? AND created_at > datetime(?, '-1 minute')""",
                (user_id, one_minute_ago),
            ).fetchone()["cnt"]

            if count > 15:
                return {
                    "decision": WARN,
                    "reason": f"High tool call rate: {count} calls in the last minute. "
                              "Possible agent loop detected.",
                    "rule": "burst_detection",
                }
    except Exception:
        pass

    return {"decision": ALLOW, "reason": "", "rule": ""}


def _check_sensitive_file_access(name: str, args: dict,
                                  user_id: str) -> dict:
    """Warn when accessing files that might contain secrets."""
    if name not in ("read_file", "write_file", "search_files"):
        return {"decision": ALLOW, "reason": "", "rule": ""}

    path = str(args.get("path", "") or args.get("file_path", ""))
    path_lower = path.lower()

    sensitive_patterns = [
        ".env", "credentials", "secrets", ".pem", ".key",
        "id_rsa", "id_ed25519", "shadow", "passwd",
        "token", "api_key",
    ]

    for pattern in sensitive_patterns:
        if pattern in path_lower:
            return {
                "decision": WARN,
                "reason": f"Accessing potentially sensitive file: {path}",
                "rule": "sensitive_file",
            }

    return {"decision": ALLOW, "reason": "", "rule": ""}


# ── Logging ──────────────────────────────────────────────────────────

def _log_decision(tool_name: str, tool_args: dict,
                   result: dict, user_id: str) -> None:
    """Log non-allow decisions to the guardian_log table."""
    try:
        with db_connection() as conn:
            conn.execute(
                """INSERT INTO guardian_log
                   (tool_name, tool_args, decision, reason, rule_name, user_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (tool_name, json.dumps(tool_args, default=str)[:2000],
                 result["decision"], result["reason"],
                 result["rule"], user_id),
            )
            conn.commit()
    except Exception:
        logger.debug("Failed to log guardian decision", exc_info=True)


# ── Query functions ──────────────────────────────────────────────────

def get_guardian_log(limit: int = 50, decision: str | None = None,
                     user_id: str = "") -> list[dict]:
    """Get recent guardian decisions."""
    with db_connection() as conn:
        if decision:
            rows = conn.execute(
                """SELECT * FROM guardian_log
                   WHERE decision = ? ORDER BY created_at DESC LIMIT ?""",
                (decision, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM guardian_log
                   ORDER BY created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def get_guardian_stats(user_id: str = "") -> dict:
    """Get summary stats of guardian decisions."""
    with db_connection() as conn:
        rows = conn.execute(
            """SELECT decision, COUNT(*) as cnt
               FROM guardian_log GROUP BY decision"""
        ).fetchall()
        stats = {r["decision"]: r["cnt"] for r in rows}

        # Most blocked rules
        blocked_rules = conn.execute(
            """SELECT rule_name, COUNT(*) as cnt
               FROM guardian_log WHERE decision = 'block'
               GROUP BY rule_name ORDER BY cnt DESC LIMIT 10"""
        ).fetchall()

        return {
            "total": sum(stats.values()),
            "allowed": stats.get("allow", 0),
            "warned": stats.get("warn", 0),
            "blocked": stats.get("block", 0),
            "top_blocked_rules": [dict(r) for r in blocked_rules],
        }
