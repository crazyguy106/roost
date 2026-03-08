"""MCP tools for contact communication history."""

from roost.mcp.server import mcp


@mcp.tool()
def log_communication(
    contact_id: int,
    comm_type: str,
    subject: str = "",
    detail: str = "",
    occurred_at: str = "",
) -> dict:
    """Log a communication with a contact (call, note, whatsapp, etc.).

    Args:
        contact_id: The contact ID.
        comm_type: Type of communication: email_sent, email_received, meeting,
            call, note, whatsapp, other.
        subject: Brief subject/topic.
        detail: Additional detail or notes.
        occurred_at: When it happened (ISO datetime). Empty = now.
    """
    try:
        from roost.task_service import log_communication as _log
        from roost.models import CommunicationCreate

        comm = _log(CommunicationCreate(
            contact_id=contact_id,
            comm_type=comm_type,
            subject=subject,
            detail=detail,
            occurred_at=occurred_at,
        ))
        return {
            "id": comm.id,
            "contact_id": comm.contact_id,
            "contact_name": comm.contact_name,
            "comm_type": comm.comm_type,
            "subject": comm.subject,
            "occurred_at": comm.occurred_at,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_contact_history(
    contact_id: int,
    comm_type: str | None = None,
    limit: int = 30,
) -> dict:
    """Get the communication timeline for a contact.

    Returns a unified list of emails, meetings, calls, notes — newest first.

    Args:
        contact_id: The contact ID.
        comm_type: Optional filter: email_sent, email_received, meeting,
            call, note, whatsapp, other.
        limit: Max records to return (default 30).
    """
    try:
        from roost.task_service import list_communications, get_contact

        contact = get_contact(contact_id)
        if not contact:
            return {"error": f"Contact {contact_id} not found"}

        comms = list_communications(contact_id, comm_type=comm_type, limit=limit)
        return {
            "contact_id": contact_id,
            "contact_name": contact.name,
            "count": len(comms),
            "communications": [
                {
                    "id": c.id,
                    "type": c.comm_type,
                    "subject": c.subject,
                    "detail": c.detail,
                    "occurred_at": c.occurred_at,
                    "external_ref": c.external_ref,
                    "external_type": c.external_type,
                }
                for c in comms
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def sync_contact_emails(contact_id: int, max_results: int = 20) -> dict:
    """Pull Gmail history for a contact and store as communication records.

    Searches for emails from/to the contact's email address, deduplicates
    against existing records (by thread ID), and stores new ones.

    Args:
        contact_id: The contact ID (must have an email address).
        max_results: Max Gmail messages to search (default 20).
    """
    try:
        from roost.task_service import sync_contact_emails as _sync

        return _sync(contact_id, max_results=max_results)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def sync_contact_meetings(contact_id: int, days: int = 30) -> dict:
    """Match calendar events to a contact by name and store as meetings.

    Searches event summaries and descriptions for the contact's name.
    Deduplicates against existing records.

    Args:
        contact_id: The contact ID.
        days: Number of days to search (default 30).
    """
    try:
        from roost.task_service import sync_contact_meetings as _sync

        return _sync(contact_id, days=days)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def delete_communication(comm_id: int) -> dict:
    """Remove a communication record.

    Args:
        comm_id: The communication record ID.
    """
    try:
        from roost.task_service import delete_communication as _delete

        deleted = _delete(comm_id)
        if not deleted:
            return {"error": f"Communication {comm_id} not found"}
        return {"deleted": True, "comm_id": comm_id}
    except Exception as e:
        return {"error": str(e)}
