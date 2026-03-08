"""MCP tools for Microsoft Outlook calendar operations."""

from roost.mcp.server import mcp


@mcp.tool()
def ms_get_today_events() -> dict:
    """Get all Microsoft Calendar events for today."""
    try:
        from datetime import timedelta
        from roost.bot.tz import get_local_now
        from roost.mcp.ms_graph_helpers import get_calendar_events

        now = get_local_now()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        end = (now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).isoformat()

        events = get_calendar_events(start=start, end=end)
        return {
            "date": now.strftime("%Y-%m-%d"),
            "count": len(events),
            "events": events,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_get_week_events(days: int = 7) -> dict:
    """Get Microsoft Calendar events for the next N days.

    Args:
        days: Number of days to look ahead (default 7).
    """
    try:
        from datetime import timedelta
        from roost.bot.tz import get_local_now
        from roost.mcp.ms_graph_helpers import get_calendar_events

        now = get_local_now()
        start = now.isoformat()
        end = (now + timedelta(days=days)).isoformat()

        events = get_calendar_events(start=start, end=end)
        return {
            "days": days,
            "count": len(events),
            "events": events,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_calendar_create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    calendar_id: str = "",
    all_day: bool = False,
    timezone: str = "",
) -> dict:
    """Create a new Microsoft Calendar event.

    Args:
        summary: Event title.
        start: Start time — ISO format "2026-02-15T09:00:00" for timed events,
            or "2026-02-15" for all-day events.
        end: End time — ISO format "2026-02-15T10:00:00" for timed events,
            or "2026-02-16" for all-day events (exclusive end date).
        description: Optional event description.
        location: Optional event location.
        calendar_id: Calendar to create in (empty = default calendar).
        all_day: Whether this is an all-day event.
        timezone: Timezone for timed events (default "Asia/Singapore").
    """
    try:
        from roost.config import REMINDER_TIMEZONE
        from roost.mcp.ms_graph_helpers import create_event

        return create_event(
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            calendar_id=calendar_id,
            all_day=all_day,
            timezone=timezone or REMINDER_TIMEZONE,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_calendar_update_event(
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    timezone: str = "",
) -> dict:
    """Update an existing Microsoft Calendar event.

    Only provide the fields you want to change — others remain untouched.

    Args:
        event_id: The event ID to update.
        summary: New event title (empty = no change).
        start: New start time in ISO format (empty = no change).
        end: New end time in ISO format (empty = no change).
        description: New description (empty = no change).
        location: New location (empty = no change).
        timezone: Timezone for timed events.
    """
    try:
        from roost.config import REMINDER_TIMEZONE
        from roost.mcp.ms_graph_helpers import update_event

        return update_event(
            event_id=event_id,
            summary=summary,
            start=start,
            end=end,
            description=description,
            location=location,
            timezone=timezone or REMINDER_TIMEZONE,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_calendar_delete_event(event_id: str) -> dict:
    """Delete a Microsoft Calendar event.

    Args:
        event_id: The event ID to delete.
    """
    try:
        from roost.mcp.ms_graph_helpers import delete_event

        return delete_event(event_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_calendar_list_calendars() -> dict:
    """List all Microsoft Calendars the user has access to.

    Returns calendar IDs, names, and permissions.
    """
    try:
        from roost.mcp.ms_graph_helpers import list_calendars

        calendars = list_calendars()
        return {"count": len(calendars), "calendars": calendars}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def ms_calendar_search_events(query: str, days: int = 30) -> dict:
    """Search Microsoft Calendar events by text query.

    Fetches events for the given number of days and filters locally
    by summary, description, and location.

    Args:
        query: Free-text search query.
        days: Number of days to search ahead from today (default 30).
    """
    try:
        from datetime import timedelta
        from roost.bot.tz import get_local_now
        from roost.mcp.ms_graph_helpers import get_calendar_events

        now = get_local_now()
        start = now.isoformat()
        end = (now + timedelta(days=days)).isoformat()

        all_events = get_calendar_events(start=start, end=end)

        # Filter locally — Graph calendarView doesn't support $search
        query_lower = query.lower()
        matched = [
            e for e in all_events
            if query_lower in e.get("summary", "").lower()
            or query_lower in e.get("description", "").lower()
            or query_lower in e.get("location", "").lower()
        ]

        return {
            "query": query,
            "days": days,
            "count": len(matched),
            "events": matched,
        }
    except Exception as e:
        return {"error": str(e)}
