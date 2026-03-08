"""MCP tools for calendar write operations - create, update, delete events."""

from roost.mcp.server import mcp


@mcp.tool()
def calendar_create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    all_day: bool = False,
    timezone: str = "Asia/Singapore",
    google_account: str = "",
) -> dict:
    """Create a new Google Calendar event.

    Accepts natural language for dates/times - e.g. "next tuesday 2pm",
    "tomorrow 10am", "march 5 at 3pm". Normalized automatically.

    Args:
        summary: Event title.
        start: Start time - "next tuesday 2pm", "tomorrow 10am", or
            ISO format "2026-02-15T09:00:00" / "2026-02-15" (all-day).
        end: End time - "next tuesday 3pm", or ISO format. For all-day
            events use the day after (exclusive).
        description: Optional event description.
        location: Optional event location.
        calendar_id: Calendar to create in (default "primary").
        all_day: Whether this is an all-day event.
        timezone: Timezone for timed events (default "Asia/Singapore").
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.gmail import get_calendar_service
        from roost.mcp.normalize import normalize

        acct = google_account or None

        # Normalize date/time inputs
        if all_day:
            fields = normalize({"deadline": start})
            start = fields.get("deadline", start)
            fields = normalize({"deadline": end})
            end = fields.get("deadline", end)
        else:
            fields = normalize({"start": start, "end": end})
            start = fields.get("start", start)
            end = fields.get("end", end)

        service = get_calendar_service(account=acct)
        if not service:
            return {"error": "Calendar service unavailable - check OAuth"}

        if all_day:
            event_body = {
                "summary": summary,
                "start": {"date": start[:10]},
                "end": {"date": end[:10]},
            }
        else:
            event_body = {
                "summary": summary,
                "start": {"dateTime": start, "timeZone": timezone},
                "end": {"dateTime": end, "timeZone": timezone},
            }

        if description:
            event_body["description"] = description
        if location:
            event_body["location"] = location

        event = service.events().insert(
            calendarId=calendar_id, body=event_body,
        ).execute()

        return {
            "event_id": event.get("id"),
            "summary": event.get("summary"),
            "html_link": event.get("htmlLink"),
            "start": start,
            "end": end,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def calendar_update_event(
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    calendar_id: str = "primary",
    all_day: bool | None = None,
    timezone: str = "Asia/Singapore",
    google_account: str = "",
) -> dict:
    """Update an existing Google Calendar event.

    Only provide the fields you want to change - others remain untouched.
    Accepts natural language for dates/times.

    Args:
        event_id: The event ID to update.
        summary: New event title (empty = no change).
        start: New start - "next tuesday 2pm", ISO format, or empty.
        end: New end - "next tuesday 3pm", ISO format, or empty.
        description: New description (empty = no change).
        location: New location (empty = no change).
        calendar_id: Calendar containing the event (default "primary").
        all_day: Set true for all-day, false for timed. None = auto-detect
            from existing event.
        timezone: Timezone for timed events.
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.gmail import get_calendar_service
        from roost.mcp.normalize import normalize

        acct = google_account or None
        service = get_calendar_service(account=acct)
        if not service:
            return {"error": "Calendar service unavailable - check OAuth"}

        # Auto-detect all_day from existing event if not specified
        if all_day is None and (start or end):
            existing = service.events().get(
                calendarId=calendar_id, eventId=event_id,
            ).execute()
            all_day = "date" in existing.get("start", {})

        # Normalize date/time inputs
        if start or end:
            if all_day:
                if start:
                    fields = normalize({"deadline": start})
                    start = fields.get("deadline", start)
                if end:
                    fields = normalize({"deadline": end})
                    end = fields.get("deadline", end)
            else:
                to_norm = {}
                if start:
                    to_norm["start"] = start
                if end:
                    to_norm["end"] = end
                fields = normalize(to_norm)
                start = fields.get("start", start)
                end = fields.get("end", end)

        patch_body = {}
        if summary:
            patch_body["summary"] = summary
        if start:
            if all_day:
                patch_body["start"] = {"date": start[:10]}
            else:
                patch_body["start"] = {"dateTime": start, "timeZone": timezone}
        if end:
            if all_day:
                patch_body["end"] = {"date": end[:10]}
            else:
                patch_body["end"] = {"dateTime": end, "timeZone": timezone}
        if description:
            patch_body["description"] = description
        if location:
            patch_body["location"] = location

        if not patch_body:
            return {"error": "No fields to update"}

        event = service.events().patch(
            calendarId=calendar_id, eventId=event_id, body=patch_body,
        ).execute()

        return {
            "event_id": event.get("id"),
            "summary": event.get("summary"),
            "updated": event.get("updated"),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def calendar_delete_event(
    event_id: str,
    calendar_id: str = "primary",
    google_account: str = "",
) -> dict:
    """Delete a Google Calendar event.

    Args:
        event_id: The event ID to delete.
        calendar_id: Calendar containing the event (default "primary").
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.gmail import get_calendar_service

        acct = google_account or None
        service = get_calendar_service(account=acct)
        if not service:
            return {"error": "Calendar service unavailable - check OAuth"}

        service.events().delete(
            calendarId=calendar_id, eventId=event_id,
        ).execute()

        return {"ok": True, "event_id": event_id, "message": "Event deleted"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def calendar_list_calendars(google_account: str = "") -> dict:
    """List all Google Calendars the user has access to.

    Returns calendar IDs, names, and access roles.

    Args:
        google_account: Google account email to use (empty = default account).
    """
    try:
        from roost.gmail import get_calendar_service

        acct = google_account or None
        service = get_calendar_service(account=acct)
        if not service:
            return {"error": "Calendar service unavailable - check OAuth"}

        result = service.calendarList().list().execute()
        calendars = []
        for cal in result.get("items", []):
            calendars.append({
                "id": cal.get("id"),
                "summary": cal.get("summary"),
                "primary": cal.get("primary", False),
                "access_role": cal.get("accessRole"),
                "background_color": cal.get("backgroundColor"),
            })

        return {"count": len(calendars), "calendars": calendars}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def calendar_search_events(
    query: str,
    days: int = 30,
    calendar_id: str = "primary",
    google_account: str = "",
) -> dict:
    """Search calendar events by text query.

    Args:
        query: Free-text search query (searches summary, description, location).
        days: Number of days to search ahead from today (default 30).
        calendar_id: Calendar to search (default "primary").
        google_account: Google account email to use (empty = default account).
    """
    try:
        from datetime import datetime, timedelta
        from roost.gmail import get_calendar_service

        acct = google_account or None
        service = get_calendar_service(account=acct)
        if not service:
            return {"error": "Calendar service unavailable - check OAuth"}

        now = datetime.now()
        time_min = now.isoformat() + "Z"
        time_max = (now + timedelta(days=days)).isoformat() + "Z"

        result = service.events().list(
            calendarId=calendar_id,
            q=query,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            maxResults=50,
        ).execute()

        events = []
        for item in result.get("items", []):
            start = item.get("start", {})
            end = item.get("end", {})
            events.append({
                "event_id": item.get("id"),
                "summary": item.get("summary", ""),
                "start": start.get("dateTime") or start.get("date"),
                "end": end.get("dateTime") or end.get("date"),
                "location": item.get("location", ""),
                "description": (item.get("description", "") or "")[:500],
            })

        return {"query": query, "count": len(events), "events": events}
    except Exception as e:
        return {"error": str(e)}
