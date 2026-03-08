"""Microsoft Graph API helper functions used by tools_ms_*.py modules.

Wraps raw Graph API calls into clean functions with error handling,
pagination, rate-limit retries, and body truncation.
"""

import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

logger = logging.getLogger("roost.mcp.ms_graph")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
MAX_BODY_CHARS = 10_000  # Truncate long email bodies
MAX_RETRIES = 3


def _escape_kql(s: str) -> str:
    """Escape special characters for KQL $search queries."""
    for char in ('"', "'", "\\"):
        s = s.replace(char, f"\\{char}")
    return s


def _get_session() -> requests.Session:
    """Get an authenticated Graph session, raising on failure."""
    from roost.microsoft import get_graph_session

    session = get_graph_session()
    if not session:
        from roost.user_context import get_current_user
        ctx = get_current_user()
        raise RuntimeError(
            f"Microsoft Graph not available for {ctx.email}. "
            "Visit the web UI and click 'Login with Microsoft' to authorize your account."
        )
    return session


def _graph_request(
    method: str,
    endpoint: str,
    session: requests.Session | None = None,
    json_body: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
) -> dict:
    """Make a Graph API request with retry on 429/5xx and 401 handling.

    Args:
        method: HTTP method (GET, POST, PATCH, DELETE).
        endpoint: Graph API endpoint path (e.g. "/me/messages").
        session: Authenticated session (fetched if None).
        json_body: Request JSON body for POST/PATCH.
        params: Query parameters.
        timeout: Request timeout in seconds.

    Returns:
        Response JSON dict (empty dict for 204 No Content).

    Raises:
        RuntimeError: On non-retryable HTTP errors.
    """
    if session is None:
        session = _get_session()

    url = f"{GRAPH_BASE}{endpoint}"

    for attempt in range(MAX_RETRIES):
        resp = session.request(
            method=method,
            url=url,
            json=json_body,
            params=params,
            timeout=timeout,
        )

        # Rate limited — honour Retry-After header
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            logger.warning("Graph API rate limited, retrying in %ds", retry_after)
            time.sleep(retry_after)
            continue

        # Server error (502/503/504) — retry with exponential backoff
        if resp.status_code in (502, 503, 504):
            backoff = 2 ** attempt
            logger.warning(
                "Graph API %d on attempt %d, retrying in %ds",
                resp.status_code, attempt + 1, backoff,
            )
            time.sleep(backoff)
            continue

        # Unauthorized — token expired mid-request, clear cache and give actionable message
        if resp.status_code == 401:
            logger.warning("Graph API 401 — token may be expired or revoked")
            try:
                from roost.microsoft.client import store_token_cache
                store_token_cache("")  # Clear stale cache so next call forces re-auth
            except Exception:
                pass
            raise RuntimeError(
                "Microsoft Graph returned 401 Unauthorized. "
                "Token has been cleared — the next request will attempt a fresh token. "
                "If this persists, re-authorize at /auth/microsoft."
            )

        if resp.status_code in (202, 204):
            return {}

        if resp.status_code >= 400:
            try:
                error_data = resp.json()
                error_msg = error_data.get("error", {}).get("message", resp.text[:500])
            except Exception:
                error_msg = resp.text[:500]
            raise RuntimeError(f"Graph API {resp.status_code}: {error_msg}")

        return resp.json()

    raise RuntimeError("Graph API: max retries exceeded (rate-limited or server errors)")


MAX_PAGINATION_PAGES = 50  # Safety guard against infinite pagination loops


def _paginate(
    endpoint: str,
    session: requests.Session | None = None,
    params: dict | None = None,
    max_items: int = 100,
) -> list[dict]:
    """Follow @odata.nextLink to collect paginated results.

    Args:
        endpoint: Graph API endpoint path.
        session: Authenticated session.
        params: Initial query parameters.
        max_items: Maximum total items to collect.

    Returns:
        List of value items across all pages.
    """
    if session is None:
        session = _get_session()

    items = []
    url = f"{GRAPH_BASE}{endpoint}"
    iterations = 0

    while url and len(items) < max_items:
        iterations += 1
        if iterations > MAX_PAGINATION_PAGES:
            logger.warning(
                "Pagination guard: stopped after %d pages (%d items) for %s",
                MAX_PAGINATION_PAGES, len(items), endpoint,
            )
            break

        resp = session.get(url, params=params, timeout=30)

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2))
            time.sleep(retry_after)
            continue

        if resp.status_code >= 400:
            break

        data = resp.json()
        items.extend(data.get("value", []))

        # After first request, params are embedded in nextLink
        params = None
        url = data.get("@odata.nextLink")

    return items[:max_items]


# ---------------------------------------------------------------------------
# Email helpers
# ---------------------------------------------------------------------------

def search_messages(query: str, max_results: int = 10) -> list[dict]:
    """Search Outlook messages via Graph $search.

    Args:
        query: KQL search query (e.g. "from:alice subject:invoice").
        max_results: Maximum messages to return.

    Returns:
        List of message summary dicts.
    """
    session = _get_session()
    params = {
        "$search": f'"{_escape_kql(query)}"',
        "$top": min(max_results, 50),
        "$select": "id,conversationId,subject,from,toRecipients,receivedDateTime,bodyPreview,parentFolderId",
        # NOTE: $orderby is NOT supported with $search — sort client-side
    }

    data = _graph_request("GET", "/me/messages", session=session, params=params)
    messages = data.get("value", [])

    output = []
    for msg in messages:
        from_addr = msg.get("from", {}).get("emailAddress", {})
        to_list = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        output.append({
            "id": msg.get("id", ""),
            "conversationId": msg.get("conversationId", ""),
            "subject": msg.get("subject", ""),
            "from": f"{from_addr.get('name', '')} <{from_addr.get('address', '')}>",
            "to": ", ".join(to_list),
            "date": msg.get("receivedDateTime", ""),
            "snippet": msg.get("bodyPreview", "")[:200],
        })

    # Sort newest-first (Graph ignores $orderby when $search is present)
    output.sort(key=lambda m: m.get("date", ""), reverse=True)
    return output


def read_conversation(conversation_id: str) -> dict:
    """Read all messages in an Outlook conversation.

    Args:
        conversation_id: The Outlook conversation ID.

    Returns:
        Dict with conversation metadata and list of messages with bodies.
    """
    session = _get_session()
    params = {
        "$filter": f"conversationId eq '{conversation_id}'",
        "$select": "id,conversationId,subject,from,toRecipients,ccRecipients,receivedDateTime,body,internetMessageId",
        "$orderby": "receivedDateTime asc",
        "$top": 50,
    }

    data = _graph_request("GET", "/me/messages", session=session, params=params)
    raw_messages = data.get("value", [])

    messages = []
    for msg in raw_messages:
        from_addr = msg.get("from", {}).get("emailAddress", {})
        to_list = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
        ]
        cc_list = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("ccRecipients", [])
        ]

        body = msg.get("body", {}).get("content", "")
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS] + "\n\n[... truncated ...]"

        messages.append({
            "id": msg.get("id", ""),
            "subject": msg.get("subject", ""),
            "from": f"{from_addr.get('name', '')} <{from_addr.get('address', '')}>",
            "to": ", ".join(to_list),
            "cc": ", ".join(cc_list),
            "date": msg.get("receivedDateTime", ""),
            "message_id": msg.get("internetMessageId", ""),
            "body": body,
        })

    return {
        "conversationId": conversation_id,
        "message_count": len(messages),
        "messages": messages,
    }


def send_message(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    reply_to_id: str = "",
    attachment_paths: list[str] | None = None,
) -> dict:
    """Send an email via Microsoft Graph.

    Args:
        to: Recipient email address(es), comma-separated.
        subject: Email subject line.
        body: Plain text body.
        cc: CC recipients, comma-separated.
        bcc: BCC recipients, comma-separated.
        reply_to_id: Message ID to reply to (creates a reply).
        attachment_paths: List of absolute file paths to attach.

    Returns:
        Dict with status on success, or error.
    """
    import base64

    session = _get_session()

    def _make_recipients(addr_str: str) -> list[dict]:
        return [
            {"emailAddress": {"address": a.strip()}}
            for a in addr_str.split(",")
            if a.strip()
        ]

    # Reply flow
    if reply_to_id:
        reply_body = {
            "message": {
                "body": {"contentType": "Text", "content": body},
            },
        }
        if cc:
            reply_body["message"]["ccRecipients"] = _make_recipients(cc)

        _graph_request(
            "POST",
            f"/me/messages/{reply_to_id}/reply",
            session=session,
            json_body=reply_body,
        )
        return {"status": "sent", "reply_to": reply_to_id}

    # New message flow
    message = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": _make_recipients(to),
    }

    if cc:
        message["ccRecipients"] = _make_recipients(cc)
    if bcc:
        message["bccRecipients"] = _make_recipients(bcc)

    # Attachments (simple upload, <4MB each)
    if attachment_paths:
        attachments = []
        for path_str in attachment_paths:
            path = Path(path_str)
            if not path.exists():
                return {"error": f"Attachment not found: {path_str}"}
            if path.stat().st_size > 4 * 1024 * 1024:
                return {"error": f"Attachment too large (>4MB): {path.name}. Use OneDrive for large files."}
            content_bytes = base64.b64encode(path.read_bytes()).decode()
            attachments.append({
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name,
                "contentBytes": content_bytes,
            })
        message["attachments"] = attachments

    _graph_request(
        "POST",
        "/me/sendMail",
        session=session,
        json_body={"message": message},
    )

    return {"status": "sent"}


def list_folders() -> list[dict]:
    """List all Outlook mail folders.

    Returns:
        List of dicts with id, name, unreadItemCount, totalItemCount.
    """
    session = _get_session()
    data = _graph_request("GET", "/me/mailFolders", session=session, params={"$top": 100})
    folders = data.get("value", [])

    return [
        {
            "id": f.get("id", ""),
            "name": f.get("displayName", ""),
            "unread_count": f.get("unreadItemCount", 0),
            "total_count": f.get("totalItemCount", 0),
        }
        for f in folders
    ]


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def list_calendars() -> list[dict]:
    """List all calendars the user has access to.

    Returns:
        List of calendar summary dicts.
    """
    session = _get_session()
    data = _graph_request("GET", "/me/calendars", session=session)
    calendars = data.get("value", [])

    return [
        {
            "id": cal.get("id", ""),
            "name": cal.get("name", ""),
            "is_default": cal.get("isDefaultCalendar", False),
            "can_edit": cal.get("canEdit", False),
            "color": cal.get("hexColor", ""),
            "owner": cal.get("owner", {}).get("address", ""),
        }
        for cal in calendars
    ]


def get_calendar_events(
    start: str,
    end: str,
    calendar_id: str = "",
) -> list[dict]:
    """Get calendar events within a time range using calendarView.

    Args:
        start: ISO datetime string for range start.
        end: ISO datetime string for range end.
        calendar_id: Specific calendar ID (empty = default calendar).

    Returns:
        List of event dicts.
    """
    session = _get_session()

    if calendar_id:
        endpoint = f"/me/calendars/{calendar_id}/calendarView"
    else:
        endpoint = "/me/calendarView"

    params = {
        "startDateTime": start,
        "endDateTime": end,
        "$select": "id,subject,start,end,location,body,isAllDay,organizer,attendees",
        "$orderby": "start/dateTime asc",
        "$top": 100,
    }

    data = _graph_request("GET", endpoint, session=session, params=params)
    events = data.get("value", [])

    return [_format_event(e) for e in events]


def _format_event(event: dict) -> dict:
    """Format a Graph calendar event into a clean dict."""
    start = event.get("start", {})
    end = event.get("end", {})
    location = event.get("location", {})
    body_content = event.get("body", {}).get("content", "")

    return {
        "event_id": event.get("id", ""),
        "summary": event.get("subject", ""),
        "start": start.get("dateTime", ""),
        "start_tz": start.get("timeZone", ""),
        "end": end.get("dateTime", ""),
        "end_tz": end.get("timeZone", ""),
        "location": location.get("displayName", ""),
        "description": body_content[:500] if body_content else "",
        "all_day": event.get("isAllDay", False),
        "organizer": event.get("organizer", {}).get("emailAddress", {}).get("address", ""),
    }


def create_event(
    summary: str,
    start: str,
    end: str,
    description: str = "",
    location: str = "",
    calendar_id: str = "",
    all_day: bool = False,
    timezone: str = "Asia/Singapore",
) -> dict:
    """Create a new calendar event.

    Args:
        summary: Event title.
        start: Start time — ISO format for timed, date for all-day.
        end: End time — ISO format for timed, date for all-day.
        description: Optional description.
        location: Optional location.
        calendar_id: Calendar to create in (empty = default).
        all_day: Whether this is an all-day event.
        timezone: Timezone for timed events.

    Returns:
        Created event summary dict.
    """
    session = _get_session()

    if calendar_id:
        endpoint = f"/me/calendars/{calendar_id}/events"
    else:
        endpoint = "/me/events"

    if all_day:
        event_body = {
            "subject": summary,
            "start": {"dateTime": f"{start[:10]}T00:00:00", "timeZone": timezone},
            "end": {"dateTime": f"{end[:10]}T00:00:00", "timeZone": timezone},
            "isAllDay": True,
        }
    else:
        event_body = {
            "subject": summary,
            "start": {"dateTime": start, "timeZone": timezone},
            "end": {"dateTime": end, "timeZone": timezone},
        }

    if description:
        event_body["body"] = {"contentType": "Text", "content": description}
    if location:
        event_body["location"] = {"displayName": location}

    result = _graph_request("POST", endpoint, session=session, json_body=event_body)

    return {
        "event_id": result.get("id", ""),
        "summary": result.get("subject", ""),
        "start": start,
        "end": end,
        "web_link": result.get("webLink", ""),
    }


def update_event(
    event_id: str,
    summary: str = "",
    start: str = "",
    end: str = "",
    description: str = "",
    location: str = "",
    timezone: str = "Asia/Singapore",
) -> dict:
    """Update an existing calendar event.

    Only provided fields are updated.

    Args:
        event_id: The event ID to update.
        summary: New title (empty = no change).
        start: New start time (empty = no change).
        end: New end time (empty = no change).
        description: New description (empty = no change).
        location: New location (empty = no change).
        timezone: Timezone for timed events.

    Returns:
        Updated event summary dict.
    """
    session = _get_session()
    patch_body: dict = {}

    if summary:
        patch_body["subject"] = summary
    if start:
        patch_body["start"] = {"dateTime": start, "timeZone": timezone}
    if end:
        patch_body["end"] = {"dateTime": end, "timeZone": timezone}
    if description:
        patch_body["body"] = {"contentType": "Text", "content": description}
    if location:
        patch_body["location"] = {"displayName": location}

    if not patch_body:
        return {"error": "No fields to update"}

    result = _graph_request(
        "PATCH", f"/me/events/{event_id}", session=session, json_body=patch_body,
    )

    return {
        "event_id": result.get("id", ""),
        "summary": result.get("subject", ""),
        "updated": result.get("lastModifiedDateTime", ""),
    }


def delete_event(event_id: str) -> dict:
    """Delete a calendar event.

    Args:
        event_id: The event ID to delete.

    Returns:
        Confirmation dict.
    """
    session = _get_session()
    _graph_request("DELETE", f"/me/events/{event_id}", session=session)
    return {"ok": True, "event_id": event_id, "message": "Event deleted"}


# ---------------------------------------------------------------------------
# OneDrive helpers
# ---------------------------------------------------------------------------

def onedrive_list(path: str = "/", max_results: int = 100) -> list[dict]:
    """List files and folders in a OneDrive path.

    Args:
        path: OneDrive path (e.g. "/" for root, "/Documents/Reports").
        max_results: Maximum items to return.

    Returns:
        List of item dicts.
    """
    session = _get_session()

    if path == "/" or not path:
        endpoint = "/me/drive/root/children"
    else:
        # Normalize path — strip leading/trailing slashes for Graph API
        clean = path.strip("/")
        endpoint = f"/me/drive/root:/{clean}:/children"

    params = {
        "$top": min(max_results, 200),
        "$select": "id,name,size,lastModifiedDateTime,folder,file,webUrl",
    }

    data = _graph_request("GET", endpoint, session=session, params=params)
    items = data.get("value", [])

    entries = []
    for item in items:
        is_dir = "folder" in item
        entries.append({
            "name": item.get("name", ""),
            "id": item.get("id", ""),
            "is_dir": is_dir,
            "size": item.get("size", 0),
            "modified": item.get("lastModifiedDateTime", ""),
            "mime_type": item.get("file", {}).get("mimeType", "") if not is_dir else "",
            "child_count": item.get("folder", {}).get("childCount", 0) if is_dir else 0,
            "web_url": item.get("webUrl", ""),
        })

    # Sort: directories first, then by name
    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 500 MB safety cap


def onedrive_download(remote_path: str, local_dir: str = "/tmp/onedrive-download/") -> dict:
    """Download a file from OneDrive to local filesystem.

    Args:
        remote_path: OneDrive path (e.g. "/Documents/report.docx").
        local_dir: Local directory to save into.

    Returns:
        Dict with download details.
    """
    session = _get_session()
    clean = remote_path.strip("/")

    # Pre-flight size check via metadata
    meta_url = f"{GRAPH_BASE}/me/drive/root:/{clean}"
    meta_resp = session.get(meta_url, timeout=30)
    if meta_resp.status_code < 400:
        file_size = meta_resp.json().get("size", 0)
        if file_size > MAX_DOWNLOAD_BYTES:
            size_mb = file_size / (1024 * 1024)
            return {"error": f"File too large ({size_mb:.0f} MB). Max download size is 500 MB."}

    endpoint = f"/me/drive/root:/{clean}:/content"
    url = f"{GRAPH_BASE}{endpoint}"
    resp = session.get(url, timeout=120, stream=True)

    if resp.status_code >= 400:
        return {"error": f"Download failed: HTTP {resp.status_code}"}

    os.makedirs(local_dir, exist_ok=True)
    filename = clean.split("/")[-1]
    local_path = os.path.join(local_dir, filename)

    downloaded = 0
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded > MAX_DOWNLOAD_BYTES:
                f.close()
                os.remove(local_path)
                return {"error": "Download aborted — exceeded 500 MB safety cap."}
            f.write(chunk)

    return {
        "remote_path": remote_path,
        "local_path": local_path,
        "size": os.path.getsize(local_path),
        "status": "downloaded",
    }


def onedrive_upload(local_path: str, remote_path: str) -> dict:
    """Upload a file to OneDrive (simple upload, <4MB).

    Args:
        local_path: Local file path to upload.
        remote_path: OneDrive destination path (e.g. "/Documents/report.docx").

    Returns:
        Dict with upload details.
    """
    path_obj = Path(local_path)
    if not path_obj.exists():
        return {"error": f"Local file not found: {local_path}"}

    file_size = path_obj.stat().st_size
    if file_size > 4 * 1024 * 1024:
        return {"error": f"File too large ({file_size} bytes). Simple upload supports <4MB."}

    session = _get_session()
    clean = remote_path.strip("/")
    endpoint = f"/me/drive/root:/{clean}:/content"

    url = f"{GRAPH_BASE}{endpoint}"
    with open(local_path, "rb") as f:
        resp = session.put(
            url,
            data=f,
            headers={"Content-Type": "application/octet-stream"},
            timeout=60,
        )

    if resp.status_code >= 400:
        return {"error": f"Upload failed: HTTP {resp.status_code} — {resp.text[:300]}"}

    result = resp.json()
    return {
        "local_path": local_path,
        "remote_path": remote_path,
        "id": result.get("id", ""),
        "size": result.get("size", 0),
        "web_url": result.get("webUrl", ""),
        "status": "uploaded",
    }


def onedrive_search(query: str) -> list[dict]:
    """Search OneDrive for files by name/content.

    Args:
        query: Search query string.

    Returns:
        List of matching item dicts.
    """
    session = _get_session()
    endpoint = f"/me/drive/root/search(q='{query}')"
    params = {
        "$top": 50,
        "$select": "id,name,size,lastModifiedDateTime,parentReference,file,folder,webUrl",
    }

    data = _graph_request("GET", endpoint, session=session, params=params)
    items = data.get("value", [])

    results = []
    for item in items:
        parent = item.get("parentReference", {})
        parent_path = parent.get("path", "").replace("/drive/root:", "", 1)
        is_dir = "folder" in item

        results.append({
            "name": item.get("name", ""),
            "id": item.get("id", ""),
            "path": f"{parent_path}/{item.get('name', '')}",
            "is_dir": is_dir,
            "size": item.get("size", 0),
            "modified": item.get("lastModifiedDateTime", ""),
            "mime_type": item.get("file", {}).get("mimeType", "") if not is_dir else "",
            "web_url": item.get("webUrl", ""),
        })

    return results


# ---------------------------------------------------------------------------
# Excel (Workbooks) helpers
# ---------------------------------------------------------------------------

def excel_list_worksheets(file_path: str) -> list[dict]:
    """List worksheets in an Excel workbook stored in OneDrive.

    Args:
        file_path: OneDrive path to the .xlsx file (e.g. "/Documents/budget.xlsx").

    Returns:
        List of worksheet dicts with id, name, position, visibility.
    """
    session = _get_session()
    clean = file_path.strip("/")
    endpoint = f"/me/drive/root:/{clean}:/workbook/worksheets"

    data = _graph_request("GET", endpoint, session=session)
    sheets = data.get("value", [])

    return [
        {
            "id": s.get("id", ""),
            "name": s.get("name", ""),
            "position": s.get("position", 0),
            "visibility": s.get("visibility", ""),
        }
        for s in sheets
    ]


def excel_read_range(file_path: str, worksheet: str, cell_range: str) -> dict:
    """Read a range of cells from an Excel workbook in OneDrive.

    Args:
        file_path: OneDrive path to the .xlsx file.
        worksheet: Worksheet name (e.g. "Sheet1").
        cell_range: Cell range in A1 notation (e.g. "A1:D10").

    Returns:
        Dict with values (2D array), row/column counts, and cell addresses.
    """
    session = _get_session()
    clean = file_path.strip("/")
    endpoint = f"/me/drive/root:/{clean}:/workbook/worksheets/{worksheet}/range(address='{cell_range}')"

    data = _graph_request("GET", endpoint, session=session)

    return {
        "address": data.get("address", ""),
        "row_count": data.get("rowCount", 0),
        "column_count": data.get("columnCount", 0),
        "values": data.get("values", []),
        "formulas": data.get("formulas", []),
    }


def excel_write_range(file_path: str, worksheet: str, cell_range: str, values: list[list]) -> dict:
    """Write values to a range of cells in an Excel workbook in OneDrive.

    Args:
        file_path: OneDrive path to the .xlsx file.
        worksheet: Worksheet name.
        cell_range: Cell range in A1 notation (e.g. "A1:C3").
        values: 2D array of values to write (rows × columns).

    Returns:
        Dict with updated address and dimensions.
    """
    session = _get_session()
    clean = file_path.strip("/")
    endpoint = f"/me/drive/root:/{clean}:/workbook/worksheets/{worksheet}/range(address='{cell_range}')"

    data = _graph_request(
        "PATCH", endpoint, session=session,
        json_body={"values": values},
    )

    return {
        "address": data.get("address", ""),
        "row_count": data.get("rowCount", 0),
        "column_count": data.get("columnCount", 0),
        "status": "written",
    }


# ---------------------------------------------------------------------------
# Teams helpers
# ---------------------------------------------------------------------------

def teams_list_teams() -> list[dict]:
    """List teams the signed-in user has joined.

    Returns:
        List of team dicts with id, displayName, description.
    """
    session = _get_session()
    data = _graph_request("GET", "/me/joinedTeams", session=session)
    teams = data.get("value", [])

    return [
        {
            "id": t.get("id", ""),
            "name": t.get("displayName", ""),
            "description": t.get("description", ""),
        }
        for t in teams
    ]


def teams_list_channels(team_id: str) -> list[dict]:
    """List channels in a team.

    Args:
        team_id: The team ID.

    Returns:
        List of channel dicts.
    """
    session = _get_session()
    data = _graph_request("GET", f"/teams/{team_id}/channels", session=session)
    channels = data.get("value", [])

    return [
        {
            "id": ch.get("id", ""),
            "name": ch.get("displayName", ""),
            "description": ch.get("description", ""),
            "membership_type": ch.get("membershipType", ""),
        }
        for ch in channels
    ]


def teams_read_messages(team_id: str, channel_id: str, max_results: int = 20) -> list[dict]:
    """Read recent messages from a Teams channel.

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        max_results: Maximum messages to return.

    Returns:
        List of message dicts (newest first).
    """
    session = _get_session()
    # NOTE: $orderby not supported on channel messages endpoint
    params = {"$top": min(max_results, 50)}
    data = _graph_request(
        "GET", f"/teams/{team_id}/channels/{channel_id}/messages",
        session=session, params=params,
    )
    messages = data.get("value", [])

    output = [_format_teams_message(m) for m in messages]
    output.sort(key=lambda m: m.get("date", ""), reverse=True)
    return output


def teams_send_message(team_id: str, channel_id: str, content: str) -> dict:
    """Send a message to a Teams channel.

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        content: Message content (HTML supported).

    Returns:
        Dict with message id and timestamp.
    """
    session = _get_session()
    body = {
        "body": {"contentType": "html", "content": content},
    }

    result = _graph_request(
        "POST", f"/teams/{team_id}/channels/{channel_id}/messages",
        session=session, json_body=body,
    )

    return {
        "id": result.get("id", ""),
        "created": result.get("createdDateTime", ""),
        "status": "sent",
    }


def teams_list_chats(max_results: int = 20) -> list[dict]:
    """List the user's recent chats (1:1 and group).

    Args:
        max_results: Maximum chats to return.

    Returns:
        List of chat dicts.
    """
    session = _get_session()
    params = {
        "$top": min(max_results, 50),
        "$expand": "members",
    }
    data = _graph_request("GET", "/me/chats", session=session, params=params)
    chats = data.get("value", [])

    output = []
    for chat in chats:
        members = [
            m.get("displayName", m.get("email", ""))
            for m in chat.get("members", [])
        ]
        output.append({
            "id": chat.get("id", ""),
            "topic": chat.get("topic", "") or "(no topic)",
            "chat_type": chat.get("chatType", ""),
            "members": members,
            "last_updated": chat.get("lastUpdatedDateTime", ""),
        })

    return output


def teams_read_chat(chat_id: str, max_results: int = 20) -> list[dict]:
    """Read recent messages from a chat.

    Args:
        chat_id: The chat ID.
        max_results: Maximum messages to return.

    Returns:
        List of message dicts (newest first).
    """
    session = _get_session()
    # NOTE: $orderby not supported on chat messages endpoint
    params = {"$top": min(max_results, 50)}
    data = _graph_request(
        "GET", f"/me/chats/{chat_id}/messages",
        session=session, params=params,
    )
    messages = data.get("value", [])

    output = [_format_teams_message(m) for m in messages]
    output.sort(key=lambda m: m.get("date", ""), reverse=True)
    return output


def teams_send_chat(chat_id: str, content: str) -> dict:
    """Send a message in a chat.

    Args:
        chat_id: The chat ID.
        content: Message content (HTML supported).

    Returns:
        Dict with message id and timestamp.
    """
    session = _get_session()
    body = {
        "body": {"contentType": "html", "content": content},
    }

    result = _graph_request(
        "POST", f"/me/chats/{chat_id}/messages",
        session=session, json_body=body,
    )

    return {
        "id": result.get("id", ""),
        "created": result.get("createdDateTime", ""),
        "status": "sent",
    }


def _format_teams_message(msg: dict) -> dict:
    """Format a Teams message into a clean dict."""
    sender = msg.get("from", {})
    user = sender.get("user", {}) if sender else {}
    body = msg.get("body", {}).get("content", "")
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + "\n\n[... truncated ...]"

    attachments = msg.get("attachments", [])
    has_images = bool(msg.get("hostedContents", []))
    # Also detect inline images in HTML body
    if not has_images and "<img" in body:
        has_images = True

    return {
        "id": msg.get("id", ""),
        "from": user.get("displayName", ""),
        "from_email": user.get("email", ""),
        "from_user_id": user.get("id", ""),
        "date": msg.get("createdDateTime", ""),
        "body": body,
        "message_type": msg.get("messageType", ""),
        "importance": msg.get("importance", ""),
        "has_attachments": bool(attachments),
        "has_images": has_images,
        "attachment_count": len(attachments),
    }


def teams_reply_channel_message(
    team_id: str,
    channel_id: str,
    message_id: str,
    content: str,
) -> dict:
    """Reply to a message in a Teams channel (threaded reply).

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        message_id: The parent message ID to reply to.
        content: Reply content (HTML supported).

    Returns:
        Dict with reply message id and timestamp.
    """
    session = _get_session()
    body = {
        "body": {"contentType": "html", "content": content},
    }

    result = _graph_request(
        "POST",
        f"/teams/{team_id}/channels/{channel_id}/messages/{message_id}/replies",
        session=session,
        json_body=body,
    )

    return {
        "id": result.get("id", ""),
        "created": result.get("createdDateTime", ""),
        "reply_to": message_id,
        "status": "sent",
    }


def teams_list_chat_members(chat_id: str) -> list[dict]:
    """List members of a chat.

    Args:
        chat_id: The chat ID.

    Returns:
        List of member dicts with userId, displayName, email, roles.
    """
    session = _get_session()
    data = _graph_request("GET", f"/me/chats/{chat_id}/members", session=session)
    members = data.get("value", [])

    return [
        {
            "user_id": m.get("userId", ""),
            "display_name": m.get("displayName", ""),
            "email": m.get("email", ""),
            "roles": m.get("roles", []),
        }
        for m in members
    ]


def teams_create_chat(
    member_emails: list[str],
    topic: str = "",
    message: str = "",
) -> dict:
    """Create a new 1:1 or group chat.

    Args:
        member_emails: List of email addresses to include. 1 = 1:1 chat, 2+ = group.
        topic: Chat topic (group chats only).
        message: Optional first message to send.

    Returns:
        Dict with chat id, type, and members.
    """
    session = _get_session()

    # Resolve emails to user IDs
    members_payload = [
        {
            "@odata.type": "#microsoft.graph.aadUserConversationMember",
            "roles": ["owner"],
            "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{email}')",
        }
        for email in member_emails
    ]
    # Add self as owner
    me = _graph_request("GET", "/me", session=session, params={"$select": "id"})
    members_payload.insert(0, {
        "@odata.type": "#microsoft.graph.aadUserConversationMember",
        "roles": ["owner"],
        "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{me['id']}')",
    })

    chat_type = "oneOnOne" if len(member_emails) == 1 else "group"
    body: dict = {
        "chatType": chat_type,
        "members": members_payload,
    }
    if topic and chat_type == "group":
        body["topic"] = topic

    result = _graph_request("POST", "/chats", session=session, json_body=body)
    chat_id = result.get("id", "")

    # Send first message if provided
    if message and chat_id:
        teams_send_chat(chat_id, message)

    return {
        "id": chat_id,
        "chat_type": chat_type,
        "topic": topic,
        "member_count": len(member_emails) + 1,
        "status": "created",
    }


def teams_lookup_user(email: str) -> dict | None:
    """Look up a Microsoft 365 user by email.

    Args:
        email: Email address to search for.

    Returns:
        User dict with id, displayName, email, or None if not found.
    """
    session = _get_session()
    try:
        data = _graph_request(
            "GET", f"/users/{email}",
            session=session,
            params={"$select": "id,displayName,mail,userPrincipalName"},
        )
        return {
            "user_id": data.get("id", ""),
            "display_name": data.get("displayName", ""),
            "email": data.get("mail", "") or data.get("userPrincipalName", ""),
        }
    except RuntimeError:
        return None


def teams_add_reaction(
    chat_id: str,
    message_id: str,
    reaction: str,
) -> dict:
    """Add a reaction to a chat message.

    Args:
        chat_id: The chat ID.
        message_id: The message ID.
        reaction: Reaction type (like, angry, sad, laugh, heart, surprised).

    Returns:
        Confirmation dict.
    """
    session = _get_session()
    body = {"reactionType": reaction}

    _graph_request(
        "POST",
        f"/me/chats/{chat_id}/messages/{message_id}/setReaction",
        session=session,
        json_body=body,
    )

    return {"ok": True, "message_id": message_id, "reaction": reaction}


def teams_remove_reaction(
    chat_id: str,
    message_id: str,
    reaction: str,
) -> dict:
    """Remove a reaction from a chat message.

    Args:
        chat_id: The chat ID.
        message_id: The message ID.
        reaction: Reaction type to remove.

    Returns:
        Confirmation dict.
    """
    session = _get_session()
    body = {"reactionType": reaction}

    _graph_request(
        "POST",
        f"/me/chats/{chat_id}/messages/{message_id}/unsetReaction",
        session=session,
        json_body=body,
    )

    return {"ok": True, "message_id": message_id, "reaction": reaction, "removed": True}


def teams_download_chat_images(
    chat_id: str,
    local_dir: str = "/tmp/teams-images/",
    max_messages: int = 50,
) -> dict:
    """Download all images from a Teams chat.

    Scans recent messages for inline images (hostedContents) and downloads them.

    Args:
        chat_id: The chat ID.
        local_dir: Local directory to save images into.
        max_messages: Maximum messages to scan.

    Returns:
        Dict with downloaded file paths and count.
    """
    import re

    session = _get_session()
    os.makedirs(local_dir, exist_ok=True)

    # Fetch messages
    params = {"$top": min(max_messages, 50)}
    data = _graph_request(
        "GET", f"/me/chats/{chat_id}/messages",
        session=session, params=params,
    )
    messages = data.get("value", [])

    downloaded = []

    for msg in messages:
        msg_id = msg.get("id", "")
        body = msg.get("body", {}).get("content", "")
        sender = (msg.get("from") or {}).get("user", {}).get("displayName", "unknown")
        date = msg.get("createdDateTime", "")[:10]

        # Pattern 1: hostedContents referenced in img tags
        # e.g. <img src="https://graph.microsoft.com/v1.0/.../hostedContents/aWQ9.../$value"
        hosted_pattern = re.findall(
            r'https://graph\.microsoft\.com/[^"]*hostedContents/([^/"]+)/\$value',
            body,
        )
        for i, content_id in enumerate(hosted_pattern):
            url = f"{GRAPH_BASE}/me/chats/{chat_id}/messages/{msg_id}/hostedContents/{content_id}/$value"
            resp = session.get(url, timeout=30)
            if resp.status_code < 400:
                # Determine extension from content-type
                content_type = resp.headers.get("Content-Type", "image/png")
                ext = content_type.split("/")[-1].split(";")[0]
                if ext not in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
                    ext = "png"
                filename = f"{date}_{sender}_{msg_id[-8:]}_{i}.{ext}"
                filepath = os.path.join(local_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                downloaded.append({
                    "path": filepath,
                    "size": len(resp.content),
                    "sender": sender,
                    "date": date,
                    "message_id": msg_id,
                })

        # Pattern 2: Direct Graph API image URLs in img tags (not hostedContents)
        direct_pattern = re.findall(
            r'src="(https://graph\.microsoft\.com/[^"]*\$value)"',
            body,
        )
        for i, url in enumerate(direct_pattern):
            if "hostedContents" in url:
                continue  # Already handled above
            resp = session.get(url, timeout=30)
            if resp.status_code < 400:
                content_type = resp.headers.get("Content-Type", "image/png")
                ext = content_type.split("/")[-1].split(";")[0]
                if ext not in ("png", "jpg", "jpeg", "gif", "webp", "bmp"):
                    ext = "png"
                filename = f"{date}_{sender}_{msg_id[-8:]}_d{i}.{ext}"
                filepath = os.path.join(local_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                downloaded.append({
                    "path": filepath,
                    "size": len(resp.content),
                    "sender": sender,
                    "date": date,
                    "message_id": msg_id,
                })

        # Pattern 3: File attachments (SharePoint/OneDrive references)
        for att in msg.get("attachments", []):
            content_url = att.get("contentUrl", "")
            att_name = att.get("name", "")
            if not content_url or not att_name:
                continue
            # SharePoint/OneDrive file references
            if att.get("contentType") == "reference" or "sharepoint.com" in content_url:
                file_resp = _download_sharepoint_url(session, content_url)
                if file_resp:
                    # Sanitize filename
                    safe_name = re.sub(r'[^\w\-.]', '_', att_name)
                    filename = f"{date}_{sender}_{safe_name}"
                    filepath = os.path.join(local_dir, filename)
                    with open(filepath, "wb") as f:
                        f.write(file_resp)
                    downloaded.append({
                        "path": filepath,
                        "size": len(file_resp),
                        "sender": sender,
                        "date": date,
                        "message_id": msg_id,
                        "original_name": att_name,
                        "source": "attachment",
                    })

    return {
        "chat_id": chat_id,
        "messages_scanned": len(messages),
        "images_downloaded": len(downloaded),
        "local_dir": local_dir,
        "files": downloaded,
    }


def _download_sharepoint_url(session, url: str) -> bytes | None:
    """Download a file from a SharePoint/OneDrive URL using Graph API.

    Handles both direct SharePoint URLs and drive item URLs.
    """
    # Try to convert SharePoint URL to Graph API download URL
    try:
        # Use the shares API to resolve any SharePoint URL to a driveItem
        import base64
        encoded = base64.urlsafe_b64encode(url.encode()).decode().rstrip("=")
        sharing_url = f"u!{encoded}"
        meta = _graph_request(
            "GET",
            f"/shares/{sharing_url}/driveItem",
            session=session,
            params={"$select": "id,@microsoft.graph.downloadUrl,size"},
        )

        # Check size
        file_size = meta.get("size", 0)
        if file_size > MAX_DOWNLOAD_BYTES:
            logger.warning("SharePoint file too large (%d bytes), skipping", file_size)
            return None

        # Use the direct download URL if available
        download_url = meta.get("@microsoft.graph.downloadUrl", "")
        if download_url:
            resp = session.get(download_url, timeout=120)
            if resp.status_code < 400:
                return resp.content

        # Fallback: download via content endpoint
        item_id = meta.get("id", "")
        if item_id:
            parent = meta.get("parentReference", {})
            drive_id = parent.get("driveId", "")
            if drive_id:
                content_url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"
                resp = session.get(content_url, timeout=120)
                if resp.status_code < 400:
                    return resp.content
    except Exception as e:
        logger.warning("Failed to download SharePoint URL %s: %s", url[:100], e)

    return None


def onedrive_upload_large(local_path: str, remote_path: str) -> dict:
    """Upload a large file to OneDrive using resumable upload session (up to 250MB).

    Args:
        local_path: Local file path to upload.
        remote_path: OneDrive destination path (e.g. "/Documents/large-report.pptx").

    Returns:
        Dict with upload details.
    """
    path_obj = Path(local_path)
    if not path_obj.exists():
        return {"error": f"Local file not found: {local_path}"}

    file_size = path_obj.stat().st_size
    max_size = 250 * 1024 * 1024  # 250 MB
    if file_size > max_size:
        return {"error": f"File too large ({file_size / 1024 / 1024:.0f} MB). Max upload size is 250 MB."}

    session = _get_session()
    clean = remote_path.strip("/")

    # Create upload session
    create_url = f"{GRAPH_BASE}/me/drive/root:/{clean}:/createUploadSession"
    create_resp = session.post(create_url, json={"item": {"name": path_obj.name}}, timeout=30)
    if create_resp.status_code >= 400:
        return {"error": f"Failed to create upload session: HTTP {create_resp.status_code}"}

    upload_url = create_resp.json().get("uploadUrl", "")
    if not upload_url:
        return {"error": "No upload URL returned"}

    # Upload in chunks (10MB each)
    chunk_size = 10 * 1024 * 1024
    uploaded = 0
    result = None

    with open(local_path, "rb") as f:
        while uploaded < file_size:
            chunk = f.read(chunk_size)
            chunk_len = len(chunk)
            end = uploaded + chunk_len - 1

            headers = {
                "Content-Length": str(chunk_len),
                "Content-Range": f"bytes {uploaded}-{end}/{file_size}",
            }

            resp = session.put(upload_url, data=chunk, headers=headers, timeout=120)

            if resp.status_code in (200, 201):
                # Upload complete
                result = resp.json()
                break
            elif resp.status_code == 202:
                # Chunk accepted, continue
                uploaded += chunk_len
            else:
                return {"error": f"Upload failed at byte {uploaded}: HTTP {resp.status_code}"}

    if not result:
        return {"error": "Upload did not complete"}

    return {
        "local_path": local_path,
        "remote_path": remote_path,
        "id": result.get("id", ""),
        "size": result.get("size", 0),
        "web_url": result.get("webUrl", ""),
        "status": "uploaded",
    }


def teams_list_channel_files(
    team_id: str,
    channel_id: str,
    path: str = "/",
    max_results: int = 100,
) -> list[dict]:
    """List files in a Teams channel's SharePoint folder.

    Args:
        team_id: The team ID.
        channel_id: The channel ID.
        path: Sub-path within the channel folder (default: root).
        max_results: Maximum items to return.

    Returns:
        List of file/folder dicts.
    """
    session = _get_session()

    # Get the channel's filesFolder driveItem
    folder_data = _graph_request(
        "GET",
        f"/teams/{team_id}/channels/{channel_id}/filesFolder",
        session=session,
    )

    drive_id = folder_data.get("parentReference", {}).get("driveId", "")
    folder_id = folder_data.get("id", "")

    if not drive_id or not folder_id:
        return []

    # List children
    if path == "/" or not path:
        endpoint = f"/drives/{drive_id}/items/{folder_id}/children"
    else:
        clean = path.strip("/")
        # Navigate relative to the channel folder
        endpoint = f"/drives/{drive_id}/items/{folder_id}:/{clean}:/children"

    params = {
        "$top": min(max_results, 200),
        "$select": "id,name,size,lastModifiedDateTime,folder,file,webUrl,@microsoft.graph.downloadUrl",
    }

    data = _graph_request("GET", endpoint, session=session, params=params)
    items = data.get("value", [])

    entries = []
    for item in items:
        is_dir = "folder" in item
        entries.append({
            "name": item.get("name", ""),
            "id": item.get("id", ""),
            "drive_id": drive_id,
            "is_dir": is_dir,
            "size": item.get("size", 0),
            "modified": item.get("lastModifiedDateTime", ""),
            "mime_type": item.get("file", {}).get("mimeType", "") if not is_dir else "",
            "web_url": item.get("webUrl", ""),
        })

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def teams_download_channel_file(
    drive_id: str,
    item_id: str,
    local_dir: str = "/tmp/teams-files/",
) -> dict:
    """Download a file from a Teams channel's SharePoint folder.

    Args:
        drive_id: The drive ID (from teams_list_channel_files).
        item_id: The item ID (from teams_list_channel_files).
        local_dir: Local directory to save into.

    Returns:
        Dict with download details.
    """
    session = _get_session()
    os.makedirs(local_dir, exist_ok=True)

    # Get item metadata with download URL
    data = _graph_request(
        "GET",
        f"/drives/{drive_id}/items/{item_id}",
        session=session,
        params={"$select": "id,name,size,@microsoft.graph.downloadUrl"},
    )

    name = data.get("name", "file")
    file_size = data.get("size", 0)

    if file_size > MAX_DOWNLOAD_BYTES:
        return {"error": f"File too large ({file_size / 1024 / 1024:.0f} MB). Max is 500 MB."}

    download_url = data.get("@microsoft.graph.downloadUrl", "")
    if not download_url:
        # Fallback to content endpoint
        download_url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item_id}/content"

    resp = session.get(download_url, timeout=120, stream=True)
    if resp.status_code >= 400:
        return {"error": f"Download failed: HTTP {resp.status_code}"}

    local_path = os.path.join(local_dir, name)
    downloaded = 0
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded > MAX_DOWNLOAD_BYTES:
                f.close()
                os.remove(local_path)
                return {"error": "Download aborted - exceeded 500 MB safety cap."}
            f.write(chunk)

    return {
        "item_id": item_id,
        "name": name,
        "local_path": local_path,
        "size": os.path.getsize(local_path),
        "status": "downloaded",
    }


# ---------------------------------------------------------------------------
# SharePoint helpers
# ---------------------------------------------------------------------------

def sharepoint_list_sites(query: str = "") -> list[dict]:
    """List or search SharePoint sites.

    Args:
        query: Search query (empty = list followed sites).

    Returns:
        List of site dicts.
    """
    session = _get_session()

    if query:
        params = {"search": query}
        data = _graph_request("GET", "/sites", session=session, params=params)
    else:
        data = _graph_request("GET", "/me/followedSites", session=session)

    sites = data.get("value", [])

    return [
        {
            "id": s.get("id", ""),
            "name": s.get("displayName", s.get("name", "")),
            "description": s.get("description", ""),
            "web_url": s.get("webUrl", ""),
        }
        for s in sites
    ]


def sharepoint_list_files(site_id: str, path: str = "/", max_results: int = 100) -> list[dict]:
    """List files in a SharePoint site's default document library.

    Args:
        site_id: The SharePoint site ID.
        path: Path within the document library (e.g. "/" for root, "/Reports").
        max_results: Maximum items to return.

    Returns:
        List of item dicts.
    """
    session = _get_session()

    if path == "/" or not path:
        endpoint = f"/sites/{site_id}/drive/root/children"
    else:
        clean = path.strip("/")
        endpoint = f"/sites/{site_id}/drive/root:/{clean}:/children"

    params = {
        "$top": min(max_results, 200),
        "$select": "id,name,size,lastModifiedDateTime,folder,file,webUrl",
    }

    data = _graph_request("GET", endpoint, session=session, params=params)
    items = data.get("value", [])

    entries = []
    for item in items:
        is_dir = "folder" in item
        entries.append({
            "name": item.get("name", ""),
            "id": item.get("id", ""),
            "is_dir": is_dir,
            "size": item.get("size", 0),
            "modified": item.get("lastModifiedDateTime", ""),
            "mime_type": item.get("file", {}).get("mimeType", "") if not is_dir else "",
            "web_url": item.get("webUrl", ""),
        })

    entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    return entries


def sharepoint_download(site_id: str, file_path: str, local_dir: str = "/tmp/sharepoint-download/") -> dict:
    """Download a file from a SharePoint site's document library.

    Args:
        site_id: The SharePoint site ID.
        file_path: Path within the document library (e.g. "/Reports/Q4.xlsx").
        local_dir: Local directory to save into.

    Returns:
        Dict with download details.
    """
    session = _get_session()
    clean = file_path.strip("/")

    # Pre-flight size check via metadata
    meta_url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{clean}"
    meta_resp = session.get(meta_url, timeout=30)
    if meta_resp.status_code < 400:
        file_size = meta_resp.json().get("size", 0)
        if file_size > MAX_DOWNLOAD_BYTES:
            size_mb = file_size / (1024 * 1024)
            return {"error": f"File too large ({size_mb:.0f} MB). Max download size is 500 MB."}

    endpoint = f"/sites/{site_id}/drive/root:/{clean}:/content"
    url = f"{GRAPH_BASE}{endpoint}"
    resp = session.get(url, timeout=120, stream=True)

    if resp.status_code >= 400:
        return {"error": f"Download failed: HTTP {resp.status_code}"}

    os.makedirs(local_dir, exist_ok=True)
    filename = clean.split("/")[-1]
    local_path = os.path.join(local_dir, filename)

    downloaded = 0
    with open(local_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            downloaded += len(chunk)
            if downloaded > MAX_DOWNLOAD_BYTES:
                f.close()
                os.remove(local_path)
                return {"error": "Download aborted — exceeded 500 MB safety cap."}
            f.write(chunk)

    return {
        "site_id": site_id,
        "remote_path": file_path,
        "local_path": local_path,
        "size": os.path.getsize(local_path),
        "status": "downloaded",
    }


def sharepoint_upload(site_id: str, local_path: str, remote_path: str) -> dict:
    """Upload a file to a SharePoint site's document library (simple upload, <4MB).

    Args:
        site_id: The SharePoint site ID.
        local_path: Local file path to upload.
        remote_path: Path within the document library (e.g. "/Reports/Q4.xlsx").

    Returns:
        Dict with upload details.
    """
    path_obj = Path(local_path)
    if not path_obj.exists():
        return {"error": f"Local file not found: {local_path}"}

    file_size = path_obj.stat().st_size
    if file_size > 4 * 1024 * 1024:
        return {"error": f"File too large ({file_size} bytes). Simple upload supports <4MB."}

    session = _get_session()
    clean = remote_path.strip("/")
    endpoint = f"/sites/{site_id}/drive/root:/{clean}:/content"

    url = f"{GRAPH_BASE}{endpoint}"
    with open(local_path, "rb") as f:
        resp = session.put(
            url,
            data=f,
            headers={"Content-Type": "application/octet-stream"},
            timeout=60,
        )

    if resp.status_code >= 400:
        return {"error": f"Upload failed: HTTP {resp.status_code} — {resp.text[:300]}"}

    result = resp.json()
    return {
        "site_id": site_id,
        "local_path": local_path,
        "remote_path": remote_path,
        "id": result.get("id", ""),
        "size": result.get("size", 0),
        "web_url": result.get("webUrl", ""),
        "status": "uploaded",
    }
