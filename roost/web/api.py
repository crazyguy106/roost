"""REST API endpoints for tasks, projects, triage, calendar, and sharing."""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from roost.web.rate_limit import limiter
from roost.models import (
    TaskCreate, TaskUpdate, ProjectCreate, ProjectUpdate,
    NoteCreate, CurriculumDocCreate,
    UserCreate, ShareLinkCreate,
    ContactCreate, ContactUpdate,
    ProjectAssignmentCreate, TaskAssignmentCreate,
    EntityCreate, EntityUpdate, ContactEntityCreate,
    CommunicationCreate,
)
from roost import task_service
from roost.web.permissions import (
    is_admin_or_owner, can_access_project, can_edit_project, can_edit_task,
)

router = APIRouter(prefix="/api")


def _api_visibility_user_id(request: Request) -> int | None:
    """Return user_id for visibility filtering, or None for admin/owner."""
    user = getattr(request.state, "current_user", None)
    if not user or is_admin_or_owner(user):
        return None
    return user.get("user_id")


def _api_current_user(request: Request) -> dict | None:
    """Return current user dict from request state."""
    return getattr(request.state, "current_user", None)


def _api_user_id(request: Request) -> int | None:
    """Return user_id from session for data scoping. None if no session user."""
    user = _api_current_user(request)
    if user:
        return user.get("user_id")
    return None


# ── Tasks ────────────────────────────────────────────────────────────

@router.get("/tasks")
def api_list_tasks(request: Request,
                   status: str | None = None, project: str | None = None,
                   priority: str | None = None, deadline_filter: str | None = None,
                   order_by: str | None = None, limit: int | None = None,
                   include_someday: bool = False, focus_only: bool = False,
                   effort_estimate: str | None = None,
                   assigned_to: int | None = None):
    vis_uid = _api_visibility_user_id(request)
    return task_service.list_tasks(
        status=status, project=project, priority=priority,
        deadline_filter=deadline_filter, order_by=order_by, limit=limit,
        include_someday=include_someday, focus_only=focus_only,
        effort_estimate=effort_estimate, assigned_to=assigned_to,
        visible_to_user_id=vis_uid,
    )


@router.post("/tasks", status_code=201)
@limiter.limit("30/minute")
def api_create_task(request: Request, data: TaskCreate):
    user = _api_current_user(request)
    if user and data.project_id and not can_edit_task(user, data.project_id):
        raise HTTPException(403, "No write access to this project")
    return task_service.create_task(data, source="web", user_id=_api_user_id(request))


@router.get("/tasks/{task_id}")
def api_get_task(task_id: int):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.put("/tasks/{task_id}")
@limiter.limit("30/minute")
def api_update_task(request: Request, task_id: int, data: TaskUpdate):
    user = _api_current_user(request)
    existing = task_service.get_task(task_id)
    if not existing:
        raise HTTPException(404, "Task not found")
    if user and not can_edit_task(user, existing.project_id):
        raise HTTPException(403, "No write access to this task")
    task = task_service.update_task(task_id, data, source="web")
    return task


@router.post("/tasks/{task_id}/done")
def api_complete_task(task_id: int):
    task = task_service.complete_task(task_id, source="web")
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.post("/tasks/{task_id}/wip")
def api_mark_wip(task_id: int):
    """Mark task as work-in-progress and redirect back."""
    task = task_service.mark_wip(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.delete("/tasks/{task_id}")
def api_delete_task(request: Request, task_id: int):
    user = _api_current_user(request)
    existing = task_service.get_task(task_id)
    if not existing:
        raise HTTPException(404, "Task not found")
    if user and not can_edit_task(user, existing.project_id):
        raise HTTPException(403, "No write access to this task")
    task_service.delete_task(task_id)
    return {"ok": True}


# ── Shelve / Unshelve ───────────────────────────────────────────────

@router.post("/tasks/{task_id}/shelve")
def api_shelve_task(task_id: int):
    task = task_service.shelve_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


@router.post("/tasks/{task_id}/unshelve")
def api_unshelve_task(task_id: int):
    task = task_service.unshelve_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task


# ── Focus Mode ─────────────────────────────────────────────────────

@router.get("/focus")
def api_get_focus(request: Request):
    uid = _api_user_id(request)
    tasks = task_service.get_focus_tasks(user_id=uid)
    suggestions = task_service.suggest_focus(user_id=uid) if not tasks else []
    return {"focus": [t.model_dump() for t in tasks], "suggestions": [t.model_dump() for t in suggestions]}


@router.post("/focus/{task_id}")
def api_set_focus(task_id: int):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    result = task_service.set_focus(task_id)
    if not result["ok"]:
        raise HTTPException(400, result["message"])
    return result


@router.delete("/focus/{task_id}")
def api_clear_focus(task_id: int):
    count = task_service.clear_focus(task_id)
    return {"ok": True, "cleared": count}


# ── Energy Mode ────────────────────────────────────────────────────

@router.post("/energy-mode")
def api_set_energy_mode(level: str = "low"):
    if level not in ("low", "medium", "high"):
        raise HTTPException(400, "level must be low, medium, or high")
    task_service.set_energy_mode(level)
    tasks = task_service.list_matching_effort_tasks(level)
    return {"mode": level, "matching_tasks": len(tasks)}


@router.get("/energy-mode")
def api_get_energy_mode():
    mode = task_service.get_energy_mode()
    return {"mode": mode}


# ── Shutdown Protocol ──────────────────────────────────────────────

@router.post("/shutdown")
def api_shutdown(request: Request):
    uid = _api_user_id(request)
    if task_service.is_shutdown_active():
        return {"ok": False, "message": "Shutdown already active"}
    result = task_service.execute_shutdown(user_id=uid)
    return {"ok": True, **result}


@router.post("/resume-day")
def api_resume_day(request: Request):
    uid = _api_user_id(request)
    if not task_service.is_shutdown_active():
        return {"ok": False, "message": "No active shutdown"}
    result = task_service.execute_resume(user_id=uid)
    return {"ok": True, **result}


# ── Triage / Today ──────────────────────────────────────────────────

@router.get("/today")
def api_today(request: Request):
    """Today's focus: overdue + due today + in progress + top urgent."""
    from roost.triage import get_today_tasks
    return get_today_tasks(user_id=_api_user_id(request))


@router.get("/urgent")
def api_urgent(limit: int = 10):
    """Top N tasks by urgency score."""
    tasks = task_service.list_tasks(
        order_by="urgency", limit=limit, exclude_paused_projects=True,
    )
    return [t for t in tasks if t.status.value != "done"]


# ── Projects ─────────────────────────────────────────────────────────

@router.get("/projects")
def api_list_projects(
    request: Request,
    status: str | None = None,
    category: str | None = None,
    project_type: str | None = None,
    parent_id: int | None = None,
    top_level_only: bool = False,
    entity_id: int | None = None,
):
    vis_uid = _api_visibility_user_id(request)
    return task_service.list_projects(
        status=status, category=category,
        project_type=project_type, parent_id=parent_id,
        top_level_only=top_level_only, entity_id=entity_id,
        visible_to_user_id=vis_uid,
    )


@router.post("/projects", status_code=201)
@limiter.limit("20/minute")
def api_create_project(request: Request, data: ProjectCreate):
    return task_service.create_project(data, user_id=_api_user_id(request))


@router.get("/projects/{project_id}")
def api_get_project(project_id: int):
    project = task_service.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.put("/projects/{project_id}")
def api_update_project(request: Request, project_id: int, data: ProjectUpdate):
    user = _api_current_user(request)
    if user and not can_edit_project(user, project_id):
        raise HTTPException(403, "No write access to this project")
    project = task_service.update_project(project_id, data)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.delete("/projects/{project_id}")
def api_delete_project(request: Request, project_id: int):
    user = _api_current_user(request)
    if user and not can_edit_project(user, project_id):
        raise HTTPException(403, "No write access to this project")
    if not task_service.delete_project(project_id):
        raise HTTPException(404, "Project not found")
    return {"ok": True}


# ── Notes ────────────────────────────────────────────────────────────

@router.get("/notes")
def api_list_notes(request: Request, tag: str | None = None, limit: int = 50):
    return task_service.list_notes(tag=tag, limit=limit, user_id=_api_user_id(request))


@router.post("/notes", status_code=201)
def api_create_note(request: Request, data: NoteCreate):
    return task_service.create_note(data, user_id=_api_user_id(request))


@router.delete("/notes/{note_id}")
def api_delete_note(request: Request, note_id: int):
    if not task_service.delete_note(note_id, user_id=_api_user_id(request)):
        raise HTTPException(404, "Note not found")
    return {"ok": True}


# ── Command Log ──────────────────────────────────────────────────────

@router.get("/log")
def api_get_log(limit: int = 50):
    return task_service.get_command_log(limit=limit)


# ── Subtasks ────────────────────────────────────────────────────────

@router.get("/tasks/{task_id}/subtasks")
def api_list_subtasks(task_id: int):
    parent = task_service.get_task(task_id)
    if not parent:
        raise HTTPException(404, "Task not found")
    return task_service.list_subtasks(task_id)


# ── Progress ────────────────────────────────────────────────────────

@router.get("/progress")
def api_progress(project: str | None = None):
    return task_service.get_progress(project=project)


# ── Curriculum Docs ─────────────────────────────────────────────────

@router.get("/curriculum-docs")
def api_list_curriculum_docs(module_id: str | None = None,
                             doc_type: str | None = None,
                             status: str | None = None):
    return task_service.list_curriculum_docs(
        module_id=module_id, doc_type=doc_type, status=status,
    )


@router.post("/curriculum-docs", status_code=201)
def api_create_curriculum_doc(data: CurriculumDocCreate):
    return task_service.create_curriculum_doc(data)


# ── Calendar ────────────────────────────────────────────────────────

@router.get("/calendar")
def api_calendar():
    """Merged today view: calendar events + task triage."""
    from roost.calendar_service import get_merged_today
    return get_merged_today()


@router.get("/calendar/events")
def api_calendar_events(days: int = 7):
    """Calendar events for the next N days."""
    from roost.calendar_service import get_week_events
    events = get_week_events(days=days)
    # Serialize datetime objects
    return [
        {
            "summary": e["summary"],
            "start": e["start"].isoformat() if e.get("start") else None,
            "end": e["end"].isoformat() if e.get("end") else None,
            "location": e.get("location", ""),
            "description": e.get("description", ""),
            "calendar": e.get("calendar", ""),
        }
        for e in events
    ]


@router.get("/calendar/range")
def api_calendar_range(start: str, end: str):
    """Events + task deadlines grouped by date for a date range.

    Query params: start=YYYY-MM-DD&end=YYYY-MM-DD
    Returns: { events: {date: [...]}, deadlines: {date: [...]} }
    """
    from datetime import datetime as dt, date

    try:
        start_date = dt.strptime(start, "%Y-%m-%d").date()
        end_date = dt.strptime(end, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(400, "start and end must be YYYY-MM-DD format")

    # Fetch calendar events
    from roost.calendar_service import fetch_events_for_range
    raw_events = fetch_events_for_range(start_date, end_date)

    events_by_date: dict[str, list] = {}
    for e in raw_events:
        if not e.get("start"):
            continue
        date_key = e["start"].date().isoformat()
        events_by_date.setdefault(date_key, []).append({
            "summary": e["summary"],
            "start": e["start"].isoformat() if e["start"] else None,
            "end": e["end"].isoformat() if e.get("end") else None,
            "location": e.get("location", ""),
            "calendar": e.get("calendar", ""),
            "all_day": e.get("all_day", False),
        })

    # Fetch task deadlines in range
    tasks = task_service.list_tasks(
        deadline_filter="has_deadline", order_by="deadline",
    )
    deadlines_by_date: dict[str, list] = {}
    for t in tasks:
        if not t.deadline:
            continue
        d = t.deadline.date() if hasattr(t.deadline, 'date') else t.deadline
        if isinstance(d, str):
            d = dt.strptime(d[:10], "%Y-%m-%d").date()
        if start_date <= d <= end_date and t.status.value != "done":
            date_key = d.isoformat()
            deadlines_by_date.setdefault(date_key, []).append({
                "id": t.id,
                "title": t.title,
                "deadline": t.deadline.isoformat() if hasattr(t.deadline, 'isoformat') else str(t.deadline),
                "priority": t.priority.value,
                "project_name": t.project_name,
            })

    return {"events": events_by_date, "deadlines": deadlines_by_date}


@router.get("/calendar/export")
def api_calendar_export():
    """Download task deadlines as .ics file."""
    from roost.calendar_service import export_tasks_to_ics
    path = export_tasks_to_ics()
    return FileResponse(
        path,
        media_type="text/calendar",
        filename="task_deadlines.ics",
    )


# ── Sharing ─────────────────────────────────────────────────────────

@router.get("/users")
def api_list_users(request: Request):
    user = _api_current_user(request)
    if not user or not is_admin_or_owner(user):
        raise HTTPException(403, "Admin or owner access required")
    from roost.sharing_service import list_users
    return list_users()


@router.post("/users", status_code=201)
def api_create_user(request: Request, data: UserCreate):
    user = _api_current_user(request)
    if not user or not is_admin_or_owner(user):
        raise HTTPException(403, "Admin or owner access required")
    from roost.sharing_service import create_user
    user = create_user(data)
    if not user:
        raise HTTPException(400, "Failed to create user")
    return user


@router.delete("/users/{user_id}")
def api_delete_user(request: Request, user_id: int):
    caller = _api_current_user(request)
    if not caller or not is_admin_or_owner(caller):
        raise HTTPException(403, "Admin or owner access required")
    from roost.sharing_service import delete_user
    if not delete_user(user_id):
        raise HTTPException(404, "User not found")
    return {"ok": True}


@router.get("/share-links")
def api_list_share_links():
    from roost.sharing_service import list_share_links
    return list_share_links()


@router.post("/share-links", status_code=201)
def api_create_share_link(data: ShareLinkCreate):
    from roost.sharing_service import create_share_link
    return create_share_link(data)


@router.delete("/share-links/{link_id}")
def api_revoke_share_link(link_id: int):
    from roost.sharing_service import revoke_share_link
    if not revoke_share_link(link_id):
        raise HTTPException(404, "Share link not found")
    return {"ok": True}


@router.get("/projects/{project_id}/members")
def api_list_project_members(project_id: int):
    from roost.sharing_service import list_project_members
    return list_project_members(project_id)


@router.post("/projects/{project_id}/members")
def api_add_project_member(project_id: int, user_id: int, role: str = "viewer"):
    from roost.sharing_service import add_project_member
    if not add_project_member(project_id, user_id, role):
        raise HTTPException(400, "Failed to add member")
    return {"ok": True}


# ── Curricula ─────────────────────────────────────────────────────────

@router.get("/curricula")
def api_list_curricula():
    return task_service.list_curricula()


@router.get("/curricula/{curriculum_id}/modules")
def api_list_curriculum_modules(curriculum_id: int):
    curriculum = task_service.get_curriculum(curriculum_id)
    if not curriculum:
        raise HTTPException(404, "Curriculum not found")
    return task_service.list_curriculum_modules(curriculum_id)


@router.post("/curricula/scan")
def api_scan_curricula():
    """Trigger a curriculum directory scan."""
    results = task_service.scan_and_register_curricula()
    return {"scanned": len(results), "results": results}


# ── Notion Admin ──────────────────────────────────────────────────────

@router.post("/notion/sync")
def api_notion_sync():
    """Trigger a manual Notion sync (push pending changes)."""
    try:
        from roost.notion.sync import process_retry_queue
        count = process_retry_queue()
        return {"ok": True, "processed": count}
    except ImportError:
        raise HTTPException(501, "Notion sync not available")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/notion/export")
def api_notion_export():
    """Bulk export all local data to Notion."""
    try:
        from roost.notion.sync import bulk_export_to_notion
        stats = bulk_export_to_notion()
        return {"ok": True, "stats": stats}
    except ImportError:
        raise HTTPException(501, "Notion sync not available")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("/notion/status")
def api_notion_status():
    """Get Notion sync status."""
    try:
        from roost.notion import is_notion_available
        from roost.database import get_connection

        available = is_notion_available()
        conn = get_connection()

        # Pending sync items
        pending = conn.execute(
            "SELECT COUNT(*) as cnt FROM notion_sync_log WHERE status = 'pending'"
        ).fetchone()["cnt"]

        # Last sync times
        states = conn.execute(
            "SELECT table_name, last_synced_at FROM notion_sync_state"
        ).fetchall()
        conn.close()

        return {
            "available": available,
            "pending_pushes": pending,
            "sync_state": {r["table_name"]: r["last_synced_at"] for r in states},
        }
    except ImportError:
        return {"available": False, "pending_pushes": 0, "sync_state": {}}
    except Exception:
        return {"available": False, "pending_pushes": 0, "sync_state": {}}


# ── Gmail ─────────────────────────────────────────────────────────────

@router.get("/gmail/status")
def api_gmail_status():
    """Get Gmail integration status."""
    try:
        from roost.gmail import is_gmail_available
        from roost.gmail.client import get_stored_refresh_token
        return {
            "available": is_gmail_available(),
            "has_token": bool(get_stored_refresh_token()),
        }
    except ImportError:
        return {"available": False, "has_token": False}


@router.post("/gmail/send-digest")
def api_gmail_send_digest():
    """Manually trigger a digest email."""
    try:
        from roost.gmail.service import send_digest
        from roost.config import GMAIL_SEND_FROM
        if not GMAIL_SEND_FROM:
            raise HTTPException(400, "GMAIL_SEND_FROM not configured")
        ok = send_digest(GMAIL_SEND_FROM)
        return {"ok": ok}
    except ImportError:
        raise HTTPException(501, "Gmail not available")
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/gmail/sync-calendar")
def api_gmail_sync_calendar():
    """Sync task deadlines to Google Calendar."""
    try:
        from roost.gmail.calendar_write import sync_task_deadlines
        stats = sync_task_deadlines()
        return {"ok": True, "stats": stats}
    except ImportError:
        raise HTTPException(501, "Gmail/Calendar not available")
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Roles ────────────────────────────────────────────────────────────

@router.get("/roles")
def api_list_roles(active_only: bool = True):
    return task_service.list_roles(active_only=active_only)


@router.get("/roles/{code}")
def api_get_role(code: str):
    role = task_service.get_role(code)
    if not role:
        raise HTTPException(404, "Role not found")
    return role


@router.post("/roles", status_code=201)
def api_create_role(code: str, label: str, description: str = ""):
    existing = task_service.get_role(code)
    if existing:
        raise HTTPException(409, f"Role '{code}' already exists")
    return task_service.create_role(code, label, description)


@router.put("/roles/{code}")
def api_update_role(code: str, label: str | None = None,
                    description: str | None = None, is_active: int | None = None):
    role = task_service.get_role(code)
    if not role:
        raise HTTPException(404, "Role not found")
    return task_service.update_role(code, label=label, description=description,
                                    is_active=is_active)


# ── Contacts ─────────────────────────────────────────────────────────

@router.get("/contacts")
def api_list_contacts(entity_id: int | None = None):
    return task_service.list_contacts(entity_id=entity_id)


@router.get("/contacts/{contact_id}")
def api_get_contact(contact_id: int):
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return contact


@router.post("/contacts", status_code=201)
@limiter.limit("20/minute")
def api_create_contact(request: Request, data: ContactCreate):
    return task_service.create_contact(data)


@router.put("/contacts/{contact_id}")
def api_update_contact(contact_id: int, data: ContactUpdate):
    contact = task_service.update_contact(contact_id, data)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return contact


@router.delete("/contacts/{contact_id}")
def api_delete_contact(contact_id: int):
    if not task_service.delete_contact(contact_id):
        raise HTTPException(404, "Contact not found")
    return {"ok": True}


@router.get("/contacts/{contact_id}/assignments")
def api_contact_assignments(contact_id: int):
    """All assignments (project + task) for a contact."""
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return task_service.list_assignments_by_contact(contact_id)


# ── Project Assignments ──────────────────────────────────────────────

@router.get("/projects/{project_id}/assignments")
def api_list_project_assignments(project_id: int, role: str | None = None):
    project = task_service.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return task_service.list_project_assignments(project_id=project_id, role=role)


@router.post("/projects/{project_id}/assignments", status_code=201)
def api_create_project_assignment(project_id: int, data: ProjectAssignmentCreate):
    project = task_service.get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    contact = task_service.get_contact(data.contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    # Override project_id from URL
    data.project_id = project_id
    try:
        return task_service.create_project_assignment(data)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "Assignment already exists")
        raise HTTPException(400, str(e))


@router.delete("/projects/{project_id}/assignments/{assignment_id}")
def api_delete_project_assignment(project_id: int, assignment_id: int):
    if not task_service.delete_project_assignment(assignment_id):
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}


@router.get("/projects/{project_id}/tree")
def api_project_tree(project_id: int):
    """Get project with children and assignments."""
    tree = task_service.get_project_tree(project_id)
    if not tree:
        raise HTTPException(404, "Project not found")
    return tree


# ── Task Assignments (RACI) ─────────────────────────────────────────

@router.get("/tasks/{task_id}/assignments")
def api_list_task_assignments(task_id: int, role: str | None = None):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    return task_service.list_task_assignments(task_id=task_id, role=role)


@router.post("/tasks/{task_id}/assignments", status_code=201)
def api_create_task_assignment(task_id: int, data: TaskAssignmentCreate):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(404, "Task not found")
    contact = task_service.get_contact(data.contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    data.task_id = task_id
    try:
        return task_service.create_task_assignment(data)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "Assignment already exists")
        raise HTTPException(400, str(e))


@router.delete("/tasks/{task_id}/assignments/{assignment_id}")
def api_delete_task_assignment(task_id: int, assignment_id: int):
    if not task_service.delete_task_assignment(assignment_id):
        raise HTTPException(404, "Assignment not found")
    return {"ok": True}


# ── Entities ─────────────────────────────────────────────────────────

@router.get("/entities")
def api_list_entities(status: str | None = None):
    """List all entities (companies/organisations)."""
    return task_service.list_entities(status=status)


@router.get("/entities/{entity_id}")
def api_get_entity(entity_id: int):
    entity = task_service.get_entity(entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    return entity


@router.post("/entities", status_code=201)
def api_create_entity(data: EntityCreate):
    try:
        return task_service.create_entity(data)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, f"Entity '{data.name}' already exists")
        raise HTTPException(400, str(e))


@router.put("/entities/{entity_id}")
def api_update_entity(entity_id: int, data: EntityUpdate):
    entity = task_service.update_entity(entity_id, data)
    if not entity:
        raise HTTPException(404, "Entity not found")
    return entity


@router.delete("/entities/{entity_id}")
def api_delete_entity(entity_id: int):
    if not task_service.delete_entity(entity_id):
        raise HTTPException(404, "Entity not found")
    return {"ok": True}


@router.get("/entities/{entity_id}/tree")
def api_entity_tree(entity_id: int):
    """Get entity with its projects and people."""
    tree = task_service.get_entity_tree(entity_id)
    if not tree:
        raise HTTPException(404, "Entity not found")
    return tree


# ── Contact-Entity Affiliations ─────────────────────────────────────

@router.get("/contacts/{contact_id}/entities")
def api_list_contact_entities(contact_id: int):
    """List entity affiliations for a contact."""
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return task_service.list_contact_entities(contact_id=contact_id)


@router.post("/contacts/{contact_id}/entities", status_code=201)
def api_add_contact_entity(contact_id: int, data: ContactEntityCreate):
    """Link a contact to an entity."""
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    entity = task_service.get_entity(data.entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    data.contact_id = contact_id
    try:
        return task_service.add_contact_entity(data)
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(409, "Affiliation already exists")
        raise HTTPException(400, str(e))


@router.delete("/contacts/{contact_id}/entities/{ce_id}")
def api_remove_contact_entity(contact_id: int, ce_id: int):
    """Remove a contact-entity affiliation."""
    if not task_service.remove_contact_entity(ce_id):
        raise HTTPException(404, "Affiliation not found")
    return {"ok": True}


@router.get("/entities/{entity_id}/people")
def api_entity_people(entity_id: int):
    """List people affiliated with an entity."""
    entity = task_service.get_entity(entity_id)
    if not entity:
        raise HTTPException(404, "Entity not found")
    return task_service.list_contact_entities(entity_id=entity_id)


# ── Contact Communications ───────────────────────────────────────────

@router.get("/contacts/{contact_id}/communications")
def api_list_communications(contact_id: int, comm_type: str | None = None,
                            limit: int = 30):
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return task_service.list_communications(contact_id, comm_type=comm_type, limit=limit)


@router.post("/contacts/{contact_id}/communications", status_code=201)
def api_log_communication(contact_id: int, data: CommunicationCreate):
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    data.contact_id = contact_id
    return task_service.log_communication(data)


@router.delete("/contacts/{contact_id}/communications/{comm_id}")
def api_delete_communication(contact_id: int, comm_id: int):
    if not task_service.delete_communication(comm_id):
        raise HTTPException(404, "Communication not found")
    return {"ok": True}


@router.post("/contacts/{contact_id}/sync-emails")
def api_sync_contact_emails(contact_id: int, max_results: int = 20):
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return task_service.sync_contact_emails(contact_id, max_results=max_results)


@router.post("/contacts/{contact_id}/sync-meetings")
def api_sync_contact_meetings(contact_id: int, days: int = 30):
    contact = task_service.get_contact(contact_id)
    if not contact:
        raise HTTPException(404, "Contact not found")
    return task_service.sync_contact_meetings(contact_id, days=days)


# ── Claude Sessions ──────────────────────────────────────────────

@router.get("/sessions")
def api_list_sessions(status: str | None = None):
    from roost import session_service
    sessions = session_service.list_sessions(status=status)
    return [s.model_dump() for s in sessions]


@router.post("/sessions", status_code=201)
def api_create_session(request: Request, data: dict):
    from roost import session_service
    from roost.models import ClaudeSessionCreate
    user = _api_current_user(request) or {}
    create_data = ClaudeSessionCreate(**data)
    session = session_service.create_session(
        create_data, user_id=user.get("user_id"), user_email=user.get("email", ""),
    )
    return session.model_dump()


@router.get("/sessions/{session_id}")
def api_get_session(session_id: int):
    from roost import session_service
    session = session_service.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.model_dump()


@router.post("/sessions/{session_id}/connect")
def api_connect_session(session_id: int):
    from roost import session_service
    session = session_service.connect_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found or closed")
    return session.model_dump()


@router.post("/sessions/{session_id}/close")
def api_close_session(session_id: int):
    from roost import session_service
    session = session_service.close_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session.model_dump()
