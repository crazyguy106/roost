"""Calendar integration — Google Calendar API fetch + task deadline export.

Fetches events from all Google Calendars via OAuth API, merges with
task deadlines, and exports task deadlines as .ics files.

Multi-tenant: calendar cache is partitioned by user_id so each user
sees only their own calendar events.
"""

import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from roost.bot.tz import get_local_now

logger = logging.getLogger("roost.calendar")

# Per-user in-memory cache: {user_id: {"events": [...], "fetched_at": timestamp}}
_cache: dict[int, dict] = {}
CACHE_TTL = 900  # 15 minutes


def _resolve_uid() -> int:
    """Resolve current user_id for cache partitioning."""
    try:
        from roost.user_context import get_current_user_id
        return get_current_user_id()
    except Exception:
        return 0  # Fallback for contexts without user init


def _get_user_cache() -> dict:
    """Get or create cache entry for current user."""
    uid = _resolve_uid()
    if uid not in _cache:
        _cache[uid] = {"events": None, "fetched_at": 0}
    return _cache[uid]


def _is_calendar_available() -> bool:
    """Check if Calendar API is usable (OAuth tokens present)."""
    try:
        from roost.gmail import is_gmail_available
        return is_gmail_available()
    except Exception:
        return False


def fetch_calendar_events() -> list[dict]:
    """Fetch events from all Google Calendars via OAuth API.

    Each event has: summary, start, end, location, description
    Results are cached per-user for 15 minutes.
    """
    if not _is_calendar_available():
        return []

    ucache = _get_user_cache()
    now = time.time()
    if ucache["events"] is not None and (now - ucache["fetched_at"]) < CACHE_TTL:
        return ucache["events"]

    try:
        from roost.gmail import get_calendar_service

        service = get_calendar_service()
        if not service:
            return []

        # Discover all calendars
        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get("items", [])

        # Time window: from start of today to 30 days out
        now_dt = get_local_now()
        time_min = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        time_max = time_min + timedelta(days=30)

        events = []
        for cal in calendars:
            cal_id = cal["id"]
            cal_name = cal.get("summary", cal_id)
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min.isoformat() + "Z",
                    timeMax=time_max.isoformat() + "Z",
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                ).execute()

                for item in result.get("items", []):
                    start_raw = item.get("start", {})
                    end_raw = item.get("end", {})

                    start = _parse_event_time(start_raw)
                    end = _parse_event_time(end_raw)

                    events.append({
                        "summary": item.get("summary", "(No title)"),
                        "start": start,
                        "end": end,
                        "location": item.get("location", ""),
                        "description": item.get("description", ""),
                        "calendar": cal_name,
                    })
            except Exception:
                logger.warning("Failed to fetch events from calendar %s", cal_name)
                continue

        # Sort by start time
        events.sort(key=lambda e: e["start"] or datetime.max)
        ucache["events"] = events
        ucache["fetched_at"] = time.time()
        logger.info("Fetched %d calendar events from %d calendars", len(events), len(calendars))
        return events

    except Exception:
        logger.exception("Failed to fetch calendar events")
        return ucache.get("events") or []


def _parse_event_time(time_dict: dict) -> datetime | None:
    """Parse a Google Calendar event time (dateTime or date) into a datetime."""
    if not time_dict:
        return None

    # dateTime: full datetime (timed events)
    dt_str = time_dict.get("dateTime")
    if dt_str:
        try:
            # Handle timezone offset format: 2026-02-10T09:00:00+08:00
            from datetime import timezone
            if "+" in dt_str[10:] or dt_str.endswith("Z"):
                # Strip timezone info for naive datetime (local display)
                dt_str_clean = dt_str.replace("Z", "+00:00")
                dt = datetime.fromisoformat(dt_str_clean)
                return dt.replace(tzinfo=None)
            return datetime.fromisoformat(dt_str)
        except (ValueError, TypeError):
            return None

    # date: all-day event
    d_str = time_dict.get("date")
    if d_str:
        try:
            return datetime.strptime(d_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            return None

    return None


def fetch_events_for_range(start_date, end_date) -> list[dict]:
    """Fetch events from all Google Calendars for a specific date range.

    Args:
        start_date: date or datetime — inclusive start
        end_date: date or datetime — inclusive end

    Returns list of event dicts (same shape as fetch_calendar_events).
    No caching — callers manage their own state.
    Max range enforced: 42 days.
    """
    from datetime import date

    # Normalise to datetime
    if isinstance(start_date, date) and not isinstance(start_date, datetime):
        start_date = datetime(start_date.year, start_date.month, start_date.day)
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        end_date = datetime(end_date.year, end_date.month, end_date.day, 23, 59, 59)

    # Enforce max 42-day range
    if (end_date - start_date).days > 42:
        end_date = start_date + timedelta(days=42)

    if not _is_calendar_available():
        return []

    try:
        from roost.gmail import get_calendar_service

        service = get_calendar_service()
        if not service:
            return []

        calendar_list = service.calendarList().list().execute()
        calendars = calendar_list.get("items", [])

        events = []
        for cal in calendars:
            cal_id = cal["id"]
            cal_name = cal.get("summary", cal_id)
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=start_date.isoformat() + "Z",
                    timeMax=end_date.isoformat() + "Z",
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=250,
                ).execute()

                for item in result.get("items", []):
                    start_raw = item.get("start", {})
                    end_raw = item.get("end", {})
                    start = _parse_event_time(start_raw)
                    end = _parse_event_time(end_raw)

                    # Detect all-day events
                    all_day = "date" in start_raw and "dateTime" not in start_raw

                    events.append({
                        "summary": item.get("summary", "(No title)"),
                        "start": start,
                        "end": end,
                        "location": item.get("location", ""),
                        "description": item.get("description", ""),
                        "calendar": cal_name,
                        "all_day": all_day,
                    })
            except Exception:
                logger.warning("Failed to fetch events from calendar %s", cal_name)
                continue

        events.sort(key=lambda e: e["start"] or datetime.max)
        logger.info("Fetched %d events for range %s–%s", len(events),
                     start_date.date(), end_date.date())
        return events

    except Exception:
        logger.exception("Failed to fetch calendar events for range")
        return []


def get_today_events() -> list[dict]:
    """Filter calendar events to today only."""
    events = fetch_calendar_events()
    today = get_local_now().date()
    return [
        e for e in events
        if e.get("start") and e["start"].date() == today
    ]


def get_week_events(days: int = 7) -> list[dict]:
    """Get calendar events for the next N days."""
    events = fetch_calendar_events()
    now = get_local_now()
    end = now + timedelta(days=days)
    return [
        e for e in events
        if e.get("start") and now.date() <= e["start"].date() <= end.date()
    ]


def export_tasks_to_ics() -> str:
    """Export all task deadlines as an .ics file. Returns file path."""
    try:
        from icalendar import Calendar, Event as IcsEvent
    except ImportError:
        raise RuntimeError("icalendar package not installed")

    from roost import task_service

    cal = Calendar()
    cal.add("prodid", "-//Roost//Task Deadlines//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")

    tasks = task_service.list_tasks(deadline_filter="has_deadline")
    for t in tasks:
        if not t.deadline:
            continue
        event = IcsEvent()
        event.add("summary", f"[{t.priority.value.upper()}] {t.title}")
        event.add("dtstart", t.deadline)
        event.add("dtend", t.deadline + timedelta(hours=1))
        if t.description:
            event.add("description", t.description)
        event.add("uid", f"roost-task-{t.id}@localhost")
        if t.project_name:
            event.add("categories", [t.project_name])
        cal.add_component(event)

    out_dir = Path("/app/data")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "task_deadlines.ics"
    path.write_bytes(cal.to_ical())
    logger.info("Exported %d task deadlines to %s", len(tasks), path)
    return str(path)


def get_merged_today() -> dict:
    """Calendar events + task triage merged for today view.

    Returns: {events: [...], triage: {...}}
    """
    from roost.triage import get_today_tasks

    events = get_today_events()
    triage = get_today_tasks()

    return {
        "events": events,
        "triage": triage,
    }
