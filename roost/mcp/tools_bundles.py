"""Context bundle tools — assembled views from multiple service functions.

Each bundle composes 4-5 existing service calls into a single structured
response, giving non-technical users situational awareness from one call.
"""

from roost.mcp.server import mcp


def _task_summary(task) -> dict:
    """Minimal task dict for bundle context."""
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status.value if hasattr(task.status, "value") else str(task.status),
        "priority": task.priority.value if hasattr(task.priority, "value") else str(task.priority),
        "deadline": str(task.deadline) if task.deadline else None,
        "project_name": task.project_name,
        "energy_level": task.energy_level,
        "effort_estimate": task.effort_estimate,
        "context_note": task.context_note if hasattr(task, "context_note") else None,
    }


def _email_summary(msg: dict) -> dict:
    """Minimal email dict for bundle context."""
    return {
        "subject": msg.get("subject", ""),
        "from": msg.get("from", ""),
        "date": msg.get("date", ""),
        "snippet": msg.get("snippet", ""),
    }


@mcp.tool()
def morning_briefing() -> dict:
    """Assemble a morning context briefing: today's calendar, in-progress
    tasks, overdue tasks, tasks due today, focused tasks, energy budget,
    and emails awaiting reply.

    Returns a single structured view for starting the day. No parameters
    needed — pulls everything relevant to today.
    """
    try:
        from roost.calendar_service import get_today_events
        from roost.task_service import get_spoon_status, list_tasks

        warnings = []

        # Calendar
        try:
            events = get_today_events()
        except Exception as e:
            events = []
            warnings.append(f"Calendar unavailable: {e}")

        # In-progress tasks (what you were working on)
        try:
            in_progress = list_tasks(
                status="in_progress",
                top_level_only=True,
                exclude_paused_projects=True,
                limit=20,
            )
        except Exception as e:
            in_progress = []
            warnings.append(f"In-progress query failed: {e}")

        # Overdue tasks
        try:
            overdue = list_tasks(
                deadline_filter="overdue",
                top_level_only=True,
                exclude_paused_projects=True,
                limit=20,
            )
        except Exception as e:
            overdue = []
            warnings.append(f"Overdue query failed: {e}")

        # Due today
        try:
            due_today = list_tasks(
                deadline_filter="today",
                top_level_only=True,
                exclude_paused_projects=True,
                limit=20,
            )
        except Exception as e:
            due_today = []
            warnings.append(f"Due-today query failed: {e}")

        # Focus tasks (set for today)
        try:
            focus = list_tasks(
                focus_only=True,
                top_level_only=True,
                exclude_paused_projects=True,
                limit=20,
            )
        except Exception as e:
            focus = []
            warnings.append(f"Focus query failed: {e}")

        # Energy budget
        try:
            spoons = get_spoon_status()
        except Exception as e:
            spoons = {}
            warnings.append(f"Spoon status unavailable: {e}")

        # Emails awaiting reply
        try:
            from roost.mcp.gmail_helpers import search_messages

            pending_replies = search_messages("label:(To Reply)", max_results=10)
        except Exception as e:
            pending_replies = []
            warnings.append(f"Gmail unavailable: {e}")

        result = {
            "calendar_today": events,
            "in_progress": [_task_summary(t) for t in in_progress],
            "overdue_tasks": [_task_summary(t) for t in overdue],
            "due_today": [_task_summary(t) for t in due_today],
            "focus_tasks": [_task_summary(t) for t in focus],
            "energy": spoons,
            "pending_replies": [_email_summary(m) for m in pending_replies],
            "summary": {
                "events": len(events),
                "in_progress": len(in_progress),
                "overdue": len(overdue),
                "due_today": len(due_today),
                "focus": len(focus),
                "emails_to_reply": len(pending_replies),
            },
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def project_pulse(project: str) -> dict:
    """Get a status snapshot for a project: tasks by status, upcoming events,
    and recent emails involving project contacts.

    Args:
        project: Project name (exact match, e.g. "SIEMLess", "Cisco AI PoD").
    """
    try:
        from roost.calendar_service import get_week_events
        from roost.task_service import list_tasks

        warnings = []

        # All project tasks
        try:
            tasks = list_tasks(
                project=project,
                top_level_only=True,
                exclude_paused_projects=False,
                limit=50,
            )
        except Exception:
            tasks = []

        if not tasks:
            return {"error": f"No tasks found for project '{project}'"}

        # Group tasks by status
        by_status = {}
        for t in tasks:
            s = t.status.value if hasattr(t.status, "value") else str(t.status)
            by_status.setdefault(s, []).append(_task_summary(t))

        # Collect contact names from tasks for event/email matching
        search_terms = {project.lower()}
        for t in tasks:
            ctx = t.context_note if hasattr(t, "context_note") else ""
            if ctx:
                # Extract names/keywords from context notes
                for word in ctx.split():
                    if len(word) > 3 and word[0].isupper():
                        search_terms.add(word.lower())

        # Upcoming events matching project or related terms
        try:
            week_events = get_week_events(days=7)
            project_events = []
            for e in week_events:
                text = ((e.get("summary", "") or "") + " "
                        + (e.get("description", "") or "")).lower()
                if any(term in text for term in search_terms):
                    project_events.append(e)
        except Exception as e:
            project_events = []
            warnings.append(f"Calendar unavailable: {e}")

        # Recent emails mentioning project name
        try:
            from roost.mcp.gmail_helpers import search_messages

            project_emails = search_messages(project, max_results=5)
        except Exception as e:
            project_emails = []
            warnings.append(f"Gmail unavailable: {e}")

        result = {
            "project": project,
            "tasks_by_status": by_status,
            "task_count": len(tasks),
            "upcoming_events": project_events,
            "recent_emails": [_email_summary(m) for m in project_emails],
            "summary": {
                "total_tasks": len(tasks),
                "todo": len(by_status.get("todo", [])),
                "in_progress": len(by_status.get("in_progress", [])),
                "done": len(by_status.get("done", [])),
                "upcoming_events": len(project_events),
                "recent_emails": len(project_emails),
            },
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def prep_for(name: str) -> dict:
    """Prepare context for a meeting or interaction with a person: contact
    details, communication history, related tasks, recent emails, and
    upcoming calendar events.

    Args:
        name: Person's name (partial match, e.g. "Harun", "Marc Ronez").
    """
    try:
        from roost.calendar_service import get_week_events
        from roost.task_service import (
            get_contact_by_name,
            list_assignments_by_contact,
            list_communications,
            list_tasks,
        )

        warnings = []

        # Find contact
        contact = get_contact_by_name(name)
        if not contact:
            return {"error": f"No contact found matching '{name}'"}

        contact_dict = {
            "id": contact.id,
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "notes": contact.notes,
            "entity": contact.entity_name,
        }

        # Communication history
        try:
            comms = list_communications(contact.id, limit=10)
            comms_list = [
                {
                    "type": c.comm_type,
                    "subject": c.subject,
                    "detail": c.detail[:200] if c.detail else "",
                    "date": c.occurred_at,
                }
                for c in comms
            ]
        except Exception as e:
            comms_list = []
            warnings.append(f"Comms history failed: {e}")

        # Project/task assignments (explicit)
        try:
            assignments = list_assignments_by_contact(contact.id)
            project_names = [
                a.project_name
                for a in assignments.get("project_assignments", [])
                if a.project_name
            ]
        except Exception:
            project_names = []

        # Tasks mentioning this person (implicit — catches unassigned refs)
        related_tasks = []
        seen_task_ids = set()
        try:
            # First: tasks from explicitly assigned projects
            for pname in project_names[:5]:
                if pname:
                    ptasks = list_tasks(
                        project=pname,
                        top_level_only=True,
                        exclude_paused_projects=False,
                        limit=10,
                    )
                    for t in ptasks:
                        if t.id not in seen_task_ids:
                            seen_task_ids.add(t.id)
                            related_tasks.append(_task_summary(t))

            # Second: scan all active tasks for name mentions
            import re

            all_active = list_tasks(
                top_level_only=True,
                exclude_paused_projects=True,
                limit=100,
            )
            name_pattern = re.compile(
                r"\b" + re.escape(name) + r"\b", re.IGNORECASE
            )
            for t in all_active:
                if t.id in seen_task_ids:
                    continue
                title = t.title or ""
                desc = t.description or ""
                ctx = (t.context_note if hasattr(t, "context_note") else "") or ""
                searchable = f"{title} {desc} {ctx}"
                if name_pattern.search(searchable):
                    seen_task_ids.add(t.id)
                    related_tasks.append(_task_summary(t))
        except Exception as e:
            warnings.append(f"Task search failed: {e}")

        # Recent emails — search by email address if available, fall back to name
        try:
            from roost.mcp.gmail_helpers import search_messages

            if contact.email:
                query = f"from:{contact.email} OR to:{contact.email}"
            else:
                query = contact.name
            emails = search_messages(query, max_results=5)
        except Exception as e:
            emails = []
            warnings.append(f"Gmail unavailable: {e}")

        # Upcoming events — match by name and email
        try:
            week_events = get_week_events(days=7)
            match_terms = [name.lower()]
            if contact.email:
                match_terms.append(contact.email.lower())
            person_events = []
            for e in week_events:
                text = ((e.get("summary", "") or "") + " "
                        + (e.get("description", "") or "")).lower()
                if any(term in text for term in match_terms):
                    person_events.append(e)
        except Exception as e:
            person_events = []
            warnings.append(f"Calendar unavailable: {e}")

        result = {
            "contact": contact_dict,
            "communication_history": comms_list,
            "project_assignments": project_names,
            "related_tasks": related_tasks,
            "recent_emails": [_email_summary(m) for m in emails],
            "upcoming_events": person_events,
            "summary": {
                "projects": len(project_names),
                "related_tasks": len(related_tasks),
                "recent_comms": len(comms_list),
                "recent_emails": len(emails),
                "upcoming_events": len(person_events),
            },
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except Exception as e:
        return {"error": str(e)}
