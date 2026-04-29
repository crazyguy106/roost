"""Telegram account linking — connect web users to Telegram IDs.

Flow:
1. Web user clicks "Link Telegram" in Settings
2. Server generates a unique 8-char code, stored with user_id + expiry
3. User sends "LINK <code>" to the Telegram bot
4. Bot verifies code, updates users.telegram_id
5. Settings page polls /api/settings/telegram/status until linked
"""

import logging
import secrets
import time

from roost.database import get_connection

logger = logging.getLogger("roost.telegram_linking")

# Code settings
CODE_LENGTH = 8
CODE_EXPIRY_SECONDS = 600  # 10 minutes

# ── Schema ────────────────────────────────────────────────────────

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telegram_link_codes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    code        TEXT NOT NULL UNIQUE,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
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


def generate_link_code(user_id: int) -> str:
    """Generate a unique link code for a web user.

    Invalidates any previous pending codes for the same user.
    Returns the plaintext code.
    """
    now = time.time()

    # Expire old codes for this user
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE telegram_link_codes SET status = 'expired' WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        )

        code = secrets.token_hex(CODE_LENGTH // 2).upper()  # 8 hex chars

        conn.execute(
            "INSERT INTO telegram_link_codes (user_id, code, status, created_at, expires_at) VALUES (?, ?, 'pending', ?, ?)",
            (user_id, code, now, now + CODE_EXPIRY_SECONDS),
        )
        conn.commit()
    finally:
        conn.close()

    logger.info("Generated link code for user %d (expires in %ds)", user_id, CODE_EXPIRY_SECONDS)
    return code


def verify_link_code(code: str, telegram_id: int) -> dict:
    """Verify a link code sent from Telegram. Links the accounts if valid.

    Returns:
        {"ok": True, "user_id": int, "name": str} on success
        {"ok": False, "error": str} on failure
    """
    now = time.time()
    code = code.strip().upper()

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM telegram_link_codes WHERE code = ? AND status = 'pending'",
            (code,),
        ).fetchone()

        if not row:
            return {"ok": False, "error": "Invalid or expired code"}

        row = dict(row)

        if now > row["expires_at"]:
            conn.execute(
                "UPDATE telegram_link_codes SET status = 'expired' WHERE id = ?",
                (row["id"],),
            )
            conn.commit()
            return {"ok": False, "error": "Code expired — generate a new one from Settings"}

        web_user_id = row["user_id"]

        # Update the user's telegram_id
        conn.execute(
            "UPDATE users SET telegram_id = ? WHERE id = ?",
            (telegram_id, web_user_id),
        )

        # Mark code as used
        conn.execute(
            "UPDATE telegram_link_codes SET status = 'used' WHERE id = ?",
            (row["id"],),
        )
        conn.commit()

        # Get user name for confirmation message
        user_row = conn.execute(
            "SELECT name FROM users WHERE id = ?", (web_user_id,),
        ).fetchone()
        user_name = user_row["name"] if user_row else f"User #{web_user_id}"

    finally:
        conn.close()

    logger.info("Linked Telegram %d to web user %d (%s)", telegram_id, web_user_id, user_name)
    return {"ok": True, "user_id": web_user_id, "name": user_name}


def get_link_status(user_id: int) -> dict:
    """Check if a user has a linked Telegram account.

    Returns {"linked": bool, "telegram_id": int|None, "pending_code": bool}
    """
    conn = get_connection()
    try:
        # Check if user has telegram_id set
        user_row = conn.execute(
            "SELECT telegram_id FROM users WHERE id = ?", (user_id,),
        ).fetchone()

        telegram_id = None
        if user_row and user_row["telegram_id"]:
            telegram_id = user_row["telegram_id"]

        # Check for pending link code
        now = time.time()
        pending = conn.execute(
            "SELECT 1 FROM telegram_link_codes WHERE user_id = ? AND status = 'pending' AND expires_at > ?",
            (user_id, now),
        ).fetchone()

        return {
            "linked": telegram_id is not None,
            "telegram_id": telegram_id,
            "pending_code": pending is not None,
        }
    finally:
        conn.close()
