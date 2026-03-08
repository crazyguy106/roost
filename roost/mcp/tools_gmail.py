"""MCP tools for Gmail operations - search, read, send, labels, accounts."""

import logging

from roost.mcp.server import mcp

logger = logging.getLogger("roost.mcp.gmail")


@mcp.tool()
def search_emails(query: str, max_results: int = 10, google_account: str = "") -> dict:
    """Search Gmail messages using Gmail query syntax.

    Examples: "from:alice subject:invoice", "is:unread label:INBOX",
    "after:2025/01/01 has:attachment".

    Args:
        query: Gmail search query (same syntax as the Gmail search box).
        max_results: Maximum number of messages to return (default 10).
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.mcp.gmail_helpers import search_messages

        acct = google_account or None
        messages = search_messages(query, max_results=max_results, account=acct)
        return {
            "count": len(messages),
            "messages": messages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def read_thread(thread_id: str, google_account: str = "") -> dict:
    """Read all messages in an email thread.

    Use the threadId from search_emails results.

    Args:
        thread_id: The Gmail thread ID.
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.mcp.gmail_helpers import read_thread as _read

        acct = google_account or None
        return _read(thread_id, account=acct)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    thread_id: str = "",
    attachment_paths: list[str] | None = None,
    google_account: str = "",
) -> dict:
    """Send an email via Gmail.

    For replies, provide the thread_id - threading headers (In-Reply-To,
    References) will be auto-fetched from the last message in the thread.

    IMPORTANT: The user must approve the email content before this tool is called.

    Args:
        to: Recipient email address(es), comma-separated.
        subject: Email subject line.
        body: Plain text email body.
        cc: CC recipients, comma-separated.
        bcc: BCC recipients, comma-separated.
        thread_id: Gmail thread ID (for replies only).
        attachment_paths: List of absolute file paths to attach.
        google_account: Google account email to send from (empty = default account).
    """
    try:
        from roost.mcp.gmail_helpers import send_message, read_thread as _read_thread

        acct = google_account or None

        # Auto-fetch threading headers when replying
        in_reply_to = ""
        references = ""
        if thread_id:
            try:
                thread = _read_thread(thread_id, account=acct)
                if thread["messages"]:
                    last_msg = thread["messages"][-1]
                    in_reply_to = last_msg.get("message_id", "")
                    references = last_msg.get("references", "")
                    if in_reply_to:
                        references = f"{references} {in_reply_to}".strip()
            except Exception:
                logger.debug("Failed to fetch threading headers for reply", exc_info=True)

        result = send_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            thread_id=thread_id,
            in_reply_to=in_reply_to,
            references=references,
            attachment_paths=attachment_paths,
            account=acct,
        )

        # Auto-log communication against matching contacts
        if result.get("status") == "sent":
            try:
                from roost.task_service import auto_log_sent_email
                auto_log_sent_email(to, subject, result.get("threadId", ""), body[:200])
            except Exception:
                logger.debug("Failed to auto-log sent email communication", exc_info=True)

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_attachments(message_id: str, google_account: str = "") -> dict:
    """List attachments on a Gmail message.

    Use the message id from search_emails or read_thread results.

    Args:
        message_id: The Gmail message ID.
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.mcp.gmail_helpers import get_attachments

        acct = google_account or None
        attachments = get_attachments(message_id, account=acct)
        return {
            "count": len(attachments),
            "attachments": attachments,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def download_attachments(
    message_id: str,
    output_dir: str,
    attachment_id: str | None = None,
    google_account: str = "",
) -> dict:
    """Download attachments from a Gmail message to a local directory.

    Downloads all attachments by default, or a specific one if attachment_id
    is provided. Use list_attachments first to see what's available.

    Args:
        message_id: The Gmail message ID.
        output_dir: Absolute path to save files (created if missing).
        attachment_id: Optional specific attachment ID (downloads all if omitted).
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.mcp.gmail_helpers import download_attachments as _download

        acct = google_account or None
        files = _download(message_id, output_dir, attachment_id=attachment_id, account=acct)
        return {
            "count": len(files),
            "files": files,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_labels(google_account: str = "") -> dict:
    """List all Gmail labels (system and user-created).

    Args:
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.mcp.gmail_helpers import list_labels as _labels

        acct = google_account or None
        labels = _labels(account=acct)
        return {
            "count": len(labels),
            "labels": labels,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_google_accounts() -> dict:
    """List all connected Google accounts.

    Shows which Google accounts have been authorized via OAuth.
    Use the account email as the google_account parameter in other tools.
    """
    try:
        from roost.gmail.client import list_google_accounts as _list

        accounts = _list()
        return {
            "count": len(accounts),
            "accounts": accounts,
        }
    except Exception as e:
        return {"error": str(e)}
