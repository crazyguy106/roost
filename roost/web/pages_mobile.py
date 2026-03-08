"""Mobile HTML page routes (server-rendered, /m/ prefix)."""

import json
import logging
import os
from pathlib import Path
from datetime import date, timedelta, datetime
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from roost import task_service
from roost.models import (
    TaskCreate, TaskUpdate,
    ContactCreate, ContactUpdate,
    EntityCreate, EntityUpdate,
    ContactEntityCreate, CommunicationCreate,
    ProjectCreate, ProjectUpdate, ProjectAssignmentCreate,
    NoteCreate,
)
from roost.web.permissions import is_admin_or_owner, can_access_project

logger = logging.getLogger("roost.web.pages_mobile")

router = APIRouter(prefix="/m")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


# ── Jinja2 filters (shared with pages.py) ────────────────────────────

def _short_date(value):
    if not value:
        return ""
    try:
        if isinstance(value, str):
            value = datetime.strptime(value[:10], "%Y-%m-%d").date()
        elif hasattr(value, 'date') and callable(value.date):
            value = value.date()
        return value.strftime("%b %-d")
    except Exception:
        return str(value)[:10]


def _is_overdue(value):
    if not value:
        return False
    try:
        if isinstance(value, str):
            value = datetime.strptime(value[:10], "%Y-%m-%d").date()
        elif hasattr(value, 'date') and callable(value.date):
            value = value.date()
        return value < date.today()
    except Exception:
        return False


templates.env.filters["short_date"] = _short_date
templates.env.filters["is_overdue"] = _is_overdue


# ── Helpers ───────────────────────────────────────────────────────────

def _base_context(request: Request) -> dict:
    return {
        "request": request,
        "current_user": getattr(request.state, "current_user", None),
    }


def _visibility_user_id(request: Request):
    user = getattr(request.state, "current_user", None)
    if not user or is_admin_or_owner(user):
        return None
    return user.get("user_id")


# ── Dashboard ─────────────────────────────────────────────────────────

@router.get("/")
def mobile_dashboard(request: Request):
    vis_uid = _visibility_user_id(request)
    all_tasks = task_service.list_tasks(visible_to_user_id=vis_uid)
    counts = {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0}
    for t in all_tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1

    today = None
    try:
        from roost.triage import get_today_tasks
        today = get_today_tasks()
    except Exception:
        logger.debug("Failed to load today's triage tasks", exc_info=True)

    focus_tasks = task_service.get_focus_tasks()
    streak = task_service.get_streak()
    spoon_status = task_service.get_spoon_status()
    energy_mode = task_service.get_energy_mode()
    shutdown_active = task_service.is_shutdown_active()

    return templates.TemplateResponse("mobile/dashboard.html", {
        **_base_context(request),
        "counts": counts,
        "today": today,
        "focus_tasks": focus_tasks,
        "streak": streak,
        "spoon_status": spoon_status,
        "energy_mode": energy_mode,
        "shutdown_active": shutdown_active,
        "active_tab": "home",
        "page_title": "Dashboard",
        "desktop_url": "/",
    })


# ── Tasks ─────────────────────────────────────────────────────────────

@router.get("/tasks")
def mobile_tasks(request: Request, status: str | None = None,
                 q: str | None = None, page: int = 1):
    per_page = 30
    vis_uid = _visibility_user_id(request)
    tasks = task_service.list_tasks(
        status=status, visible_to_user_id=vis_uid, order_by="urgency",
        search=q,
    )

    total = len(tasks)
    start = (page - 1) * per_page
    tasks_page = tasks[start:start + per_page]
    has_more = start + per_page < total

    all_tasks = task_service.list_tasks(visible_to_user_id=vis_uid)
    project_names = sorted(set(t.project_name for t in all_tasks if t.project_name))

    return templates.TemplateResponse("mobile/tasks.html", {
        **_base_context(request),
        "tasks": tasks_page,
        "current_status": status,
        "search_query": q,
        "project_names": project_names,
        "has_more": has_more,
        "current_page": page,
        "active_tab": "tasks",
        "page_title": "Tasks",
        "desktop_url": "/tasks",
    })


@router.get("/tasks/new")
def mobile_task_new(request: Request):
    projects = task_service.list_projects()
    return templates.TemplateResponse("mobile/task_new.html", {
        **_base_context(request),
        "projects": projects,
        "active_tab": "tasks",
        "page_title": "New Task",
        "desktop_url": "/tasks/new",
    })


@router.get("/tasks/{task_id}")
def mobile_task_detail(request: Request, task_id: int):
    task = task_service.get_task(task_id)
    if not task:
        return RedirectResponse("/m/tasks", status_code=303)
    projects = task_service.list_projects()
    user = getattr(request.state, "current_user", None)
    _can_edit = True
    if user and task.project_id and not is_admin_or_owner(user):
        if not can_access_project(user, task.project_id):
            _can_edit = False
    return templates.TemplateResponse("mobile/task_detail.html", {
        **_base_context(request),
        "task": task,
        "projects": projects,
        "can_edit": _can_edit,
        "active_tab": "tasks",
        "page_title": f"Task #{task_id}",
        "desktop_url": f"/tasks/{task_id}",
    })


# ── Task Fragment Routes (HTMX) ──────────────────────────────

def _hx_trigger(msg: str, type: str = "success") -> str:
    """Build HX-Trigger JSON header value for toast notifications."""
    return json.dumps({"showToast": {"msg": msg, "type": type}})


@router.post("/tasks/{task_id}/complete")
def mobile_task_complete(request: Request, task_id: int):
    task = task_service.complete_task(task_id, source="web")
    if not task:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Task not found", "error")})
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Task completed!"),
        "HX-Redirect": f"/m/tasks/{task_id}",
    })


@router.post("/tasks/{task_id}/start")
def mobile_task_start(request: Request, task_id: int):
    task = task_service.update_task(task_id, TaskUpdate(status="in_progress"), source="web")
    if not task:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Task not found", "error")})
    return templates.TemplateResponse("mobile/fragments/task_actions.html", {
        "request": request, "task": task,
    }, headers={"HX-Trigger": _hx_trigger("Started working")})


@router.post("/tasks/{task_id}/focus")
def mobile_task_focus(request: Request, task_id: int):
    result = task_service.set_focus(task_id)
    if not result.get("ok"):
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger(result.get("message", "Failed"), "error")})
    task = task_service.get_task(task_id)
    return templates.TemplateResponse("mobile/fragments/task_actions.html", {
        "request": request, "task": task,
    }, headers={"HX-Trigger": _hx_trigger("Focused")})


@router.post("/tasks/{task_id}/unfocus")
def mobile_task_unfocus(request: Request, task_id: int):
    task_service.clear_focus(task_id)
    task = task_service.get_task(task_id)
    return templates.TemplateResponse("mobile/fragments/task_actions.html", {
        "request": request, "task": task,
    }, headers={"HX-Trigger": _hx_trigger("Unfocused")})


@router.post("/tasks/{task_id}/delete")
def mobile_task_delete(request: Request, task_id: int):
    ok = task_service.delete_task(task_id)
    if not ok:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Task not found", "error")})
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Task deleted"),
        "HX-Redirect": "/m/tasks",
    })


@router.post("/tasks/{task_id}/shelve")
def mobile_task_shelve(request: Request, task_id: int):
    task = task_service.shelve_task(task_id)
    if not task:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Task not found", "error")})
    return templates.TemplateResponse("mobile/fragments/task_actions.html", {
        "request": request, "task": task,
    }, headers={"HX-Trigger": _hx_trigger("Shelved to Someday")})


@router.post("/tasks/{task_id}/unshelve")
def mobile_task_unshelve(request: Request, task_id: int):
    task = task_service.unshelve_task(task_id)
    if not task:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Task not found", "error")})
    return templates.TemplateResponse("mobile/fragments/task_actions.html", {
        "request": request, "task": task,
    }, headers={"HX-Trigger": _hx_trigger("Restored from Someday")})


@router.put("/tasks/{task_id}/edit")
def mobile_task_edit(request: Request, task_id: int,
                     title: str = Form(...), description: str = Form(""),
                     status: str = Form("todo"), priority: str = Form("medium"),
                     deadline: str = Form(""), project_id: str = Form(""),
                     context_note: str = Form("")):
    data = TaskUpdate(
        title=title,
        description=description,
        status=status,
        priority=priority,
        deadline=deadline if deadline else None,
        project_id=int(project_id) if project_id else None,
        context_note=context_note if context_note else None,
    )
    task_service.update_task(task_id, data, source="web")
    task = task_service.get_task(task_id)
    can_edit = True
    user = getattr(request.state, "current_user", None)
    if user and task and task.project_id and not is_admin_or_owner(user):
        if not can_access_project(user, task.project_id):
            can_edit = False
    return templates.TemplateResponse("mobile/fragments/task_view.html", {
        "request": request, "task": task, "can_edit": can_edit,
    }, headers={"HX-Trigger": json.dumps({"showToast": {"msg": "Saved", "type": "success"}, "editComplete": True})})


@router.post("/tasks/create")
def mobile_task_create(request: Request,
                       title: str = Form(...), description: str = Form(""),
                       priority: str = Form("medium"), deadline: str = Form(""),
                       project_id: str = Form("")):
    data = TaskCreate(
        title=title,
        description=description,
        priority=priority,
        deadline=deadline if deadline else None,
        project_id=int(project_id) if project_id else None,
    )
    task = task_service.create_task(data, source="web")
    # HTMX request → respond with HX headers
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={
            "HX-Trigger": _hx_trigger("Task created"),
            "HX-Redirect": f"/m/tasks/{task.id}",
        })
    # Plain POST fallback (HTMX not loaded) → redirect
    return RedirectResponse(f"/m/tasks/{task.id}", status_code=303)


# ── Projects ──────────────────────────────────────────────────────────

@router.get("/projects")
def mobile_projects(request: Request):
    vis_uid = _visibility_user_id(request)
    entities = task_service.list_entities()
    projects = task_service.list_projects(visible_to_user_id=vis_uid)

    grouped = []
    entity_map = {}
    for e in entities:
        entity_map[e.name] = e.id

    by_entity = {}
    for p in projects:
        key = p.entity_name or "Unaffiliated"
        by_entity.setdefault(key, []).append(p)

    for e in entities:
        if e.name in by_entity:
            grouped.append((e.name, by_entity[e.name]))
    if "Unaffiliated" in by_entity:
        grouped.append(("Unaffiliated", by_entity["Unaffiliated"]))

    return templates.TemplateResponse("mobile/projects.html", {
        **_base_context(request),
        "grouped_projects": grouped,
        "active_tab": "projects",
        "page_title": "Projects",
        "desktop_url": "/projects",
    })


@router.get("/projects/new")
def mobile_project_new(request: Request):
    entities = task_service.list_entities()
    return templates.TemplateResponse("mobile/project_new.html", {
        **_base_context(request),
        "entities": entities,
        "active_tab": "projects",
        "page_title": "New Project",
        "desktop_url": "/projects",
    })


@router.get("/projects/{project_id}")
def mobile_project_detail(request: Request, project_id: int):
    project = task_service.get_project(project_id)
    if not project:
        return RedirectResponse("/m/projects", status_code=303)

    raw_progress = task_service.get_progress(project=project.name)
    by_status = raw_progress.get("by_status", {})
    progress = {
        "todo": by_status.get("todo", 0),
        "in_progress": by_status.get("in_progress", 0),
        "done": by_status.get("done", 0),
        "blocked": by_status.get("blocked", 0),
    }
    recent_tasks = task_service.list_tasks(project=project.name, limit=10)
    assignments = task_service.list_project_assignments(project_id=project_id)
    contacts = task_service.list_contacts()
    entities = task_service.list_entities()

    return templates.TemplateResponse("mobile/project_detail.html", {
        **_base_context(request),
        "project": project,
        "progress": progress,
        "recent_tasks": recent_tasks,
        "assignments": assignments,
        "contacts": contacts,
        "entities": entities,
        "active_tab": "projects",
        "page_title": project.name,
        "desktop_url": f"/projects/{project_id}",
    })


@router.post("/projects/create")
def mobile_project_create(request: Request,
                          name: str = Form(...),
                          description: str = Form(""),
                          category: str = Form(""),
                          project_type: str = Form("project"),
                          entity_id: str = Form("")):
    data = ProjectCreate(
        name=name,
        description=description,
        category=category,
        project_type=project_type,
        entity_id=int(entity_id) if entity_id else None,
    )
    project = task_service.create_project(data, source="web")
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Project created"),
        "HX-Redirect": f"/m/projects/{project.id}",
    })


@router.post("/projects/{project_id}/edit")
def mobile_project_update(request: Request, project_id: int,
                          name: str = Form(...),
                          description: str = Form(""),
                          category: str = Form(""),
                          project_type: str = Form("project"),
                          entity_id: str = Form("")):
    data = ProjectUpdate(
        name=name,
        description=description,
        category=category,
        project_type=project_type,
        entity_id=int(entity_id) if entity_id else None,
    )
    task_service.update_project(project_id, data, source="web")
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Project updated"),
        "HX-Redirect": f"/m/projects/{project_id}",
    })


@router.post("/projects/{project_id}/delete")
def mobile_project_delete(request: Request, project_id: int):
    ok = task_service.delete_project(project_id)
    if not ok:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Project not found", "error")})
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Project deleted"),
        "HX-Redirect": "/m/projects",
    })


@router.post("/projects/{project_id}/pause")
def mobile_project_pause(request: Request, project_id: int):
    from roost.models import ProjectStatus
    task_service.update_project(project_id, ProjectUpdate(status=ProjectStatus.PAUSED), source="web")
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Project paused"),
        "HX-Redirect": f"/m/projects/{project_id}",
    })


@router.post("/projects/{project_id}/resume")
def mobile_project_resume(request: Request, project_id: int):
    from roost.models import ProjectStatus
    task_service.update_project(project_id, ProjectUpdate(status=ProjectStatus.ACTIVE), source="web")
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Project resumed"),
        "HX-Redirect": f"/m/projects/{project_id}",
    })


@router.post("/projects/{project_id}/add-member")
def mobile_project_add_member(request: Request, project_id: int,
                              contact_id: str = Form(...),
                              role: str = Form("I")):
    data = ProjectAssignmentCreate(
        contact_id=int(contact_id),
        project_id=project_id,
        role=role,
    )
    task_service.create_project_assignment(data)
    assignments = task_service.list_project_assignments(project_id=project_id)
    contacts = task_service.list_contacts()
    return templates.TemplateResponse("mobile/fragments/project_team.html", {
        "request": request,
        "project": task_service.get_project(project_id),
        "assignments": assignments,
        "contacts": contacts,
    }, headers={"HX-Trigger": _hx_trigger("Member added")})


@router.post("/projects/{project_id}/remove-member/{assignment_id}")
def mobile_project_remove_member(request: Request, project_id: int, assignment_id: int):
    task_service.delete_project_assignment(assignment_id)
    assignments = task_service.list_project_assignments(project_id=project_id)
    contacts = task_service.list_contacts()
    return templates.TemplateResponse("mobile/fragments/project_team.html", {
        "request": request,
        "project": task_service.get_project(project_id),
        "assignments": assignments,
        "contacts": contacts,
    }, headers={"HX-Trigger": _hx_trigger("Member removed")})


# ── Calendar ──────────────────────────────────────────────────────────

@router.get("/calendar")
def mobile_calendar(request: Request, year: int | None = None,
                    month: int | None = None):
    import calendar as cal_mod

    today_date = date.today()
    y = year or today_date.year
    m = month or today_date.month

    first_of_month = date(y, m, 1)
    start_offset = first_of_month.weekday()
    grid_start = first_of_month - timedelta(days=start_offset)
    grid_end = grid_start + timedelta(days=41)

    grid_dates = [grid_start + timedelta(days=i) for i in range(42)]

    # Fetch calendar events
    calendar_configured = False
    raw_events = []
    try:
        from roost.calendar_service import fetch_events_for_range, _is_calendar_available
        calendar_configured = _is_calendar_available()
        if calendar_configured:
            raw_events = fetch_events_for_range(grid_start, grid_end)
    except Exception:
        logger.debug("Failed to fetch calendar events", exc_info=True)

    events_by_date = {}
    for e in raw_events:
        if not e.get("start"):
            continue
        date_key = e["start"].date().isoformat()
        events_by_date.setdefault(date_key, []).append({
            "summary": e["summary"],
            "start": e["start"].isoformat(),
            "end": e["end"].isoformat() if e.get("end") else None,
            "location": e.get("location", ""),
            "calendar": e.get("calendar", ""),
            "all_day": e.get("all_day", False),
        })

    # Fetch task deadlines
    all_deadline_tasks = task_service.list_tasks(
        deadline_filter="has_deadline", order_by="deadline",
    )
    deadlines_by_date = {}
    for t in all_deadline_tasks:
        if not t.deadline or t.status.value == "done":
            continue
        try:
            d = t.deadline.date() if hasattr(t.deadline, 'date') else t.deadline
            if isinstance(d, str):
                d = datetime.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if grid_start <= d <= grid_end:
            date_key = d.isoformat()
            deadlines_by_date.setdefault(date_key, []).append({
                "id": t.id,
                "title": t.title,
                "status": t.status.value,
                "priority": t.priority.value,
                "deadline": d.isoformat(),
            })

    # Previous/next month
    if m == 1:
        prev_y, prev_m = y - 1, 12
    else:
        prev_y, prev_m = y, m - 1
    if m == 12:
        next_y, next_m = y + 1, 1
    else:
        next_y, next_m = y, m + 1

    month_name = cal_mod.month_name[m]

    return templates.TemplateResponse("mobile/calendar.html", {
        **_base_context(request),
        "year": y,
        "month": m,
        "month_name": month_name,
        "today": today_date.isoformat(),
        "grid_dates": [d.isoformat() for d in grid_dates],
        "events_by_date": events_by_date,
        "deadlines_by_date": deadlines_by_date,
        "prev_y": prev_y,
        "prev_m": prev_m,
        "next_y": next_y,
        "next_m": next_m,
        "calendar_configured": calendar_configured,
        "active_tab": "calendar",
        "page_title": f"{month_name} {y}",
        "desktop_url": "/calendar",
    })


# ── Contacts ──────────────────────────────────────────────────────────

@router.get("/contacts")
def mobile_contacts(request: Request, q: str | None = None):
    entities = task_service.list_entities()
    contacts = task_service.list_contacts()

    if q:
        q_lower = q.lower()
        contacts = [c for c in contacts if q_lower in c.name.lower()
                     or (c.email and q_lower in c.email.lower())]

    grouped = []
    entity_map = {}
    for e in entities:
        entity_map[e.name] = e.id

    by_entity = {}
    for c in contacts:
        key = c.entity_name or "Independent"
        by_entity.setdefault(key, []).append(c)

    for e in entities:
        if e.name in by_entity:
            grouped.append((e.name, by_entity[e.name]))
    if "Independent" in by_entity:
        grouped.append(("Independent", by_entity["Independent"]))

    return templates.TemplateResponse("mobile/contacts.html", {
        **_base_context(request),
        "grouped_contacts": grouped,
        "search_query": q,
        "active_tab": "people",
        "page_title": "People",
        "desktop_url": "/contacts",
    })


@router.get("/contacts/new")
def mobile_contact_new(request: Request):
    entities = task_service.list_entities()
    return templates.TemplateResponse("mobile/contact_new.html", {
        **_base_context(request),
        "entities": entities,
        "active_tab": "people",
        "page_title": "New Contact",
        "desktop_url": "/contacts",
    })


@router.get("/contacts/{contact_id}")
def mobile_contact_detail(request: Request, contact_id: int):
    contact = task_service.get_contact(contact_id)
    if not contact:
        return RedirectResponse("/m/contacts", status_code=303)
    affiliations = task_service.list_contact_entities(contact_id=contact_id)
    communications = task_service.list_communications(contact_id, limit=20)
    entities = task_service.list_entities()

    return templates.TemplateResponse("mobile/contact_detail.html", {
        **_base_context(request),
        "contact": contact,
        "affiliations": affiliations,
        "communications": communications,
        "entities": entities,
        "active_tab": "people",
        "page_title": contact.name,
        "desktop_url": f"/contacts/{contact_id}",
    })


@router.post("/contacts/create")
def mobile_contact_create(request: Request,
                          name: str = Form(...), email: str = Form(""),
                          phone: str = Form(""), notes: str = Form("")):
    data = ContactCreate(name=name, email=email, phone=phone, notes=notes)
    contact = task_service.create_contact(data)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Contact created"),
        "HX-Redirect": f"/m/contacts/{contact.id}",
    })


@router.get("/contacts/{contact_id}/edit")
def mobile_contact_edit(request: Request, contact_id: int):
    contact = task_service.get_contact(contact_id)
    if not contact:
        return RedirectResponse("/m/contacts", status_code=303)
    return templates.TemplateResponse("mobile/contact_edit.html", {
        **_base_context(request),
        "contact": contact,
        "active_tab": "people",
        "page_title": f"Edit {contact.name}",
        "desktop_url": f"/contacts/{contact_id}",
    })


@router.post("/contacts/{contact_id}/edit")
def mobile_contact_update(request: Request, contact_id: int,
                          name: str = Form(...), notes: str = Form("")):
    data = ContactUpdate(name=name, notes=notes)
    task_service.update_contact(contact_id, data)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Contact updated"),
        "HX-Redirect": f"/m/contacts/{contact_id}",
    })


@router.post("/contacts/{contact_id}/add-identifier")
def mobile_contact_add_identifier(request: Request, contact_id: int,
                                  ident_type: str = Form(...),
                                  ident_value: str = Form(...),
                                  ident_label: str = Form(""),
                                  ident_primary: int = Form(0)):
    from roost.services.contacts import set_contact_identifier
    set_contact_identifier(contact_id, ident_type, ident_value.strip(),
                           label=ident_label.strip(), is_primary=ident_primary)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Identifier added"),
        "HX-Redirect": f"/m/contacts/{contact_id}/edit",
    })


@router.post("/contacts/{contact_id}/remove-identifier/{ident_id}")
def mobile_contact_remove_identifier(request: Request, contact_id: int, ident_id: int):
    from roost.services.contacts import remove_contact_identifier
    remove_contact_identifier(ident_id)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Identifier removed"),
        "HX-Redirect": f"/m/contacts/{contact_id}/edit",
    })


@router.post("/contacts/{contact_id}/delete")
def mobile_contact_delete(request: Request, contact_id: int):
    ok = task_service.delete_contact(contact_id)
    if not ok:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Contact not found", "error")})
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Contact deleted"),
        "HX-Redirect": "/m/contacts",
    })


@router.post("/contacts/{contact_id}/add-affiliation")
def mobile_contact_add_affiliation(request: Request, contact_id: int,
                                   entity_id: str = Form(...),
                                   title: str = Form("")):
    data = ContactEntityCreate(
        contact_id=contact_id,
        entity_id=int(entity_id),
        title=title,
    )
    task_service.add_contact_entity(data)
    affiliations = task_service.list_contact_entities(contact_id=contact_id)
    entities = task_service.list_entities()
    return templates.TemplateResponse("mobile/fragments/contact_affiliations.html", {
        "request": request,
        "contact": task_service.get_contact(contact_id),
        "affiliations": affiliations,
        "entities": entities,
    }, headers={"HX-Trigger": _hx_trigger("Affiliation added")})


@router.post("/contacts/{contact_id}/remove-affiliation/{ce_id}")
def mobile_contact_remove_affiliation(request: Request, contact_id: int, ce_id: int):
    task_service.remove_contact_entity(ce_id)
    affiliations = task_service.list_contact_entities(contact_id=contact_id)
    entities = task_service.list_entities()
    return templates.TemplateResponse("mobile/fragments/contact_affiliations.html", {
        "request": request,
        "contact": task_service.get_contact(contact_id),
        "affiliations": affiliations,
        "entities": entities,
    }, headers={"HX-Trigger": _hx_trigger("Affiliation removed")})


@router.post("/contacts/{contact_id}/log-communication")
def mobile_contact_log_comm(request: Request, contact_id: int,
                            comm_type: str = Form(...),
                            subject: str = Form(""),
                            detail: str = Form("")):
    data = CommunicationCreate(
        contact_id=contact_id,
        comm_type=comm_type,
        subject=subject,
        detail=detail,
    )
    task_service.log_communication(data)
    communications = task_service.list_communications(contact_id, limit=20)
    return templates.TemplateResponse("mobile/fragments/contact_comms.html", {
        "request": request,
        "contact": task_service.get_contact(contact_id),
        "communications": communications,
    }, headers={"HX-Trigger": _hx_trigger("Communication logged")})


@router.post("/contacts/{contact_id}/delete-communication/{comm_id}")
def mobile_contact_delete_comm(request: Request, contact_id: int, comm_id: int):
    task_service.delete_communication(comm_id)
    communications = task_service.list_communications(contact_id, limit=20)
    return templates.TemplateResponse("mobile/fragments/contact_comms.html", {
        "request": request,
        "contact": task_service.get_contact(contact_id),
        "communications": communications,
    }, headers={"HX-Trigger": _hx_trigger("Communication deleted")})


@router.post("/contacts/{contact_id}/sync-emails")
def mobile_contact_sync_emails(request: Request, contact_id: int):
    result = task_service.sync_contact_emails(contact_id)
    if "error" in result:
        return HTMLResponse("", headers={
            "HX-Trigger": _hx_trigger(result["error"], "error"),
            "HX-Redirect": f"/m/contacts/{contact_id}",
        })
    msg = f"Synced {result.get('new_records', 0)} new emails"
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger(msg),
        "HX-Redirect": f"/m/contacts/{contact_id}",
    })


@router.post("/contacts/{contact_id}/sync-meetings")
def mobile_contact_sync_meetings(request: Request, contact_id: int):
    result = task_service.sync_contact_meetings(contact_id)
    if "error" in result:
        return HTMLResponse("", headers={
            "HX-Trigger": _hx_trigger(result["error"], "error"),
            "HX-Redirect": f"/m/contacts/{contact_id}",
        })
    msg = f"Synced {result.get('new_meetings', 0)} new meetings"
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger(msg),
        "HX-Redirect": f"/m/contacts/{contact_id}",
    })


# ── Entities ─────────────────────────────────────────────────────────

@router.get("/entities")
def mobile_entities(request: Request):
    entities = task_service.list_entities()
    active = [e for e in entities if e.status == "active"]
    inactive = [e for e in entities if e.status != "active"]
    return templates.TemplateResponse("mobile/entities.html", {
        **_base_context(request),
        "active_entities": active,
        "inactive_entities": inactive,
        "active_tab": "people",
        "page_title": "Entities",
        "desktop_url": "/entities",
    })


@router.get("/entities/{entity_id}")
def mobile_entity_detail(request: Request, entity_id: int):
    entity = task_service.get_entity(entity_id)
    if not entity:
        return RedirectResponse("/m/entities", status_code=303)
    tree = task_service.get_entity_tree(entity_id)
    return templates.TemplateResponse("mobile/entity_detail.html", {
        **_base_context(request),
        "entity": entity,
        "projects": tree.get("projects", []),
        "people": tree.get("people", []),
        "active_tab": "people",
        "page_title": entity.name,
        "desktop_url": f"/entities/{entity_id}",
    })


@router.post("/entities/create")
def mobile_entity_create(request: Request,
                         name: str = Form(...),
                         description: str = Form(""),
                         notes: str = Form("")):
    data = EntityCreate(name=name, description=description, notes=notes)
    entity = task_service.create_entity(data)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Entity created"),
        "HX-Redirect": f"/m/entities/{entity.id}",
    })


@router.post("/entities/{entity_id}/edit")
def mobile_entity_update(request: Request, entity_id: int,
                         name: str = Form(...),
                         description: str = Form(""),
                         status: str = Form("active"),
                         notes: str = Form("")):
    data = EntityUpdate(name=name, description=description, status=status, notes=notes)
    task_service.update_entity(entity_id, data)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Entity updated"),
        "HX-Redirect": f"/m/entities/{entity_id}",
    })


@router.post("/entities/{entity_id}/delete")
def mobile_entity_delete(request: Request, entity_id: int):
    ok = task_service.delete_entity(entity_id)
    if not ok:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Entity not found", "error")})
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Entity deleted"),
        "HX-Redirect": "/m/entities",
    })


# ── Dashboard Actions ─────────────────────────────────────────────────

@router.post("/shutdown")
def mobile_shutdown(request: Request):
    result = task_service.execute_shutdown()
    msg = f"Shutdown: {result.get('paused_count', 0)} tasks paused"
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger(msg),
        "HX-Redirect": "/m/",
    })


@router.post("/resume-day")
def mobile_resume(request: Request):
    result = task_service.execute_resume()
    msg = f"Resumed: {result.get('resumed_count', 0)} tasks restored"
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger(msg),
        "HX-Redirect": "/m/",
    })


@router.post("/energy-mode")
def mobile_energy_mode(request: Request, level: str = Form(...)):
    task_service.set_energy_mode(level)
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger(f"Energy: {level}"),
        "HX-Redirect": "/m/",
    })


# ── Notes ────────────────────────────────────────────────────────────

@router.get("/notes")
def mobile_notes(request: Request, tag: str | None = None):
    notes = task_service.list_notes(tag=tag)
    all_notes = task_service.list_notes(limit=200)
    tags = sorted(set(n.tag for n in all_notes if n.tag))
    projects = task_service.list_projects()
    return templates.TemplateResponse("mobile/notes.html", {
        **_base_context(request),
        "notes": notes,
        "tags": tags,
        "projects": projects,
        "current_tag": tag,
        "active_tab": "",
        "page_title": "Notes",
        "desktop_url": "/notes",
    })


@router.post("/notes/create")
def mobile_note_create(request: Request,
                       content: str = Form(...),
                       title: str = Form(""),
                       tag: str = Form(""),
                       project_id: str = Form("")):
    data = NoteCreate(
        title=title.strip(),
        content=content,
        tag=tag,
        project_id=int(project_id) if project_id else None,
    )
    task_service.create_note(data, source="web")
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Note created"),
        "HX-Redirect": "/m/notes",
    })


@router.post("/notes/{note_id}/delete")
def mobile_note_delete(request: Request, note_id: int):
    ok = task_service.delete_note(note_id)
    if not ok:
        return HTMLResponse("", headers={"HX-Trigger": _hx_trigger("Note not found", "error")})
    return HTMLResponse("", headers={
        "HX-Trigger": _hx_trigger("Note deleted"),
        "HX-Redirect": "/m/notes",
    })


# ── Settings ─────────────────────────────────────────────────────────

@router.get("/settings")
def mobile_settings(request: Request):
    from roost.config_service import get_flags_status, get_integrations_status
    return templates.TemplateResponse("mobile/settings.html", {
        **_base_context(request),
        "flags": get_flags_status(),
        "integrations": get_integrations_status(),
        "active_tab": "",
        "page_title": "Settings",
        "desktop_url": "/settings",
    })


@router.post("/settings/flag/{flag_name}")
def toggle_flag(request: Request, flag_name: str):
    user = getattr(request.state, "current_user", None)
    if not user or user.get("role") not in ("admin", "owner"):
        return JSONResponse({"error": "Admin required"}, status_code=403)

    from roost.config_service import get_flag_value, set_flag_override
    current = get_flag_value(flag_name)
    result = set_flag_override(flag_name, not current)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


# ── Docs (Markdown Reader) ───────────────────────────────────────

_DOCS_ROOTS: list[Path] = [
    Path(p.strip()).expanduser().resolve()
    for p in os.getenv("DOCS_ROOTS", "~/projects").split(",")
    if p.strip()
]


def _safe_resolve(rel_path: str) -> Path | None:
    """Resolve a relative path safely within allowed DOCS_ROOTS.

    Returns the resolved Path if it exists and is inside an allowed root,
    otherwise None. Prevents path traversal via '..' or symlinks.

    Paths use the root directory name as the first component:
      "projects"                → /home/dev/projects (the root itself)
      "docs"  → /home/dev/docs
    """
    # Strip any traversal components
    clean = rel_path.replace("..", "").strip("/")
    if not clean:
        return None

    parts = clean.split("/", 1)
    root_name = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    for root in _DOCS_ROOTS:
        if root.name != root_name:
            continue
        if not rest:
            # Path is just the root name — return the root itself
            return root
        candidate = (root / rest).resolve()
        if candidate.is_relative_to(root) and candidate.exists():
            return candidate
    return None


def _human_size(nbytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if nbytes < 1024:
            return f"{nbytes:.0f} {unit}" if unit == "B" else f"{nbytes:.1f} {unit}"
        nbytes /= 1024
    return f"{nbytes:.1f} TB"


def _build_breadcrumbs(rel_path: str) -> list[dict]:
    """Build breadcrumb trail from a relative path."""
    parts = [p for p in rel_path.strip("/").split("/") if p]
    crumbs = []
    for i, part in enumerate(parts):
        crumbs.append({
            "name": part,
            "path": "/".join(parts[: i + 1]),
        })
    return crumbs


def _path_to_root_relative(resolved: Path) -> str:
    """Convert an absolute resolved path back to a root-relative string.

    Prefixes with the root directory name so paths round-trip through _safe_resolve:
      /home/dev/docs → "docs"
    """
    for root in _DOCS_ROOTS:
        if resolved == root:
            return root.name
        if resolved.is_relative_to(root):
            return f"{root.name}/{resolved.relative_to(root)}"
    return ""


@router.get("/docs")
def mobile_docs(request: Request, path: str = ""):
    """File browser — list directories and .md files."""
    ctx = {
        **_base_context(request),
        "active_tab": "",
        "desktop_url": "/docs",
    }

    if not path:
        # Root listing — show each allowed root as a directory entry
        entries = []
        for root in _DOCS_ROOTS:
            if root.is_dir():
                # Use the last directory name as display name
                entries.append({
                    "name": root.name,
                    "is_dir": True,
                    "url": f"/m/docs?path={root.name}",
                    "size": None,
                })
        return templates.TemplateResponse("mobile/docs.html", {
            **ctx,
            "view_mode": "browse",
            "entries": entries,
            "breadcrumbs": [],
            "parent_path": None,
            "page_title": "Docs",
        })

    resolved = _safe_resolve(path)
    if not resolved:
        return RedirectResponse("/m/docs", status_code=303)

    if resolved.is_dir():
        # List directory contents — folders first, then .md files
        dirs = []
        files = []
        try:
            for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                rel = _path_to_root_relative(child)
                if child.is_dir():
                    dirs.append({
                        "name": child.name,
                        "is_dir": True,
                        "url": f"/m/docs?path={rel}",
                        "size": None,
                    })
                elif child.suffix.lower() in (".md", ".json"):
                    files.append({
                        "name": child.name,
                        "is_dir": False,
                        "url": f"/m/docs?path={rel}",
                        "size": _human_size(child.stat().st_size),
                    })
        except PermissionError:
            pass

        # Parent path for back button
        rel_parts = [p for p in path.strip("/").split("/") if p]
        parent = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""

        return templates.TemplateResponse("mobile/docs.html", {
            **ctx,
            "view_mode": "browse",
            "entries": dirs + files,
            "breadcrumbs": _build_breadcrumbs(path),
            "parent_path": parent,
            "page_title": resolved.name,
        })

    elif resolved.is_file() and resolved.suffix.lower() in (".md", ".json"):
        # Render markdown or JSON file
        content = resolved.read_text(encoding="utf-8", errors="replace")
        rel = _path_to_root_relative(resolved)
        # Parent directory for back button
        rel_parts = [p for p in path.strip("/").split("/") if p]
        parent = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""

        user = getattr(request.state, "current_user", None)
        can_edit = bool(user and is_admin_or_owner(user))

        # JSON files: wrap in markdown code fence for rendering
        if resolved.suffix.lower() == ".json":
            import json as _json
            try:
                parsed = _json.loads(content)
                content = "```json\n" + _json.dumps(parsed, indent=2, ensure_ascii=False) + "\n```"
            except _json.JSONDecodeError:
                content = "```\n" + content + "\n```"

        return templates.TemplateResponse("mobile/docs.html", {
            **ctx,
            "view_mode": "view",
            "content": content,
            "file_path": rel,
            "filename": resolved.name,
            "breadcrumbs": _build_breadcrumbs(path),
            "parent_path": parent,
            "can_edit": can_edit,
            "page_title": resolved.name,
        })

    return RedirectResponse("/m/docs", status_code=303)


@router.post("/docs/save")
async def mobile_docs_save(request: Request, path: str = Form(...),
                           content: str = Form(...)):
    """Save edited markdown back to filesystem. Admin/owner only."""
    user = getattr(request.state, "current_user", None)
    if not user or not is_admin_or_owner(user):
        return JSONResponse({"error": "Admin access required"}, status_code=403)

    resolved = _safe_resolve(path)
    if not resolved or not resolved.is_file() or resolved.suffix.lower() != ".md":
        return JSONResponse({"error": "Invalid file path"}, status_code=400)

    try:
        resolved.write_text(content, encoding="utf-8")
        return JSONResponse({"ok": True})
    except Exception as e:
        logger.exception("Failed to save docs file: %s", path)
        return JSONResponse({"error": str(e)}, status_code=500)
