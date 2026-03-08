"""MCP tools for calendar event queries."""

from roost.mcp.server import mcp


@mcp.tool()
def get_today_events(google_account: str = "") -> dict:
    """Get all calendar events for today.

    Args:
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.calendar_service import get_today_events as _today

        events = _today()
        return {
            "count": len(events),
            "events": [_event_dict(e) for e in events],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_week_events(days: int = 7, google_account: str = "") -> dict:
    """Get calendar events for the next N days.

    Args:
        days: Number of days to look ahead (default 7).
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.calendar_service import get_week_events as _week

        events = _week(days=days)
        return {
            "count": len(events),
            "events": [_event_dict(e) for e in events],
        }
    except Exception as e:
        return {"error": str(e)}


def _event_dict(event: dict) -> dict:
    """Convert a calendar event dict to a serialisable format."""
    start = event.get("start")
    end = event.get("end")
    return {
        "summary": event.get("summary", ""),
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "location": event.get("location", ""),
        "description": event.get("description", "")[:500],
        "calendar": event.get("calendar", ""),
        "all_day": event.get("all_day", False),
    }
