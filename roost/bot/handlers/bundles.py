"""Context bundle command handlers: /briefing, /pulse, /prep.

Telegram-formatted wrappers around the same service calls used by the
MCP context bundle tools (tools_bundles.py).
"""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.handlers.common import _format_task_obj
from roost.bot.tz import to_local_dt

logger = logging.getLogger(__name__)


# ── Formatting helpers ──────────────────────────────────────────────

def _fmt_event(e: dict) -> str:
    """Format a calendar event for Telegram."""
    start = e.get("start")
    if hasattr(start, "strftime"):
        time_str = to_local_dt(start).strftime("%H:%M") if start.tzinfo else start.strftime("%H:%M")
    elif isinstance(start, str) and len(start) >= 16:
        # ISO string — parse, convert to local TZ, format
        try:
            from datetime import datetime as _dt
            s_clean = start.replace("Z", "+00:00")
            # Strip sub-second precision beyond 6 digits (MS Graph returns 7)
            s_clean = re.sub(r"(\.\d{6})\d+", r"\1", s_clean)
            dt = _dt.fromisoformat(s_clean)
            time_str = to_local_dt(dt).strftime("%H:%M")
        except (ValueError, TypeError):
            time_str = start[11:16]
    else:
        time_str = "?"
    summary = e.get("summary", "(no title)")
    loc = f" @ {e['location']}" if e.get("location") else ""
    return f"  {time_str} {summary}{loc}"


def _fmt_email(m: dict) -> str:
    """Format an email summary for Telegram."""
    subj = m.get("subject", "(no subject)")[:60]
    sender = m.get("from", "")
    # Extract just the name or short email
    if "<" in sender:
        sender = sender.split("<")[0].strip()
    if len(sender) > 25:
        sender = sender[:25] + "..."
    return f"  {sender}: {subj}"


# ── /briefing ───────────────────────────────────────────────────────

@authorized
async def cmd_briefing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/briefing — morning context briefing: calendar, tasks, energy, emails."""
    await update.message.reply_text("Assembling briefing...")

    lines = [
        "*Morning Briefing*\n",
        "_Life provides. You respond. The wake forms behind you._ (以悟归空)\n",
    ]
    warnings = []

    # Calendar
    try:
        from roost.calendar_service import get_today_events
        events = get_today_events()
        if events:
            lines.append(f"*Calendar ({len(events)}):*")
            for e in events[:8]:
                lines.append(_fmt_event(e))
            if len(events) > 8:
                lines.append(f"  ...and {len(events) - 8} more")
        else:
            lines.append("*Calendar:* Clear day")
    except Exception as e:
        warnings.append(f"Calendar: {e}")

    # In-progress tasks
    try:
        from roost.task_service import list_tasks
        in_progress = list_tasks(
            status="in_progress",
            top_level_only=True,
            exclude_paused_projects=True,
            limit=10,
        )
        if in_progress:
            lines.append(f"\n*In Progress ({len(in_progress)}):*")
            for t in in_progress[:5]:
                lines.append(_format_task_obj(t, show_context=True))
    except Exception as e:
        warnings.append(f"In-progress: {e}")

    # Overdue tasks
    try:
        from roost.task_service import list_tasks
        overdue = list_tasks(
            deadline_filter="overdue",
            top_level_only=True,
            exclude_paused_projects=True,
            limit=10,
        )
        if overdue:
            lines.append(f"\n*Overdue ({len(overdue)}):*")
            for t in overdue[:5]:
                lines.append(_format_task_obj(t))
    except Exception as e:
        warnings.append(f"Overdue: {e}")

    # Due today
    try:
        from roost.task_service import list_tasks
        due_today = list_tasks(
            deadline_filter="today",
            top_level_only=True,
            exclude_paused_projects=True,
            limit=10,
        )
        if due_today:
            lines.append(f"\n*Due Today ({len(due_today)}):*")
            for t in due_today[:5]:
                lines.append(_format_task_obj(t))
    except Exception as e:
        warnings.append(f"Due today: {e}")

    # Focus tasks
    try:
        from roost.task_service import list_tasks
        focus = list_tasks(
            focus_only=True,
            top_level_only=True,
            exclude_paused_projects=True,
            limit=5,
        )
        if focus:
            lines.append(f"\n*Focus ({len(focus)}):*")
            for t in focus:
                lines.append(_format_task_obj(t))
    except Exception as e:
        warnings.append(f"Focus: {e}")

    # Energy budget
    try:
        from roost.task_service import get_spoon_status
        spoons = get_spoon_status()
        remaining = spoons.get("remaining", 0)
        budget = spoons.get("budget", 0)
        if budget > 0:
            filled = round(remaining / budget * 10)
            meter = "●" * filled + "○" * (10 - filled)
            lines.append(f"\n*Energy:* {meter} {remaining}/{budget}")
    except Exception:
        logger.debug("Failed to fetch spoon status for briefing", exc_info=True)

    # Pending replies
    try:
        from roost.mcp.gmail_helpers import search_messages
        pending = search_messages("label:(To Reply)", max_results=5)
        if pending:
            lines.append(f"\n*Awaiting Reply ({len(pending)}):*")
            for m in pending[:5]:
                lines.append(_fmt_email(m))
    except Exception:
        logger.debug("Failed to fetch pending replies for briefing", exc_info=True)

    if warnings:
        lines.append(f"\n_Warnings: {'; '.join(warnings)}_")

    if len(lines) == 1:
        lines.append("Nothing to report. Enjoy your day!")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /pulse ──────────────────────────────────────────────────────────

@authorized
async def cmd_pulse(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pulse <project> — project status snapshot."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /pulse <project name>\n"
            "Example: /pulse SIEMLess"
        )
        return

    project = " ".join(context.args)
    await update.message.reply_text(f"Checking {project}...")

    from roost.task_service import list_tasks

    # All project tasks
    try:
        tasks = list_tasks(
            project=project,
            top_level_only=True,
            exclude_paused_projects=False,
            limit=50,
        )
    except Exception:
        logger.debug("Failed to fetch project tasks for pulse", exc_info=True)
        tasks = []

    if not tasks:
        await update.message.reply_text(f"No tasks found for '{project}'.")
        return

    # Group by status
    by_status = {}
    for t in tasks:
        s = t.status.value if hasattr(t.status, "value") else str(t.status)
        by_status.setdefault(s, []).append(t)

    lines = [f"*{project} Pulse*\n"]

    # Summary counts
    total = len(tasks)
    todo = len(by_status.get("todo", []))
    wip = len(by_status.get("in_progress", []))
    done = len(by_status.get("done", []))
    blocked = len(by_status.get("blocked", []))
    lines.append(f"Total: {total} | Todo: {todo} | WIP: {wip} | Done: {done}" +
                 (f" | Blocked: {blocked}" if blocked else ""))

    # Show in-progress tasks
    if by_status.get("in_progress"):
        lines.append(f"\n*In Progress:*")
        for t in by_status["in_progress"][:5]:
            lines.append(_format_task_obj(t, show_context=True))

    # Show blocked tasks
    if by_status.get("blocked"):
        lines.append(f"\n*Blocked:*")
        for t in by_status["blocked"][:3]:
            lines.append(_format_task_obj(t))

    # Show todo (top 5 by urgency)
    if by_status.get("todo"):
        lines.append(f"\n*Todo (top 5):*")
        todo_sorted = sorted(
            by_status["todo"],
            key=lambda t: getattr(t, "urgency_score", 0) or 0,
            reverse=True,
        )
        for t in todo_sorted[:5]:
            lines.append(_format_task_obj(t))

    # Upcoming events matching project
    try:
        from roost.calendar_service import get_week_events
        week_events = get_week_events(days=7)
        project_events = [
            e for e in week_events
            if project.lower() in (
                (e.get("summary", "") or "") + " " +
                (e.get("description", "") or "")
            ).lower()
        ]
        if project_events:
            lines.append(f"\n*Upcoming Events ({len(project_events)}):*")
            for e in project_events[:3]:
                lines.append(_fmt_event(e))
    except Exception:
        logger.debug("Failed to fetch project events for pulse", exc_info=True)

    # Recent emails
    try:
        from roost.mcp.gmail_helpers import search_messages
        emails = search_messages(project, max_results=3)
        if emails:
            lines.append(f"\n*Recent Emails:*")
            for m in emails[:3]:
                lines.append(_fmt_email(m))
    except Exception:
        logger.debug("Failed to fetch project emails for pulse", exc_info=True)

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /prep ───────────────────────────────────────────────────────────

@authorized
async def cmd_prep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/prep <name> — prepare context for meeting/interaction with a person."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /prep <person name>\n"
            "Example: /prep Harun"
        )
        return

    name = " ".join(context.args)
    await update.message.reply_text(f"Prepping for {name}...")

    from roost.task_service import (
        get_contact_by_name,
        list_assignments_by_contact,
        list_communications,
        list_tasks,
    )

    # Find contact
    contact = get_contact_by_name(name)
    if not contact:
        await update.message.reply_text(
            f"No contact found for '{name}'.\n"
            "Try a different spelling or /contacts to browse."
        )
        return

    lines = [f"*Prep: {contact.name}*\n"]

    # Contact card
    if contact.email:
        lines.append(f"Email: {contact.email}")
    if contact.phone:
        lines.append(f"Phone: {contact.phone}")
    if contact.entity_name:
        lines.append(f"Org: {contact.entity_name}")
    if contact.notes:
        notes_short = contact.notes[:150]
        if len(contact.notes) > 150:
            notes_short += "..."
        lines.append(f"Notes: {notes_short}")

    # Communication history
    try:
        comms = list_communications(contact.id, limit=5)
        if comms:
            lines.append(f"\n*Recent Comms ({len(comms)}):*")
            for c in comms[:5]:
                date_str = str(c.occurred_at)[:10] if c.occurred_at else "?"
                subj = c.subject[:50] if c.subject else ""
                lines.append(f"  {date_str} [{c.comm_type}] {subj}")
    except Exception:
        logger.debug("Failed to fetch communication history for prep", exc_info=True)

    # Related tasks
    try:
        # From project assignments
        assignments = list_assignments_by_contact(contact.id)
        project_names = [
            a.project_name
            for a in assignments.get("project_assignments", [])
            if a.project_name
        ]

        related = []
        seen = set()
        for pname in project_names[:5]:
            if pname:
                ptasks = list_tasks(
                    project=pname,
                    top_level_only=True,
                    exclude_paused_projects=False,
                    limit=5,
                )
                for t in ptasks:
                    if t.id not in seen:
                        seen.add(t.id)
                        related.append(t)

        # Also search by name mention
        all_active = list_tasks(
            top_level_only=True,
            exclude_paused_projects=True,
            limit=100,
        )
        name_pattern = re.compile(r"\b" + re.escape(name) + r"\b", re.IGNORECASE)
        for t in all_active:
            if t.id in seen:
                continue
            searchable = f"{t.title or ''} {t.description or ''} {getattr(t, 'context_note', '') or ''}"
            if name_pattern.search(searchable):
                seen.add(t.id)
                related.append(t)

        if related:
            lines.append(f"\n*Related Tasks ({len(related)}):*")
            for t in related[:5]:
                lines.append(_format_task_obj(t))

        if project_names:
            lines.append(f"\n*Projects:* {', '.join(project_names)}")
    except Exception:
        logger.debug("Failed to fetch related tasks for prep", exc_info=True)

    # Recent emails
    try:
        from roost.mcp.gmail_helpers import search_messages
        if contact.email:
            query = f"from:{contact.email} OR to:{contact.email}"
        else:
            query = contact.name
        emails = search_messages(query, max_results=3)
        if emails:
            lines.append(f"\n*Recent Emails:*")
            for m in emails[:3]:
                lines.append(_fmt_email(m))
    except Exception:
        logger.debug("Failed to fetch contact emails for prep", exc_info=True)

    # Upcoming events
    try:
        from roost.calendar_service import get_week_events
        week_events = get_week_events(days=7)
        match_terms = [name.lower()]
        if contact.email:
            match_terms.append(contact.email.lower())
        person_events = [
            e for e in week_events
            if any(
                term in (
                    (e.get("summary", "") or "") + " " +
                    (e.get("description", "") or "")
                ).lower()
                for term in match_terms
            )
        ]
        if person_events:
            lines.append(f"\n*Upcoming Events:*")
            for e in person_events[:3]:
                lines.append(_fmt_event(e))
    except Exception:
        logger.debug("Failed to fetch upcoming events for prep", exc_info=True)

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
