"""Two-factor action confirmation — OTP via Telegram for destructive web chat actions.

When the AI agent (via web chat) wants to execute a destructive tool (send email,
SSH exec, k8s delete, etc.), the action is held in a pending state. A 6-digit OTP
is sent to the user's Telegram. The user enters the OTP in the web chat to release
the action.

Flow:
1. Agent calls destructive tool → _execute_tool detects TIER_WEB scope
2. Action is stored as pending with a 6-digit OTP
3. OTP sent to user's Telegram via bot API
4. Web chat shows "Action held — enter OTP to confirm"
5. User enters OTP → action executes → result returned

Storage: SQLite table `action_confirmations` (same DB as everything else).
"""

import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from roost.database import get_connection

logger = logging.getLogger("roost.action_confirmations")

# OTP settings
OTP_LENGTH = 6
OTP_EXPIRY_SECONDS = 300  # 5 minutes
MAX_ATTEMPTS = 3

# ── Schema ────────────────────────────────────────────────────────

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS action_confirmations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    otp_hash        TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    tool_args       TEXT NOT NULL DEFAULT '{}',
    tool_scope      TEXT NOT NULL DEFAULT 'full',
    status          TEXT NOT NULL DEFAULT 'pending',
    attempts        INTEGER NOT NULL DEFAULT 0,
    session_id      TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    created_at      REAL NOT NULL,
    expires_at      REAL NOT NULL,
    resolved_at     REAL
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


# ── Destructive tools that require confirmation ───────────────────

DESTRUCTIVE_TOOLS = {
    # Email
    "send_email", "draft_email",
    # Infrastructure
    "ssh_exec", "scp_upload",
    "kubectl_apply", "kubectl_delete",
    "docker_compose_up", "docker_compose_down",
    # File system
    "write_file",
    # Skills (arbitrary code)
    "run_skill",
    # External APIs
    "calendar_create_event", "calendar_delete_event",
    "notion_create_page", "notion_update_page", "notion_delete_block",
    "notion_archive_page",
    # Drive
    "drive_upload",
}


def is_destructive(tool_name: str) -> bool:
    """Check if a tool requires OTP confirmation in web scope."""
    return tool_name in DESTRUCTIVE_TOOLS


# ── OTP generation and hashing ────────────────────────────────────

def _generate_otp() -> str:
    """Generate a cryptographically random 6-digit OTP."""
    return "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))


def _hash_otp(otp: str) -> str:
    """Hash OTP for storage (don't store plaintext)."""
    import hashlib
    return hashlib.sha256(otp.encode()).hexdigest()


# ── CRUD ──────────────────────────────────────────────────────────

def create_pending_action(
    user_id: str,
    tool_name: str,
    tool_args: dict[str, Any],
    session_id: str = "",
    description: str = "",
) -> tuple[int, str]:
    """Create a pending action and return (action_id, plaintext_otp).

    The plaintext OTP is sent to Telegram — only the hash is stored.
    """
    import json

    otp = _generate_otp()
    now = time.time()

    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO action_confirmations
               (user_id, otp_hash, tool_name, tool_args, session_id, description,
                status, attempts, created_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', 0, ?, ?)""",
            (
                user_id,
                _hash_otp(otp),
                tool_name,
                json.dumps(tool_args, default=str),
                session_id,
                description,
                now,
                now + OTP_EXPIRY_SECONDS,
            ),
        )
        conn.commit()
        action_id = cur.lastrowid
    finally:
        conn.close()

    logger.info(
        "Pending action #%d: %s for user %s (expires in %ds)",
        action_id, tool_name, user_id, OTP_EXPIRY_SECONDS,
    )
    return action_id, otp


def verify_otp(action_id: int, otp: str) -> dict[str, Any]:
    """Verify an OTP for a pending action.

    Returns:
        {"ok": True, "tool_name": ..., "tool_args": ...} on success
        {"ok": False, "error": "..."} on failure
    """
    import json

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM action_confirmations WHERE id = ?", (action_id,),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Action not found"}

        row = dict(row)

        if row["status"] != "pending":
            return {"ok": False, "error": f"Action already {row['status']}"}

        if time.time() > row["expires_at"]:
            conn.execute(
                "UPDATE action_confirmations SET status = 'expired', resolved_at = ? WHERE id = ?",
                (time.time(), action_id),
            )
            conn.commit()
            return {"ok": False, "error": "OTP expired — request the action again"}

        if row["attempts"] >= MAX_ATTEMPTS:
            conn.execute(
                "UPDATE action_confirmations SET status = 'failed', resolved_at = ? WHERE id = ?",
                (time.time(), action_id),
            )
            conn.commit()
            return {"ok": False, "error": "Too many attempts — request the action again"}

        # Check OTP
        if _hash_otp(otp) != row["otp_hash"]:
            conn.execute(
                "UPDATE action_confirmations SET attempts = attempts + 1 WHERE id = ?",
                (action_id,),
            )
            conn.commit()
            remaining = MAX_ATTEMPTS - row["attempts"] - 1
            return {"ok": False, "error": f"Invalid OTP ({remaining} attempts remaining)"}

        # Success — mark confirmed
        conn.execute(
            "UPDATE action_confirmations SET status = 'confirmed', resolved_at = ? WHERE id = ?",
            (time.time(), action_id),
        )
        conn.commit()

        return {
            "ok": True,
            "tool_name": row["tool_name"],
            "tool_args": json.loads(row["tool_args"]),
            "session_id": row["session_id"],
            "user_id": row["user_id"],
        }
    finally:
        conn.close()


def cancel_action(action_id: int) -> bool:
    """Cancel a pending action."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE action_confirmations SET status = 'cancelled', resolved_at = ? WHERE id = ? AND status = 'pending'",
            (time.time(), action_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def get_pending_actions(user_id: str) -> list[dict]:
    """Get all pending (non-expired) actions for a user."""
    import json
    now = time.time()
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, tool_name, description, created_at, expires_at
               FROM action_confirmations
               WHERE user_id = ? AND status = 'pending' AND expires_at > ?
               ORDER BY created_at DESC""",
            (user_id, now),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def cleanup_expired():
    """Mark expired pending actions as expired. Call periodically."""
    now = time.time()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE action_confirmations SET status = 'expired', resolved_at = ? WHERE status = 'pending' AND expires_at < ?",
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()


# ── Telegram OTP delivery ─────────────────────────────────────────

async def send_otp_via_telegram(user_id: str, otp: str, tool_name: str,
                                 description: str = "", action_id: int = 0) -> bool:
    """Send OTP to user's Telegram. Returns True if sent successfully.

    Looks up the user's telegram_id from the users table, then sends
    via the Telegram Bot API.
    """
    try:
        from roost.config import TELEGRAM_BOT_TOKEN
        if not TELEGRAM_BOT_TOKEN:
            logger.warning("Cannot send OTP: TELEGRAM_BOT_TOKEN not set")
            return False

        # Look up telegram_id for this user
        telegram_id = _resolve_telegram_id(user_id)
        if not telegram_id:
            logger.warning("Cannot send OTP: no telegram_id for user %s", user_id)
            return False

        import httpx

        lines = [
            "Web Chat Action Confirmation",
            "",
            f"Action: {tool_name}",
        ]
        if description:
            lines.append(f"Details: {description}")
        lines.extend([
            "",
            f"OTP: {otp}",
            "",
            f"Enter this code in the web chat to confirm.",
            f"Expires in {OTP_EXPIRY_SECONDS // 60} minutes.",
        ])

        message = "\n".join(lines)

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": telegram_id,
                "text": message,
            })
            if resp.status_code == 200:
                logger.info("OTP sent to Telegram user %s for action #%d", telegram_id, action_id)
                return True
            else:
                logger.warning("Telegram OTP send failed: %s", resp.text[:200])
                return False

    except Exception:
        logger.exception("Failed to send OTP via Telegram")
        return False


def _resolve_telegram_id(user_id: str) -> int | None:
    """Resolve a web user_id to a Telegram chat ID.

    Checks:
    1. users table telegram_id column
    2. TELEGRAM_ALLOWED_USERS config (single-user fallback)
    """
    # Try DB first
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT telegram_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()
        if row and row["telegram_id"]:
            return int(row["telegram_id"])
    except Exception:
        pass
    finally:
        conn.close()

    # Fallback: single-user mode — use first TELEGRAM_ALLOWED_USERS
    try:
        from roost.config import TELEGRAM_ALLOWED_USERS
        if TELEGRAM_ALLOWED_USERS:
            return TELEGRAM_ALLOWED_USERS[0]
    except Exception:
        pass

    return None
