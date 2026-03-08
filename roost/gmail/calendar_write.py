"""Google Calendar write operations — create/update/delete events from tasks.

Uses the Calendar API v3 (not ICS). Read-only ICS polling remains separate
in calendar_service.py. This module handles write operations only.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger("roost.gmail.calendar_write")


def create_event_from_task(task) -> str | None:
    """Create a Google Calendar event from a task with a deadline.

    Returns the event ID on success, None on failure.
    """
    from roost.gmail import get_calendar_service

    service = get_calendar_service()
    if not service:
        return None

    deadline = getattr(task, "deadline", None)
    if not deadline:
        return None

    # Build event
    title = getattr(task, "title", "Task")
    task_id = getattr(task, "id", 0)
    priority = getattr(task, "priority", None)
    priority_val = priority.value if hasattr(priority, "value") else str(priority or "medium")
    description = getattr(task, "description", "")
    project = getattr(task, "project_name", "")

    # Parse deadline
    if isinstance(deadline, str):
        try:
            deadline = datetime.strptime(deadline[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                deadline = datetime.strptime(deadline[:10], "%Y-%m-%d")
            except ValueError:
                return None

    event_body = {
        "summary": f"[{priority_val.upper()}] {title}",
        "description": (
            f"Roost Task #{task_id}\n"
            f"Priority: {priority_val}\n"
            f"{('Project: ' + project) if project else ''}\n"
            f"{description}"
        ).strip(),
        "start": {
            "dateTime": deadline.isoformat(),
            "timeZone": "Asia/Singapore",
        },
        "end": {
            "dateTime": (deadline + timedelta(hours=1)).isoformat(),
            "timeZone": "Asia/Singapore",
        },
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": 30},
            ],
        },
    }

    try:
        event = service.events().insert(
            calendarId="primary", body=event_body,
        ).execute()
        event_id = event.get("id", "")
        logger.info("Created calendar event %s for task #%s", event_id, task_id)
        return event_id
    except Exception:
        logger.exception("Failed to create calendar event for task #%s", task_id)
        return None


def update_event(event_id: str, task) -> bool:
    """Update an existing calendar event from task data."""
    from roost.gmail import get_calendar_service

    service = get_calendar_service()
    if not service:
        return False

    deadline = getattr(task, "deadline", None)
    if not deadline:
        return False

    title = getattr(task, "title", "Task")
    priority = getattr(task, "priority", None)
    priority_val = priority.value if hasattr(priority, "value") else str(priority or "medium")

    if isinstance(deadline, str):
        try:
            deadline = datetime.strptime(deadline[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                deadline = datetime.strptime(deadline[:10], "%Y-%m-%d")
            except ValueError:
                return False

    try:
        service.events().patch(
            calendarId="primary",
            eventId=event_id,
            body={
                "summary": f"[{priority_val.upper()}] {title}",
                "start": {
                    "dateTime": deadline.isoformat(),
                    "timeZone": "Asia/Singapore",
                },
                "end": {
                    "dateTime": (deadline + timedelta(hours=1)).isoformat(),
                    "timeZone": "Asia/Singapore",
                },
            },
        ).execute()
        logger.info("Updated calendar event %s", event_id)
        return True
    except Exception:
        logger.exception("Failed to update calendar event %s", event_id)
        return False


def delete_event(event_id: str) -> bool:
    """Delete a calendar event."""
    from roost.gmail import get_calendar_service

    service = get_calendar_service()
    if not service:
        return False

    try:
        service.events().delete(
            calendarId="primary", eventId=event_id,
        ).execute()
        logger.info("Deleted calendar event %s", event_id)
        return True
    except Exception:
        logger.exception("Failed to delete calendar event %s", event_id)
        return False


def sync_task_deadlines() -> dict:
    """Sync all task deadlines to Google Calendar.

    Returns: {"created": N, "skipped": N, "errors": N}
    """
    from roost.gmail import is_gmail_available

    if not is_gmail_available():
        return {"created": 0, "skipped": 0, "errors": 0}

    from roost import task_service

    tasks = task_service.list_tasks(deadline_filter="has_deadline")
    stats = {"created": 0, "skipped": 0, "errors": 0}

    for t in tasks:
        if t.status.value == "done":
            stats["skipped"] += 1
            continue

        event_id = create_event_from_task(t)
        if event_id:
            stats["created"] += 1
        else:
            stats["errors"] += 1

    logger.info("Calendar sync: %s", stats)
    return stats
