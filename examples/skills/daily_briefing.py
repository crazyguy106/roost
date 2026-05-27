"""Morning briefing — calendar + tasks + energy in one message.

Designed to run on a cron trigger at 09:00. Pulls today's calendar
events, the tasks flagged for today, and any outstanding follow-ups,
then assembles a single formatted message.

This is the kind of skill the curriculum builds toward in Module 6:
your accumulated context (CAGE) turning into daily utility.
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)

SKILL_META = {
    "name": "daily_briefing",
    "description": "Morning briefing: today's calendar, tasks, and follow-ups.",
    "trigger": "briefing",
    "version": "1.0.0",
    "risk_tier": "read_only",
}


async def run(args: dict) -> str:
    """
    No args required. Optional:
        greeting: str  — custom greeting line (default "Good morning")
    """
    greeting = args.get("greeting", "Good morning")
    today = datetime.now().strftime("%A, %B %d")

    sections: list[str] = [f"## {greeting}. Here's your {today}.\n"]

    # -- Calendar --
    events = _get_today_events()
    if events:
        sections.append("### Today's calendar")
        for ev in events[:8]:  # cap at 8 to keep the message readable
            time_str = ev.get("start_time", "")
            title = ev.get("title", "(untitled)")
            sections.append(f"- **{time_str}** — {title}")
        if len(events) > 8:
            sections.append(f"- _…and {len(events) - 8} more._")
        sections.append("")
    else:
        sections.append("### Today's calendar\n- Nothing on the calendar.\n")

    # -- Tasks due today --
    tasks = _get_tasks_due_today()
    if tasks:
        sections.append("### Due today")
        for t in tasks[:10]:
            priority = "⚡" if t.get("priority") == "high" else "•"
            sections.append(f"- {priority} {t.get('title', '(untitled)')}")
        sections.append("")
    else:
        sections.append("### Due today\n- Nothing due today.\n")

    # -- Focus suggestion --
    focus = _suggest_focus(tasks)
    if focus:
        sections.append(f"### Suggested focus\n{focus}\n")

    return "\n".join(sections)


def _get_today_events() -> list[dict]:
    """Fetch today's calendar events, gracefully handling missing config."""
    try:
        from roost.calendar_service import get_today_events
        return get_today_events()
    except ImportError:
        logger.info("calendar service not enabled")
        return []
    except Exception:
        logger.exception("calendar fetch failed")
        return []


def _get_tasks_due_today() -> list[dict]:
    """Fetch tasks whose deadline is today (in_progress + todo)."""
    try:
        from roost.services.tasks import list_tasks
        today_str = datetime.now().strftime("%Y-%m-%d")
        # list_tasks accepts deadline_filter="today" — let the service do the work
        tasks = []
        for status in ("todo", "in_progress"):
            tasks.extend(list_tasks(status=status, deadline_filter="today"))
        # Task is a pydantic model — convert to plain dicts
        return [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in tasks]
    except ImportError:
        return []
    except Exception:
        logger.exception("task fetch failed")
        return []


def _suggest_focus(tasks: list[dict]) -> str:
    """Pick one task as the 'most important'. Naive heuristic for now."""
    if not tasks:
        return ""
    high = [t for t in tasks if t.get("priority") == "high"]
    pick = high[0] if high else tasks[0]
    return f"**{pick.get('title', '(untitled)')}** — start here before anything else."
