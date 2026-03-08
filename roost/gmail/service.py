"""Gmail send operations — email digests, task notifications, general send."""

import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from roost.config import GMAIL_SEND_FROM

logger = logging.getLogger("roost.gmail.service")


def send_email(to: str, subject: str, body: str, html: bool = False) -> bool:
    """Send an email via Gmail API. Returns True on success."""
    from roost.gmail import get_gmail_service

    service = get_gmail_service()
    if not service:
        logger.warning("Gmail service not available — skipping send")
        return False

    try:
        if html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body, "html"))
        else:
            msg = MIMEText(body)

        msg["To"] = to
        msg["From"] = GMAIL_SEND_FROM or "me"
        msg["Subject"] = subject

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(
            userId="me",
            body={"raw": raw},
        ).execute()

        logger.info("Email sent to %s: %s", to, subject)
        return True

    except Exception:
        logger.exception("Failed to send email to %s", to)
        return False


def send_digest(to: str) -> bool:
    """Send the morning digest via email."""
    try:
        from roost.triage import get_today_tasks
        from roost.calendar_service import get_today_events

        triage = get_today_tasks()
        events = get_today_events()

        lines = ["<h2>Daily Briefing</h2>"]

        overdue = triage.get("overdue", [])
        if overdue:
            lines.append(f"<h3>Overdue ({len(overdue)})</h3><ul>")
            for t in overdue[:10]:
                lines.append(f"<li>#{t['id']} {t['title']}</li>")
            lines.append("</ul>")

        due = triage.get("due_today", [])
        if due:
            lines.append(f"<h3>Due Today ({len(due)})</h3><ul>")
            for t in due[:10]:
                lines.append(f"<li>#{t['id']} {t['title']}</li>")
            lines.append("</ul>")

        wip = triage.get("in_progress", [])
        if wip:
            lines.append(f"<h3>In Progress ({len(wip)})</h3><ul>")
            for t in wip[:5]:
                ctx = f" — {t.get('context_note', '')}" if t.get("context_note") else ""
                lines.append(f"<li>#{t['id']} {t['title']}{ctx}</li>")
            lines.append("</ul>")

        if events:
            lines.append(f"<h3>Calendar ({len(events)})</h3><ul>")
            for e in events[:10]:
                start = e["start"].strftime("%H:%M") if e.get("start") else "?"
                lines.append(f"<li>{start} {e['summary']}</li>")
            lines.append("</ul>")

        top = triage.get("top_urgent", [])
        if top:
            lines.append(f"<h3>Suggested Focus</h3><p>#{top[0]['id']} {top[0]['title']}</p>")

        body = "\n".join(lines)
        return send_email(to, "Roost — Daily Briefing", body, html=True)

    except Exception:
        logger.exception("Failed to build digest email")
        return False


def send_task_notification(to: str, task, event_type: str) -> bool:
    """Send a task event notification email."""
    title = getattr(task, "title", str(task))
    task_id = getattr(task, "id", "?")
    status = getattr(task, "status", None)
    status_val = status.value if hasattr(status, "value") else str(status or "")

    subjects = {
        "completed": f"Task #{task_id} completed: {title}",
        "deadline": f"Deadline approaching: #{task_id} {title}",
        "created": f"New task: #{task_id} {title}",
    }
    subject = subjects.get(event_type, f"Task update: #{task_id} {title}")

    body = (
        f"<h3>{subject}</h3>"
        f"<p><b>Status:</b> {status_val}</p>"
    )
    deadline = getattr(task, "deadline", None)
    if deadline:
        body += f"<p><b>Deadline:</b> {deadline}</p>"

    return send_email(to, subject, body, html=True)
