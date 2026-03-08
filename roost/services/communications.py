"""Communication logging and email/meeting sync."""

import json
import logging
import re
from datetime import datetime

from roost.database import get_connection
from roost.models import Communication, CommunicationCreate

logger = logging.getLogger("roost.services.communications")

__all__ = [
    "log_communication",
    "get_communication",
    "list_communications",
    "delete_communication",
    "auto_log_sent_email",
    "sync_contact_emails",
    "sync_contact_meetings",
]


def log_communication(data: CommunicationCreate) -> Communication:
    """Insert a communication record for a contact."""
    conn = get_connection()
    occurred = data.occurred_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.execute(
        """INSERT INTO contact_communications
           (contact_id, comm_type, subject, detail, occurred_at, external_ref, external_type)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (data.contact_id, data.comm_type, data.subject, data.detail,
         occurred, data.external_ref, data.external_type),
    )
    conn.commit()
    comm_id = cur.lastrowid
    conn.close()
    return get_communication(comm_id)


def get_communication(comm_id: int) -> Communication | None:
    """Get a single communication record by ID."""
    conn = get_connection()
    row = conn.execute(
        """SELECT cc.*, c.name as contact_name
           FROM contact_communications cc
           JOIN contacts c ON cc.contact_id = c.id
           WHERE cc.id = ?""",
        (comm_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Communication(**dict(row))


def list_communications(
    contact_id: int,
    comm_type: str | None = None,
    limit: int = 50,
) -> list[Communication]:
    """List communications for a contact, newest first."""
    conn = get_connection()
    query = """SELECT cc.*, c.name as contact_name
               FROM contact_communications cc
               JOIN contacts c ON cc.contact_id = c.id
               WHERE cc.contact_id = ?"""
    params: list = [contact_id]
    if comm_type:
        query += " AND cc.comm_type = ?"
        params.append(comm_type)
    query += " ORDER BY cc.occurred_at DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [Communication(**dict(r)) for r in rows]


def delete_communication(comm_id: int) -> bool:
    """Delete a communication record."""
    conn = get_connection()
    cur = conn.execute("DELETE FROM contact_communications WHERE id = ?", (comm_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def _extract_email_address(header_value: str) -> str:
    """Extract bare email from 'Name <email>' format, lowercase."""
    match = re.search(r"<([^>]+)>", header_value)
    if match:
        return match.group(1).strip().lower()
    return header_value.strip().lower()


def auto_log_sent_email(
    to: str, subject: str, thread_id: str = "", snippet: str = "",
) -> list[int]:
    """Auto-log a sent email against matching contacts.

    Splits comma-separated recipients, matches each against contacts by email,
    and logs email_sent for matches. Returns list of comm IDs created.
    """
    from roost.services.contacts import get_contact_by_email

    comm_ids = []
    recipients = [addr.strip() for addr in to.split(",") if addr.strip()]
    for addr in recipients:
        email = _extract_email_address(addr)
        contact = get_contact_by_email(email)
        if not contact:
            continue
        # Dedup: skip if thread already logged for this contact
        if thread_id:
            conn = get_connection()
            existing = conn.execute(
                """SELECT id FROM contact_communications
                   WHERE contact_id = ? AND external_ref = ? AND external_type = 'gmail_thread'""",
                (contact.id, thread_id),
            ).fetchone()
            conn.close()
            if existing:
                continue
        comm = log_communication(CommunicationCreate(
            contact_id=contact.id,
            comm_type="email_sent",
            subject=subject,
            detail=snippet[:200] if snippet else "",
            external_ref=thread_id,
            external_type="gmail_thread" if thread_id else "",
        ))
        comm_ids.append(comm.id)
    return comm_ids


def sync_contact_emails(contact_id: int, max_results: int = 20) -> dict:
    """Sync Gmail history for a contact. Returns summary of new vs skipped."""
    from roost.services.contacts import get_contact

    contact = get_contact(contact_id)
    if not contact:
        return {"error": f"Contact {contact_id} not found"}
    if not contact.email:
        return {"error": f"Contact '{contact.name}' has no email address"}

    from roost.mcp.gmail_helpers import search_messages

    email = contact.email.strip().lower()
    messages = search_messages(f"from:{email} OR to:{email}", max_results=max_results)

    # Load existing external_refs for dedup
    conn = get_connection()
    existing_refs = {
        row["external_ref"]
        for row in conn.execute(
            """SELECT external_ref FROM contact_communications
               WHERE contact_id = ? AND external_type = 'gmail_thread'""",
            (contact_id,),
        ).fetchall()
    }
    conn.close()

    new_records = 0
    skipped = 0
    for msg in messages:
        thread_id = msg.get("threadId", "")
        if thread_id in existing_refs:
            skipped += 1
            continue
        existing_refs.add(thread_id)  # prevent dupes within batch

        from_addr = _extract_email_address(msg.get("from", ""))
        comm_type = "email_received" if from_addr == email else "email_sent"

        log_communication(CommunicationCreate(
            contact_id=contact_id,
            comm_type=comm_type,
            subject=msg.get("subject", ""),
            detail=msg.get("snippet", "")[:200],
            occurred_at=msg.get("date", ""),
            external_ref=thread_id,
            external_type="gmail_thread",
        ))
        new_records += 1

    return {
        "contact_id": contact_id,
        "contact_name": contact.name,
        "email": email,
        "new_records": new_records,
        "skipped_existing": skipped,
        "total_searched": len(messages),
    }


def sync_contact_meetings(contact_id: int, days: int = 30) -> dict:
    """Match calendar events to a contact by name search. Returns summary."""
    from roost.services.contacts import get_contact

    contact = get_contact(contact_id)
    if not contact:
        return {"error": f"Contact {contact_id} not found"}

    from roost.calendar_service import fetch_calendar_events

    events = fetch_calendar_events()

    # Build search terms: full name + individual parts
    name_lower = contact.name.strip().lower()
    search_terms = [name_lower]
    parts = name_lower.split()
    if len(parts) > 1:
        search_terms.extend(parts)

    # Load existing refs for dedup
    conn = get_connection()
    existing_refs = {
        row["external_ref"]
        for row in conn.execute(
            """SELECT external_ref FROM contact_communications
               WHERE contact_id = ? AND external_type = 'calendar_event'""",
            (contact_id,),
        ).fetchall()
    }
    conn.close()

    new_meetings = 0
    for event in events:
        summary = (event.get("summary") or "").lower()
        description = (event.get("description") or "").lower()
        haystack = f"{summary} {description}"

        # Match full name first, then parts
        matched = name_lower in haystack
        if not matched and len(parts) > 1:
            matched = all(part in haystack for part in parts)
        if not matched:
            continue

        start = event.get("start", "")
        if hasattr(start, "isoformat"):
            start_str = start.isoformat()
        else:
            start_str = str(start)

        ext_ref = f"{event.get('summary', '')}_{start_str}"
        if ext_ref in existing_refs:
            continue
        existing_refs.add(ext_ref)

        log_communication(CommunicationCreate(
            contact_id=contact_id,
            comm_type="meeting",
            subject=event.get("summary", ""),
            detail=event.get("location", ""),
            occurred_at=start_str,
            external_ref=ext_ref,
            external_type="calendar_event",
        ))
        new_meetings += 1

    return {
        "contact_id": contact_id,
        "contact_name": contact.name,
        "new_meetings": new_meetings,
        "events_searched": len(events),
    }
