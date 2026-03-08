"""Scheduled email service — queue, send, cancel persistent email jobs.

Multi-tenant: scheduled emails are scoped by user_id. Auto-resolved from
current user context if not explicitly provided.
"""

import json
import logging
from datetime import datetime, timezone

from roost.database import get_connection

logger = logging.getLogger("roost.services.scheduled_emails")


def _resolve_uid(user_id: int | None = None) -> int:
    """Resolve user_id from context if not provided."""
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


def schedule_email(
    *,
    provider: str = "gmail",
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    thread_id: str = "",
    reply_to_id: str = "",
    attachment_paths: list[str] | None = None,
    scheduled_at: str,
    user_id: int | None = None,
) -> dict:
    """Queue an email for future sending.

    Args:
        provider: 'gmail' or 'microsoft'
        to: Recipient(s), comma-separated
        subject: Email subject
        body: Plain text body
        cc: CC recipients, comma-separated
        bcc: BCC recipients, comma-separated
        thread_id: Gmail thread ID (for replies)
        reply_to_id: MS message ID (for replies)
        attachment_paths: List of file paths to attach
        scheduled_at: ISO datetime in UTC (e.g. '2026-02-21T01:00:00Z')
        user_id: Owner of this scheduled email (auto-resolved if None)

    Returns:
        dict with id, scheduled_at, status
    """
    uid = _resolve_uid(user_id)
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO scheduled_emails
           (provider, to_addr, cc, bcc, subject, body, thread_id,
            reply_to_id, attachment_paths, scheduled_at, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            provider,
            to,
            cc,
            bcc,
            subject,
            body,
            thread_id,
            reply_to_id,
            json.dumps(attachment_paths or []),
            scheduled_at,
            uid,
        ),
    )
    conn.commit()
    email_id = cur.lastrowid
    conn.close()

    logger.info("Scheduled email #%d to %s at %s (user=%d)", email_id, to, scheduled_at, uid)
    return {"id": email_id, "scheduled_at": scheduled_at, "status": "pending"}


def list_scheduled(status: str = "pending", limit: int = 20,
                   user_id: int | None = None) -> list[dict]:
    """List scheduled emails for the current user, optionally filtered by status."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    if status:
        rows = conn.execute(
            """SELECT * FROM scheduled_emails WHERE user_id = ? AND status = ?
               ORDER BY scheduled_at ASC LIMIT ?""",
            (uid, status, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT * FROM scheduled_emails WHERE user_id = ?
               ORDER BY scheduled_at DESC LIMIT ?""",
            (uid, limit),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cancel_scheduled(email_id: int, user_id: int | None = None) -> dict:
    """Cancel a pending scheduled email (must belong to current user)."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM scheduled_emails WHERE id = ? AND user_id = ?", (email_id, uid)
    ).fetchone()

    if not row:
        conn.close()
        return {"error": f"Scheduled email #{email_id} not found"}

    if row["status"] != "pending":
        conn.close()
        return {"error": f"Cannot cancel — status is '{row['status']}'"}

    conn.execute(
        "UPDATE scheduled_emails SET status = 'cancelled' WHERE id = ?",
        (email_id,),
    )
    conn.commit()
    conn.close()

    logger.info("Cancelled scheduled email #%d", email_id)
    return {"id": email_id, "status": "cancelled"}


def process_due_emails() -> int:
    """Send all emails that are due (all users). Returns count of emails sent."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_connection()
    due = conn.execute(
        """SELECT * FROM scheduled_emails
           WHERE status = 'pending' AND scheduled_at <= ?
           ORDER BY scheduled_at ASC""",
        (now_utc,),
    ).fetchall()
    conn.close()

    sent_count = 0
    for row in due:
        email = dict(row)
        try:
            _send_one(email)
            _update_status(email["id"], "sent")
            sent_count += 1
            logger.info("Sent scheduled email #%d to %s", email["id"], email["to_addr"])
        except Exception as e:
            _update_status(email["id"], "failed", str(e))
            logger.exception("Failed to send scheduled email #%d", email["id"])

    return sent_count


def _send_one(email: dict) -> None:
    """Send a single email via the appropriate provider.

    Uses the email owner's tokens (user_id stored on the row).
    """
    attachments = json.loads(email.get("attachment_paths") or "[]")
    email_user_id = email.get("user_id")

    if email["provider"] == "gmail":
        from roost.mcp.gmail_helpers import send_message

        result = send_message(
            to=email["to_addr"],
            subject=email["subject"],
            body=email["body"],
            cc=email.get("cc", ""),
            bcc=email.get("bcc", ""),
            thread_id=email.get("thread_id", ""),
            attachment_paths=attachments or None,
        )
        if result.get("error"):
            raise RuntimeError(result["error"])

        # Auto-log communication
        try:
            from roost.services.communications import auto_log_sent_email
            auto_log_sent_email(
                email["to_addr"],
                email["subject"],
                result.get("threadId", ""),
                email["body"][:200],
            )
        except Exception:
            logger.debug("Failed to auto-log scheduled email", exc_info=True)

    elif email["provider"] == "microsoft":
        from roost.mcp.ms_graph_helpers import send_message

        result = send_message(
            to=email["to_addr"],
            subject=email["subject"],
            body=email["body"],
            cc=email.get("cc", ""),
            bcc=email.get("bcc", ""),
            reply_to_id=email.get("reply_to_id", ""),
            attachment_paths=attachments or None,
            user_id=email_user_id,
        )
        if result.get("error"):
            raise RuntimeError(result["error"])

    else:
        raise ValueError(f"Unknown provider: {email['provider']}")


def _update_status(email_id: int, status: str, error: str = "") -> None:
    """Update scheduled email status and sent_at timestamp."""
    conn = get_connection()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if status == "sent":
        conn.execute(
            "UPDATE scheduled_emails SET status = ?, sent_at = ? WHERE id = ?",
            (status, now_utc, email_id),
        )
    else:
        conn.execute(
            "UPDATE scheduled_emails SET status = ?, error = ? WHERE id = ?",
            (status, error, email_id),
        )
    conn.commit()
    conn.close()
