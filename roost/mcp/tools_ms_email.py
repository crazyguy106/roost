"""MCP tools for Microsoft Outlook email operations — search, read, send, folders."""

import logging

from roost.mcp.server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
def ms_search_emails(query: str, max_results: int = 10) -> dict:
    """Search Outlook emails via Microsoft Graph.

    Uses KQL search syntax. Examples: "from:alice subject:invoice",
    "hasAttachment:true", "received>=2026-01-01".

    Args:
        query: Search query (KQL syntax).
        max_results: Maximum number of messages to return (default 10).
    """
    try:
        from roost.mcp.ms_graph_helpers import search_messages

        messages = search_messages(query, max_results=max_results)
        return {
            "count": len(messages),
            "messages": messages,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_read_conversation(conversation_id: str) -> dict:
    """Read all messages in an Outlook conversation thread.

    Use the conversationId from ms_search_emails results.

    Args:
        conversation_id: The Outlook conversation ID.
    """
    try:
        from roost.mcp.ms_graph_helpers import read_conversation

        return read_conversation(conversation_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_send_email(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    reply_to_id: str = "",
    attachment_paths: list[str] | None = None,
) -> dict:
    """Send an email via Microsoft Outlook.

    For replies, provide the reply_to_id (message ID from ms_read_conversation).

    IMPORTANT: The user must approve the email content before this tool is called.

    Args:
        to: Recipient email address(es), comma-separated.
        subject: Email subject line.
        body: Plain text email body.
        cc: CC recipients, comma-separated.
        bcc: BCC recipients, comma-separated.
        reply_to_id: Message ID to reply to (for replies only).
        attachment_paths: List of absolute file paths to attach (<4MB each).
    """
    try:
        from roost.mcp.ms_graph_helpers import send_message

        result = send_message(
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            bcc=bcc,
            reply_to_id=reply_to_id,
            attachment_paths=attachment_paths,
        )

        # Auto-log communication against matching contacts
        if result.get("status") == "sent":
            try:
                from roost.task_service import auto_log_sent_email
                auto_log_sent_email(to, subject, "", body[:200])
            except Exception:
                logger.debug("Failed to auto-log sent MS email communication", exc_info=True)

        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_list_folders() -> dict:
    """List all Outlook mail folders with unread/total counts."""
    try:
        from roost.mcp.ms_graph_helpers import list_folders

        folders = list_folders()
        return {
            "count": len(folders),
            "folders": folders,
        }
    except Exception as e:
        return {"error": str(e)}
