"""Poll Gmail inbox for email-to-task capture, auto-labelling, and action cycling.

Responsibilities:
1. Email-to-task: Emails with [task] or [note] subject prefix create items.
2. Auto-label: Apply domain-based labels to unlabelled inbox messages.
3. Action cycling: Manage (To Reply) ↔ (Waiting for Reply) state transitions.
"""

import logging
import re

logger = logging.getLogger("roost.gmail.poller")

# Track processed message IDs to avoid duplicates
_processed_ids: set[str] = set()


def poll_inbox() -> int:
    """Poll Gmail inbox for actionable emails. Returns count of items created."""
    from roost.gmail import is_gmail_available, get_gmail_service

    if not is_gmail_available():
        return 0

    service = get_gmail_service()
    if not service:
        return 0

    # Run auto-labelling and action cycling alongside task capture
    try:
        from roost.gmail.auto_label import auto_label_recent, cycle_action_labels
        auto_label_recent(service)
        cycle_action_labels(service)
    except Exception:
        logger.exception("Auto-label/cycle failed (non-fatal)")

    try:
        # Search for unread emails with [task] or [note] prefix
        query = "is:unread (subject:[task] OR subject:[note])"
        results = service.users().messages().list(
            userId="me", q=query, maxResults=20,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            return 0

        created = 0
        for msg_ref in messages:
            msg_id = msg_ref["id"]
            if msg_id in _processed_ids:
                continue

            try:
                msg = service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["Subject", "From"],
                ).execute()

                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                subject = headers.get("Subject", "")
                from_addr = headers.get("From", "")

                if _process_email(subject, from_addr):
                    created += 1

                # Mark as read
                service.users().messages().modify(
                    userId="me", id=msg_id,
                    body={"removeLabelIds": ["UNREAD"]},
                ).execute()

                _processed_ids.add(msg_id)

            except Exception:
                logger.exception("Failed to process message %s", msg_id)

        # Trim processed IDs cache
        if len(_processed_ids) > 1000:
            _processed_ids.clear()

        if created:
            logger.info("Created %d items from email", created)
        return created

    except Exception:
        logger.exception("Gmail inbox poll failed")
        return 0


def _process_email(subject: str, from_addr: str) -> bool:
    """Process a single email by subject prefix. Returns True if item created."""
    subject_lower = subject.lower().strip()

    # [task] prefix → create task
    task_match = re.match(r"\[task\]\s*(.*)", subject_lower)
    if task_match:
        title = re.sub(r"^\[task\]\s*", "", subject, flags=re.IGNORECASE).strip()
        if not title:
            return False
        try:
            from roost.models import TaskCreate
            from roost import task_service
            task_service.create_task(
                TaskCreate(title=title, context_note=f"From email: {from_addr}"),
                source="gmail",
            )
            logger.info("Created task from email: %s", title)
            return True
        except Exception:
            logger.exception("Failed to create task from email")
            return False

    # [note] prefix → create note
    note_match = re.match(r"\[note\]\s*(.*)", subject_lower)
    if note_match:
        content = re.sub(r"^\[note\]\s*", "", subject, flags=re.IGNORECASE).strip()
        if not content:
            return False
        try:
            from roost.models import NoteCreate
            from roost import task_service
            task_service.create_note(
                NoteCreate(content=content, tag="email"),
                source="gmail",
            )
            logger.info("Created note from email: %s", content[:50])
            return True
        except Exception:
            logger.exception("Failed to create note from email")
            return False

    return False
