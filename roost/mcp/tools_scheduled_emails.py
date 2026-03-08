"""MCP tools for scheduled email sending."""

from roost.mcp.server import mcp


@mcp.tool()
def schedule_email(
    to: str,
    subject: str,
    body: str,
    send_at: str,
    provider: str = "gmail",
    cc: str = "",
    bcc: str = "",
    thread_id: str = "",
    reply_to_id: str = "",
    attachment_paths: list[str] | None = None,
) -> dict:
    """Schedule an email for future sending.

    The email is queued in the database and sent automatically at the
    specified time by a background job (checks every 60 seconds).

    IMPORTANT: The user must approve the email content before this tool is called.

    Args:
        to: Recipient email address(es), comma-separated.
        subject: Email subject line.
        body: Plain text email body.
        send_at: When to send (ISO datetime, e.g. '2026-02-21T09:00:00+08:00').
                 Converted to UTC internally. Supports timezone offsets.
        provider: 'gmail' or 'microsoft' (default: gmail).
        cc: CC recipients, comma-separated.
        bcc: BCC recipients, comma-separated.
        thread_id: Gmail thread ID (for replies only).
        reply_to_id: MS message ID (for replies only).
        attachment_paths: List of absolute file paths to attach.
    """
    try:
        from datetime import datetime, timezone

        # Parse send_at and convert to UTC
        dt = datetime.fromisoformat(send_at)
        if dt.tzinfo is None:
            # Assume Singapore time if no timezone
            from zoneinfo import ZoneInfo
            dt = dt.replace(tzinfo=ZoneInfo("Asia/Singapore"))
        dt_utc = dt.astimezone(timezone.utc)
        scheduled_at_utc = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        from roost.services.scheduled_emails import schedule_email as _schedule

        result = _schedule(
            provider=provider,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            thread_id=thread_id,
            reply_to_id=reply_to_id,
            attachment_paths=attachment_paths,
            scheduled_at=scheduled_at_utc,
        )

        # Add human-readable local time to response
        result["send_at_local"] = dt.strftime("%Y-%m-%d %H:%M %Z")
        result["send_at_utc"] = scheduled_at_utc
        return result

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_scheduled_emails(
    status: str = "pending",
    limit: int = 20,
) -> dict:
    """List scheduled emails in the queue.

    Args:
        status: Filter by status ('pending', 'sent', 'failed', 'cancelled').
                Empty string = all statuses.
        limit: Max results (default 20).
    """
    try:
        from roost.services.scheduled_emails import list_scheduled

        emails = list_scheduled(status=status, limit=limit)
        return {"count": len(emails), "emails": emails}

    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def cancel_scheduled_email(email_id: int) -> dict:
    """Cancel a pending scheduled email by ID.

    Only emails with status 'pending' can be cancelled.

    Args:
        email_id: The scheduled email ID to cancel.
    """
    try:
        from roost.services.scheduled_emails import cancel_scheduled

        return cancel_scheduled(email_id)

    except Exception as e:
        return {"error": str(e)}
