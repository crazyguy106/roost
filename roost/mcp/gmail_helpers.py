"""Gmail API helper functions used by tools_gmail.py.

Wraps raw Gmail API calls into clean functions with error handling
and body truncation to avoid flooding LLM context.
"""

import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

logger = logging.getLogger("roost.mcp.gmail")

MAX_BODY_CHARS = 10_000  # Truncate long email bodies


def _get_service(account: str | None = None):
    """Get Gmail API service, raising on failure."""
    from roost.gmail import get_gmail_service

    service = get_gmail_service(account=account)
    if not service:
        from roost.user_context import get_current_user
        ctx = get_current_user()
        acct_str = f" (account: {account})" if account else ""
        raise RuntimeError(
            f"Gmail not available for {ctx.email}{acct_str}. "
            "Visit the web UI and click 'Integrations' > 'Authorize Gmail' to connect your account."
        )
    return service


def search_messages(query: str, max_results: int = 10, account: str | None = None) -> list[dict]:
    """Search Gmail messages and return metadata.

    Args:
        query: Gmail search query (same syntax as Gmail search box).
        max_results: Maximum messages to return.
        account: Google account email (None = default).

    Returns:
        List of message dicts with id, threadId, subject, from, to, date, snippet.
    """
    service = _get_service(account=account)

    result = service.users().messages().list(
        userId="me",
        q=query,
        maxResults=max_results,
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        return []

    output = []
    for msg_stub in messages:
        msg = service.users().messages().get(
            userId="me",
            id=msg_stub["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "To", "Date"],
        ).execute()

        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        output.append({
            "id": msg["id"],
            "threadId": msg["threadId"],
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "labelIds": msg.get("labelIds", []),
        })

    return output


def read_thread(thread_id: str, account: str | None = None) -> dict:
    """Read a full email thread.

    Args:
        thread_id: The Gmail thread ID.
        account: Google account email (None = default).

    Returns:
        Dict with thread metadata and list of messages with bodies.
    """
    service = _get_service(account=account)

    thread = service.users().threads().get(
        userId="me",
        id=thread_id,
        format="full",
    ).execute()

    messages = []
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}

        body = _extract_body(msg.get("payload", {}))
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n[... truncated ...]"

        messages.append({
            "id": msg["id"],
            "subject": headers.get("Subject", ""),
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "cc": headers.get("Cc", ""),
            "date": headers.get("Date", ""),
            "message_id": headers.get("Message-ID", ""),
            "references": headers.get("References", ""),
            "body": body,
            "labelIds": msg.get("labelIds", []),
        })

    return {
        "threadId": thread_id,
        "message_count": len(messages),
        "messages": messages,
    }


def _extract_body(payload: dict) -> str:
    """Recursively extract plain text body from a Gmail message payload."""
    mime_type = payload.get("mimeType", "")

    # Direct text part
    if mime_type == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Multipart — recurse into parts
    parts = payload.get("parts", [])
    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    # Fallback: try text/html if no plain text
    if mime_type == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for part in parts:
        result = _extract_body(part)
        if result:
            return result

    return ""


def send_message(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    thread_id: str = "",
    in_reply_to: str = "",
    references: str = "",
    attachment_paths: list[str] | None = None,
    account: str | None = None,
) -> dict:
    """Send an email via Gmail API.

    When replying to a thread, provide thread_id plus in_reply_to and references
    headers from the message being replied to. The send_email MCP tool will
    auto-fetch these when only thread_id is provided.

    Args:
        to: Recipient email address(es), comma-separated.
        subject: Email subject line.
        body: Plain text body.
        cc: CC recipients, comma-separated.
        bcc: BCC recipients, comma-separated.
        thread_id: Gmail thread ID for replies.
        in_reply_to: Message-ID header of the message being replied to.
        references: References header for threading.
        attachment_paths: List of absolute file paths to attach.
        account: Google account email (None = default).

    Returns:
        Dict with message id and threadId on success, or error.
    """
    from roost.config import GMAIL_SEND_FROM

    service = _get_service(account=account)

    has_attachments = attachment_paths and len(attachment_paths) > 0

    if has_attachments:
        msg = MIMEMultipart()
        msg.attach(MIMEText(body))
        for path_str in attachment_paths:
            path = Path(path_str)
            if not path.exists():
                return {"error": f"Attachment not found: {path_str}"}
            part = MIMEBase("application", "octet-stream")
            part.set_payload(path.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f"attachment; filename={path.name}")
            msg.attach(part)
    else:
        msg = MIMEText(body)

    msg["To"] = to
    msg["From"] = GMAIL_SEND_FROM or "me"
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = references

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    send_body = {"raw": raw}
    if thread_id:
        send_body["threadId"] = thread_id

    result = service.users().messages().send(
        userId="me",
        body=send_body,
    ).execute()

    return {
        "id": result.get("id"),
        "threadId": result.get("threadId"),
        "status": "sent",
    }


def get_attachments(message_id: str, account: str | None = None) -> list[dict]:
    """List attachments on a Gmail message.

    Args:
        message_id: The Gmail message ID.
        account: Google account email (None = default).

    Returns:
        List of dicts with filename, attachmentId, size, mimeType.
    """
    service = _get_service(account=account)

    msg = service.users().messages().get(
        userId="me",
        id=message_id,
        format="full",
    ).execute()

    attachments = []
    parts = msg.get("payload", {}).get("parts", [])
    for part in parts:
        filename = part.get("filename", "")
        if filename:
            att_id = part["body"].get("attachmentId")
            size = part["body"].get("size", 0)
            mime_type = part.get("mimeType", "application/octet-stream")
            attachments.append({
                "filename": filename,
                "attachmentId": att_id,
                "size": size,
                "mimeType": mime_type,
            })

    return attachments


def download_attachments(
    message_id: str,
    output_dir: str,
    attachment_id: str | None = None,
    account: str | None = None,
) -> list[dict]:
    """Download attachments from a Gmail message to a local directory.

    Args:
        message_id: The Gmail message ID.
        output_dir: Absolute path to the directory to save files.
        attachment_id: Optional specific attachment ID. If None, downloads all.
        account: Google account email (None = default).

    Returns:
        List of dicts with filename, path, size for each downloaded file.
    """
    service = _get_service(account=account)
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    # Get attachment metadata
    attachments = get_attachments(message_id, account=account)
    if not attachments:
        return []

    # Filter to specific attachment if requested
    if attachment_id:
        attachments = [a for a in attachments if a["attachmentId"] == attachment_id]
        if not attachments:
            raise ValueError(f"Attachment ID not found: {attachment_id}")

    downloaded = []
    for att in attachments:
        data = service.users().messages().attachments().get(
            userId="me",
            messageId=message_id,
            id=att["attachmentId"],
        ).execute()

        file_data = base64.urlsafe_b64decode(data["data"])
        file_path = out_path / att["filename"]
        file_path.write_bytes(file_data)

        downloaded.append({
            "filename": att["filename"],
            "path": str(file_path),
            "size": len(file_data),
        })
        logger.info("Downloaded attachment: %s (%d bytes)", att["filename"], len(file_data))

    return downloaded


def list_labels(account: str | None = None) -> list[dict]:
    """List all Gmail labels.

    Args:
        account: Google account email (None = default).

    Returns:
        List of dicts with id, name, type.
    """
    service = _get_service(account=account)

    result = service.users().labels().list(userId="me").execute()
    labels = result.get("labels", [])

    return [
        {
            "id": label["id"],
            "name": label["name"],
            "type": label.get("type", ""),
        }
        for label in labels
    ]
