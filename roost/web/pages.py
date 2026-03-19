"""HTML page routes (server-rendered with Jinja2)."""

import logging
import os
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from roost.models import (
    TaskCreate, TaskUpdate, TaskStatus, Priority,
    ProjectCreate, ProjectUpdate, NoteCreate,
    EntityCreate, EntityUpdate, ContactCreate, ContactUpdate,
    ContactEntityCreate, ProjectAssignmentCreate,
    CommunicationCreate,
)
from roost import task_service
try:
    from roost.curriculum_context import get_modules_by_phase, get_phase_names, MODULES, PHASE_NAMES
except ImportError:
    MODULES: dict = {}
    PHASE_NAMES: dict = {}
    def get_modules_by_phase(*a, **kw): return {}
    def get_phase_names(*a, **kw): return {}
from roost.web.permissions import (
    is_admin_or_owner, can_access_project, can_edit_project, can_edit_task,
)

logger = logging.getLogger("roost.web.pages")

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _short_date(value):
    """Format a deadline to short date like 'Feb 18'."""
    from datetime import date, datetime
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
    """Check if a deadline date is in the past."""
    from datetime import date, datetime
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

# DeptTools integration — show nav link when co-deployed
templates.env.globals["depttools_enabled"] = os.environ.get(
    "DEPTTOOLS_ENABLED", ""
).lower() in ("1", "true", "yes")


def _base_context(request: Request) -> dict:
    """Base template context: request + current_user."""
    return {
        "request": request,
        "current_user": getattr(request.state, "current_user", None),
    }


def _visibility_user_id(request: Request) -> int | None:
    """Return user_id for visibility filtering, or None for admin/owner (see all)."""
    user = getattr(request.state, "current_user", None)
    if not user or is_admin_or_owner(user):
        return None
    return user.get("user_id")


def _page_user_id(request: Request) -> int | None:
    """Return user_id from session for data scoping. None if no session user."""
    user = getattr(request.state, "current_user", None)
    if user:
        return user.get("user_id")
    return None


def _forbidden_response(request: Request):
    """Return a 403 forbidden page."""
    return templates.TemplateResponse(
        "forbidden.html", _base_context(request), status_code=403,
    )


# ── Dashboard ────────────────────────────────────────────────────────

@router.get("/")
def dashboard(request: Request):
    vis_uid = _visibility_user_id(request)
    all_tasks = task_service.list_tasks(visible_to_user_id=vis_uid)
    counts = {"todo": 0, "in_progress": 0, "done": 0, "blocked": 0}
    for t in all_tasks:
        counts[t.status.value] = counts.get(t.status.value, 0) + 1

    active = [t for t in all_tasks if t.status.value != "done"]

    # Today's triage data
    today = None
    try:
        from roost.triage import get_today_tasks
        today = get_today_tasks()
    except Exception:
        logger.debug("Failed to load today's triage tasks", exc_info=True)

    # Entity summary — open task counts per entity
    entities = task_service.list_entities()
    projects = task_service.list_projects()
    proj_entity = {p.name: p.entity_id for p in projects}
    entity_open: dict[int, int] = {}
    for t in all_tasks:
        if t.status.value != "done" and t.project_name:
            eid = proj_entity.get(t.project_name)
            if eid:
                entity_open[eid] = entity_open.get(eid, 0) + 1
    entity_summary = [
        {"id": e.id, "name": e.name, "project_count": e.project_count,
         "open_tasks": entity_open.get(e.id, 0)}
        for e in entities
    ]

    # Focus tasks
    focus_tasks = task_service.get_focus_tasks()
    focus_suggestions = task_service.suggest_focus() if not focus_tasks else []

    # Energy mode
    energy_mode = task_service.get_energy_mode()
    energy_tasks = task_service.list_matching_effort_tasks(energy_mode) if energy_mode == "low" else []

    # Shutdown state
    shutdown_summary = task_service.get_shutdown_summary()

    # Streak + spoons
    streak = task_service.get_streak()
    spoon_status = task_service.get_spoon_status()

    return templates.TemplateResponse("dashboard.html", {
        **_base_context(request),
        "counts": counts,
        "active_tasks": active[:20],
        "today": today,
        "entity_summary": entity_summary,
        "focus_tasks": focus_tasks,
        "focus_suggestions": focus_suggestions,
        "energy_mode": energy_mode,
        "energy_tasks": energy_tasks,
        "shutdown_summary": shutdown_summary,
        "streak": streak,
        "spoon_status": spoon_status,
    })


# ── Task pages ───────────────────────────────────────────────────────

@router.get("/tasks")
def task_list_page(request: Request, status: str | None = None,
                   project: str | None = None, someday: bool = False,
                   focus: bool = False, q: str | None = None,
                   mine: bool = False, page: int = 1):
    per_page = 30

    # Resolve "mine" filter to assigned_to user_id
    current_user = getattr(request.state, "current_user", None)
    assigned_to = None
    if mine and current_user and current_user.get("user_id"):
        assigned_to = current_user["user_id"]

    vis_uid = _visibility_user_id(request)
    tasks = task_service.list_tasks(
        status=status, project=project,
        include_someday=someday, focus_only=focus,
        assigned_to=assigned_to,
        visible_to_user_id=vis_uid,
    )
    # For someday view, only show someday tasks
    if someday:
        tasks = [t for t in tasks if t.someday]

    # Search filter
    if q:
        q_lower = q.lower()
        tasks = [t for t in tasks if q_lower in t.title.lower()]

    # Pagination
    total = len(tasks)
    start = (page - 1) * per_page
    tasks_page = tasks[start:start + per_page]
    has_more = start + per_page < total

    # Build pagination query string (preserve filters)
    parts = []
    if status: parts.append(f"status={status}")
    if project: parts.append(f"project={project}")
    if someday: parts.append("someday=true")
    if focus: parts.append("focus=true")
    if mine: parts.append("mine=true")
    if q: parts.append(f"q={q}")
    pagination_query = "&".join(parts)

    # Get unique project names for filter chips
    all_tasks = task_service.list_tasks(include_someday=someday, visible_to_user_id=vis_uid)
    project_names = sorted(set(t.project_name for t in all_tasks if t.project_name))

    return templates.TemplateResponse("tasks.html", {
        **_base_context(request),
        "tasks": tasks_page,
        "current_status": status,
        "current_someday": someday,
        "current_focus": focus,
        "current_mine": mine,
        "current_project": project,
        "search_query": q,
        "project_names": project_names,
        "has_more": has_more,
        "current_page": page,
        "pagination_query": pagination_query,
    })


@router.get("/tasks/new")
def task_new_page(request: Request):
    projects = task_service.list_projects()
    return templates.TemplateResponse("task_new.html", {
        **_base_context(request),
        "projects": projects,
    })


@router.get("/tasks/{task_id}")
def task_detail_page(request: Request, task_id: int):
    task = task_service.get_task(task_id)
    if not task:
        return RedirectResponse("/tasks", status_code=303)
    user = getattr(request.state, "current_user", None)
    # Access check: if task has a project, verify user can access it
    if user and task.project_id and not is_admin_or_owner(user):
        if not can_access_project(user, task.project_id):
            return _forbidden_response(request)
    _can_edit = can_edit_task(user, task.project_id) if user else True
    projects = task_service.list_projects()
    return templates.TemplateResponse("task_detail.html", {
        **_base_context(request),
        "task": task,
        "projects": projects,
        "can_edit": _can_edit,
    })


@router.post("/tasks/add")
def task_add(title: str = Form(...), description: str = Form(""),
             priority: str = Form("medium"), deadline: str = Form(""),
             project_id: str = Form("")):
    data = TaskCreate(
        title=title,
        description=description,
        priority=priority,
        deadline=deadline if deadline else None,
        project_id=int(project_id) if project_id else None,
    )
    task_service.create_task(data, source="web")
    return RedirectResponse("/", status_code=303)


@router.post("/tasks/{task_id}/edit")
def task_edit(request: Request, task_id: int, title: str = Form(...), description: str = Form(""),
              status: str = Form("todo"), priority: str = Form("medium"),
              deadline: str = Form(""), project_id: str = Form(""),
              energy_level: str = Form("medium"), context_note: str = Form(""),
              effort_estimate: str = Form("moderate")):
    user = getattr(request.state, "current_user", None)
    task = task_service.get_task(task_id)
    if user and task and not can_edit_task(user, task.project_id):
        return _forbidden_response(request)
    data = TaskUpdate(
        title=title,
        description=description,
        status=status,
        priority=priority,
        deadline=deadline if deadline else None,
        project_id=int(project_id) if project_id else None,
        energy_level=energy_level,
        context_note=context_note if context_note else None,
        effort_estimate=effort_estimate,
    )
    task_service.update_task(task_id, data, source="web")
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/toggle")
def task_toggle(request: Request, task_id: int):
    user = getattr(request.state, "current_user", None)
    task = task_service.get_task(task_id)
    if user and task and not can_edit_task(user, task.project_id):
        return _forbidden_response(request)
    if task:
        new_status = "todo" if task.status.value == "done" else "done"
        task_service.update_task(task_id, TaskUpdate(status=new_status), source="web")
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/delete")
def task_delete(request: Request, task_id: int):
    user = getattr(request.state, "current_user", None)
    task = task_service.get_task(task_id)
    if user and task and not can_edit_task(user, task.project_id):
        return _forbidden_response(request)
    task_service.delete_task(task_id)
    return RedirectResponse("/tasks", status_code=303)


@router.post("/tasks/{task_id}/shelve")
def task_shelve(task_id: int):
    task_service.shelve_task(task_id)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/unshelve")
def task_unshelve(task_id: int):
    task_service.unshelve_task(task_id)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/focus")
def task_focus(task_id: int):
    task_service.set_focus(task_id)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/tasks/{task_id}/unfocus")
def task_unfocus(task_id: int):
    task_service.clear_focus(task_id)
    return RedirectResponse(f"/tasks/{task_id}", status_code=303)


@router.post("/shutdown")
def page_shutdown():
    task_service.execute_shutdown()
    return RedirectResponse("/", status_code=303)


@router.post("/resume-day")
def page_resume_day():
    task_service.execute_resume()
    return RedirectResponse("/", status_code=303)


@router.post("/energy-mode")
def page_energy_mode(level: str = Form("low")):
    task_service.set_energy_mode(level)
    return RedirectResponse("/", status_code=303)


# ── Inbox page ───────────────────────────────────────────────────────

@router.get("/inbox")
def inbox_page(request: Request):
    return templates.TemplateResponse("email_inbox.html", {**_base_context(request)})


# ── Notes pages ──────────────────────────────────────────────────────

@router.get("/notes")
def notes_page(request: Request, tag: str | None = None):
    uid = _page_user_id(request)
    notes = task_service.list_notes(tag=tag, limit=50, user_id=uid)
    # Get unique tags for filter chips
    all_notes = task_service.list_notes(limit=200, user_id=uid)
    tags = sorted(set(n.tag for n in all_notes if n.tag))
    return templates.TemplateResponse("notes.html", {
        **_base_context(request),
        "notes": notes,
        "tags": tags,
        "current_tag": tag,
    })


@router.post("/notes/add")
def note_add(request: Request, content: str = Form(...), title: str = Form(""), tag: str = Form("")):
    task_service.create_note(NoteCreate(title=title.strip(), content=content, tag=tag.strip()), user_id=_page_user_id(request))
    return RedirectResponse("/notes", status_code=303)


@router.post("/notes/{note_id}/delete")
def note_delete(request: Request, note_id: int):
    task_service.delete_note(note_id, user_id=_page_user_id(request))
    return RedirectResponse("/notes", status_code=303)


# ── Entity pages ─────────────────────────────────────────────────────

@router.get("/entities")
def entities_page(request: Request):
    entities = task_service.list_entities()
    return templates.TemplateResponse("entities.html", {
        **_base_context(request),
        "entities": entities,
    })


@router.get("/entities/{entity_id}")
def entity_detail_page(request: Request, entity_id: int):
    entity = task_service.get_entity(entity_id)
    if not entity:
        return RedirectResponse("/entities", status_code=303)
    tree = task_service.get_entity_tree(entity_id)
    all_contacts = task_service.list_contacts()
    return templates.TemplateResponse("entity_detail.html", {
        **_base_context(request),
        "entity": entity,
        "projects": tree["projects"],
        "people": tree["people"],
        "all_contacts": all_contacts,
    })


@router.post("/entities/add")
def entity_add(name: str = Form(...)):
    task_service.create_entity(EntityCreate(name=name))
    return RedirectResponse("/entities", status_code=303)


@router.post("/entities/{entity_id}/delete")
def entity_delete(entity_id: int):
    task_service.delete_entity(entity_id)
    return RedirectResponse("/entities", status_code=303)


@router.post("/entities/{entity_id}/edit")
def entity_edit(entity_id: int, name: str = Form(...), description: str = Form(""),
                status: str = Form("active"), notes: str = Form("")):
    task_service.update_entity(entity_id, EntityUpdate(
        name=name, description=description, status=status, notes=notes,
    ))
    return RedirectResponse(f"/entities/{entity_id}", status_code=303)


@router.post("/entities/{entity_id}/add-project")
def entity_add_project(entity_id: int, name: str = Form(...),
                       project_type: str = Form("project")):
    task_service.create_project(ProjectCreate(
        name=name, project_type=project_type, entity_id=entity_id,
    ))
    return RedirectResponse(f"/entities/{entity_id}", status_code=303)


@router.post("/entities/{entity_id}/add-person")
def entity_add_person(entity_id: int, contact_id: int = Form(...),
                      title: str = Form(""), is_primary: int = Form(0)):
    task_service.add_contact_entity(ContactEntityCreate(
        contact_id=contact_id, entity_id=entity_id,
        title=title, is_primary=is_primary,
    ))
    return RedirectResponse(f"/entities/{entity_id}", status_code=303)


@router.post("/entities/{entity_id}/remove-person/{ce_id}")
def entity_remove_person(entity_id: int, ce_id: int):
    task_service.remove_contact_entity(ce_id)
    return RedirectResponse(f"/entities/{entity_id}", status_code=303)


# ── Project pages ────────────────────────────────────────────────────

@router.get("/projects")
def projects_page(request: Request, status: str | None = None,
                  type: str | None = None):
    vis_uid = _visibility_user_id(request)
    entities = task_service.list_entities()
    projects = task_service.list_projects(status=status, project_type=type,
                                          visible_to_user_id=vis_uid)

    # Group projects by entity
    grouped: dict[str, list] = {}
    entity_ids: dict[str, int] = {}
    for e in entities:
        entity_ids[e.name] = e.id

    for p in projects:
        key = p.entity_name or "Unaffiliated"
        grouped.setdefault(key, []).append(p)

    # Sort: entities first (alphabetical), unaffiliated last
    ordered = []
    for e in entities:
        if e.name in grouped:
            ordered.append((e.name, grouped[e.name]))
    if "Unaffiliated" in grouped:
        ordered.append(("Unaffiliated", grouped["Unaffiliated"]))

    return templates.TemplateResponse("projects.html", {
        **_base_context(request),
        "grouped_projects": ordered,
        "group_entity_ids": entity_ids,
        "entities": entities,
        "current_status": status,
        "current_type": type,
    })


@router.post("/projects/add")
def project_add(name: str = Form(...), entity_id: str = Form(""),
                project_type: str = Form("project")):
    task_service.create_project(ProjectCreate(
        name=name,
        project_type=project_type,
        entity_id=int(entity_id) if entity_id else None,
    ))
    return RedirectResponse("/projects", status_code=303)


@router.post("/projects/{project_id}/delete")
def project_delete(request: Request, project_id: int):
    user = getattr(request.state, "current_user", None)
    if user and not can_edit_project(user, project_id):
        return _forbidden_response(request)
    task_service.delete_project(project_id)
    return RedirectResponse("/projects", status_code=303)


@router.post("/projects/{project_id}/pause")
def project_pause(request: Request, project_id: int):
    user = getattr(request.state, "current_user", None)
    if user and not can_edit_project(user, project_id):
        return _forbidden_response(request)
    task_service.update_project(project_id, ProjectUpdate(status="paused"))
    return RedirectResponse("/projects", status_code=303)


@router.post("/projects/{project_id}/resume")
def project_resume(request: Request, project_id: int):
    user = getattr(request.state, "current_user", None)
    if user and not can_edit_project(user, project_id):
        return _forbidden_response(request)
    task_service.update_project(project_id, ProjectUpdate(status="active"))
    return RedirectResponse("/projects", status_code=303)


@router.get("/projects/{project_id}")
def project_detail_page(request: Request, project_id: int):
    project = task_service.get_project(project_id)
    if not project:
        return RedirectResponse("/projects", status_code=303)
    user = getattr(request.state, "current_user", None)
    if user and not is_admin_or_owner(user):
        if not can_access_project(user, project_id):
            return _forbidden_response(request)
    _can_edit = can_edit_project(user, project_id) if user else True
    entities = task_service.list_entities()
    all_projects = task_service.list_projects()
    contacts = task_service.list_contacts()
    roles = task_service.list_roles()
    team = task_service.list_project_assignments(project_id=project_id)
    raw_progress = task_service.get_progress(project=project.name)
    by_status = raw_progress.get("by_status", {})
    progress = {
        "todo": by_status.get("todo", 0),
        "in_progress": by_status.get("in_progress", 0),
        "done": by_status.get("done", 0),
        "blocked": by_status.get("blocked", 0),
    }
    recent_tasks = task_service.list_tasks(project=project.name, limit=10)

    # Project members (users table, not RACI contacts)
    from roost.sharing_service import list_project_members, list_users
    members = list_project_members(project_id)
    all_users = list_users() if _can_edit else []

    return templates.TemplateResponse("project_detail.html", {
        **_base_context(request),
        "project": project,
        "entities": entities,
        "all_projects": all_projects,
        "contacts": contacts,
        "roles": roles,
        "team": team,
        "progress": progress,
        "recent_tasks": recent_tasks,
        "can_edit": _can_edit,
        "members": members,
        "all_users": all_users,
    })


@router.post("/projects/{project_id}/edit")
def project_edit(request: Request, project_id: int, name: str = Form(...), description: str = Form(""),
                 entity_id: str = Form(""), project_type: str = Form("project"),
                 status: str = Form("active"), parent_project_id: str = Form("")):
    user = getattr(request.state, "current_user", None)
    if user and not can_edit_project(user, project_id):
        return _forbidden_response(request)
    task_service.update_project(project_id, ProjectUpdate(
        name=name, description=description,
        entity_id=int(entity_id) if entity_id else None,
        project_type=project_type, status=status,
        parent_project_id=int(parent_project_id) if parent_project_id else None,
    ))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/add-member")
def project_add_member(request: Request, project_id: int, contact_id: int = Form(...),
                       role: str = Form("I")):
    user = getattr(request.state, "current_user", None)
    if user and not can_edit_project(user, project_id):
        return _forbidden_response(request)
    task_service.create_project_assignment(ProjectAssignmentCreate(
        contact_id=contact_id, project_id=project_id, role=role,
    ))
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/remove-member/{assignment_id}")
def project_remove_member(request: Request, project_id: int, assignment_id: int):
    user = getattr(request.state, "current_user", None)
    if user and not can_edit_project(user, project_id):
        return _forbidden_response(request)
    task_service.delete_project_assignment(assignment_id)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ── Project Access Members (user login access) ──────────────────────

@router.post("/projects/{project_id}/add-access-member")
def project_add_access_member(request: Request, project_id: int,
                               user_id: int = Form(...), role: str = Form("viewer")):
    """Add a user to project_members (controls visibility + write access)."""
    user = getattr(request.state, "current_user", None)
    if not user or not is_admin_or_owner(user):
        return _forbidden_response(request)
    from roost.sharing_service import add_project_member
    add_project_member(project_id, user_id, role)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


@router.post("/projects/{project_id}/remove-access-member/{member_user_id}")
def project_remove_access_member(request: Request, project_id: int, member_user_id: int):
    """Remove a user from project_members."""
    user = getattr(request.state, "current_user", None)
    if not user or not is_admin_or_owner(user):
        return _forbidden_response(request)
    from roost.sharing_service import remove_project_member
    remove_project_member(project_id, member_user_id)
    return RedirectResponse(f"/projects/{project_id}", status_code=303)


# ── Contact pages ────────────────────────────────────────────────────

@router.get("/contacts")
def contacts_page(request: Request):
    entities = task_service.list_entities()
    contacts = task_service.list_contacts()

    # Group by primary entity
    grouped: dict[str, list] = {}
    entity_ids: dict[str, int] = {}
    for e in entities:
        entity_ids[e.name] = e.id

    for c in contacts:
        key = c.entity_name or "Independent"
        grouped.setdefault(key, []).append(c)

    ordered = []
    for e in entities:
        if e.name in grouped:
            ordered.append((e.name, grouped[e.name]))
    if "Independent" in grouped:
        ordered.append(("Independent", grouped["Independent"]))

    return templates.TemplateResponse("contacts.html", {
        **_base_context(request),
        "grouped_contacts": ordered,
        "group_entity_ids": entity_ids,
        "entities": entities,
    })


@router.get("/contacts/{contact_id}")
def contact_detail_page(request: Request, contact_id: int):
    contact = task_service.get_contact(contact_id)
    if not contact:
        return RedirectResponse("/contacts", status_code=303)
    affiliations = task_service.list_contact_entities(contact_id=contact_id)
    all_asgn = task_service.list_assignments_by_contact(contact_id)
    entities = task_service.list_entities()
    communications = task_service.list_communications(contact_id, limit=50)
    return templates.TemplateResponse("contact_detail.html", {
        **_base_context(request),
        "contact": contact,
        "affiliations": affiliations,
        "project_assignments": all_asgn["project_assignments"],
        "task_assignments": all_asgn["task_assignments"],
        "entities": entities,
        "communications": communications,
    })


@router.post("/contacts/add")
def contact_add(name: str = Form(...), email: str = Form(""),
                entity_id: str = Form("")):
    contact = task_service.create_contact(ContactCreate(name=name, email=email))
    if entity_id:
        task_service.add_contact_entity(ContactEntityCreate(
            contact_id=contact.id, entity_id=int(entity_id), is_primary=1,
        ))
    return RedirectResponse("/contacts", status_code=303)


@router.post("/contacts/{contact_id}/delete")
def contact_delete(contact_id: int):
    task_service.delete_contact(contact_id)
    return RedirectResponse("/contacts", status_code=303)


@router.post("/contacts/{contact_id}/edit")
def contact_edit(contact_id: int, name: str = Form(...), notes: str = Form("")):
    task_service.update_contact(contact_id, ContactUpdate(name=name, notes=notes))
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/add-identifier")
def contact_add_identifier(contact_id: int, ident_type: str = Form(...),
                           ident_value: str = Form(...), ident_label: str = Form(""),
                           ident_primary: int = Form(0)):
    from roost.services.contacts import set_contact_identifier
    set_contact_identifier(contact_id, ident_type, ident_value.strip(),
                           label=ident_label.strip(), is_primary=ident_primary)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/remove-identifier/{ident_id}")
def contact_remove_identifier(contact_id: int, ident_id: int):
    from roost.services.contacts import remove_contact_identifier
    remove_contact_identifier(ident_id)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/add-affiliation")
def contact_add_affiliation(contact_id: int, entity_id: int = Form(...),
                            title: str = Form(""), is_primary: int = Form(0)):
    task_service.add_contact_entity(ContactEntityCreate(
        contact_id=contact_id, entity_id=entity_id,
        title=title, is_primary=is_primary,
    ))
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/remove-affiliation/{ce_id}")
def contact_remove_affiliation(contact_id: int, ce_id: int):
    task_service.remove_contact_entity(ce_id)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/log-communication")
def contact_log_communication(contact_id: int, comm_type: str = Form("note"),
                              subject: str = Form(""), detail: str = Form(""),
                              occurred_at: str = Form("")):
    task_service.log_communication(CommunicationCreate(
        contact_id=contact_id, comm_type=comm_type,
        subject=subject, detail=detail, occurred_at=occurred_at,
    ))
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/delete-communication/{comm_id}")
def contact_delete_communication(contact_id: int, comm_id: int):
    task_service.delete_communication(comm_id)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/sync-emails")
def contact_sync_emails(contact_id: int):
    task_service.sync_contact_emails(contact_id, max_results=20)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


@router.post("/contacts/{contact_id}/sync-meetings")
def contact_sync_meetings(contact_id: int):
    task_service.sync_contact_meetings(contact_id, days=30)
    return RedirectResponse(f"/contacts/{contact_id}", status_code=303)


# ── Calendar page ────────────────────────────────────────────────────

@router.get("/calendar")
def calendar_page(request: Request, year: int | None = None, month: int | None = None):
    from datetime import date, timedelta
    import calendar as cal_mod
    import json

    today = date.today()
    y = year or today.year
    m = month or today.month

    # Compute 42-cell grid bounds (Monday-start, 6 rows)
    first_of_month = date(y, m, 1)
    # Monday = 0 in weekday()
    start_offset = first_of_month.weekday()  # 0=Mon .. 6=Sun
    grid_start = first_of_month - timedelta(days=start_offset)
    grid_end = grid_start + timedelta(days=41)  # 42 cells

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

    # Group events by date, serialise datetimes
    events_by_date: dict[str, list] = {}
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

    # Fetch task deadlines for grid range
    all_deadline_tasks = task_service.list_tasks(
        deadline_filter="has_deadline", order_by="deadline",
    )
    deadlines_by_date: dict[str, list] = {}
    for t in all_deadline_tasks:
        if not t.deadline or t.status.value == "done":
            continue
        try:
            d = t.deadline.date() if hasattr(t.deadline, 'date') else t.deadline
            if isinstance(d, str):
                from datetime import datetime as dt
                d = dt.strptime(d[:10], "%Y-%m-%d").date()
        except Exception:
            continue
        if grid_start <= d <= grid_end:
            date_key = d.isoformat()
            deadlines_by_date.setdefault(date_key, []).append({
                "id": t.id,
                "title": t.title,
                "deadline": t.deadline.isoformat() if hasattr(t.deadline, 'isoformat') else str(t.deadline),
                "priority": t.priority.value,
                "project_name": t.project_name,
            })

    month_name = cal_mod.month_name[m]

    return templates.TemplateResponse("calendar.html", {
        **_base_context(request),
        "year": y,
        "month": m,
        "month_name": month_name,
        "today": today.isoformat(),
        "grid_dates": [d.isoformat() for d in grid_dates],
        "events_by_date": events_by_date,
        "deadlines_by_date": deadlines_by_date,
        "calendar_configured": calendar_configured,
    })


# ── Shared views (no auth) ──────────────────────────────────────────

@router.get("/shared/{token}")
def shared_view(request: Request, token: str):
    from roost.sharing_service import get_shared_view

    data = get_shared_view(token)
    if not data:
        return templates.TemplateResponse("shared_expired.html", {
            **_base_context(request),
        })

    if data.get("project"):
        return templates.TemplateResponse("shared_project.html", {
            **_base_context(request),
            "project": data["project"],
            "tasks": data.get("tasks", []),
            "progress": data.get("progress", {}),
        })

    return templates.TemplateResponse("shared_dashboard.html", {
        **_base_context(request),
        "label": data.get("label", "Shared Dashboard"),
        "triage": data.get("triage", {}),
        "progress": data.get("progress", {}),
        "tasks": data.get("tasks", []),
    })


# ── Integrations page ──────────────────────────────────────────────

def _get_integration_status():
    """Gather status for all integrations."""
    # Gmail
    gmail = {"available": False, "has_token": False, "send_from": ""}
    try:
        from roost.gmail import is_gmail_available
        from roost.gmail.client import get_stored_refresh_token
        from roost.config import GMAIL_SEND_FROM
        gmail["has_token"] = bool(get_stored_refresh_token())
        gmail["available"] = is_gmail_available()
        gmail["send_from"] = GMAIL_SEND_FROM
    except Exception:
        logger.debug("Failed to check Gmail integration status", exc_info=True)

    # Notion
    notion = {"available": False, "pending": 0, "failed": 0, "sync_states": []}
    try:
        from roost.notion import is_notion_available
        notion["available"] = is_notion_available()
        if notion["available"]:
            from roost.database import get_connection
            conn = get_connection()
            notion["pending"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM notion_sync_log WHERE status = 'pending'"
            ).fetchone()["cnt"]
            notion["failed"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM notion_sync_log WHERE status = 'failed'"
            ).fetchone()["cnt"]
            notion["sync_states"] = [
                dict(r) for r in conn.execute(
                    "SELECT table_name, last_synced_at FROM notion_sync_state"
                ).fetchall()
            ]
            conn.close()
    except Exception:
        logger.debug("Failed to check Notion integration status", exc_info=True)

    # Calendar
    calendar = {"configured": False}
    try:
        from roost.calendar_service import _is_calendar_available
        calendar["configured"] = _is_calendar_available()
    except Exception:
        logger.debug("Failed to check calendar integration status", exc_info=True)

    # Dropbox + Otter
    dropbox = {"available": False, "folder": "", "pending": 0, "poll_interval": 120}
    try:
        from roost.dropbox_client import is_dropbox_available
        from roost.config import DROPBOX_OTTER_FOLDER, OTTER_POLL_INTERVAL
        dropbox["available"] = is_dropbox_available()
        dropbox["folder"] = DROPBOX_OTTER_FOLDER
        dropbox["poll_interval"] = OTTER_POLL_INTERVAL
        if dropbox["available"]:
            from roost.database import get_connection
            conn = get_connection()
            dropbox["pending"] = conn.execute(
                "SELECT COUNT(*) as cnt FROM otter_pending WHERE status = 'pending'"
            ).fetchone()["cnt"]
            conn.close()
    except Exception:
        logger.debug("Failed to check Dropbox integration status", exc_info=True)

    # Voice
    voice = {"status": "standby (model not loaded)"}
    try:
        import roost.voice as v
        if v._model is not None:
            voice["status"] = "loaded (in memory)"
    except Exception:
        logger.debug("Failed to check voice integration status", exc_info=True)

    return gmail, notion, calendar, dropbox, voice


@router.get("/integrations")
def integrations_page(request: Request):
    gmail, notion, calendar, dropbox, voice = _get_integration_status()
    return templates.TemplateResponse("integrations.html", {
        **_base_context(request),
        "gmail": gmail,
        "notion": notion,
        "calendar": calendar,
        "dropbox": dropbox,
        "voice": voice,
    })


@router.post("/integrations/gmail/digest")
def gmail_send_digest(request: Request):
    result = ""
    try:
        from roost.gmail.service import send_digest
        from roost.config import GMAIL_SEND_FROM
        ok = send_digest(GMAIL_SEND_FROM)
        result = f"Digest sent to {GMAIL_SEND_FROM}" if ok else "Failed to send digest."
    except Exception as e:
        result = f"Error: {e}"
    gmail, notion, calendar, dropbox, voice = _get_integration_status()
    return templates.TemplateResponse("integrations.html", {
        **_base_context(request),
        "gmail": gmail, "notion": notion, "calendar": calendar,
        "dropbox": dropbox, "voice": voice,
        "gmail_result": result,
    })


@router.post("/integrations/gmail/sync-calendar")
def gmail_sync_calendar(request: Request):
    result = ""
    try:
        from roost.gmail.calendar_write import sync_task_deadlines
        stats = sync_task_deadlines()
        result = f"Calendar sync: {stats['created']} created, {stats['skipped']} skipped, {stats['errors']} errors"
    except Exception as e:
        result = f"Error: {e}"
    gmail, notion, calendar, dropbox, voice = _get_integration_status()
    return templates.TemplateResponse("integrations.html", {
        **_base_context(request),
        "gmail": gmail, "notion": notion, "calendar": calendar,
        "dropbox": dropbox, "voice": voice,
        "gmail_result": result,
    })


@router.post("/integrations/gmail/poll")
def gmail_poll_inbox(request: Request):
    result = ""
    try:
        from roost.gmail.poller import poll_inbox
        created = poll_inbox()
        result = f"Inbox poll: {created} items created."
    except Exception as e:
        result = f"Error: {e}"
    gmail, notion, calendar, dropbox, voice = _get_integration_status()
    return templates.TemplateResponse("integrations.html", {
        **_base_context(request),
        "gmail": gmail, "notion": notion, "calendar": calendar,
        "dropbox": dropbox, "voice": voice,
        "gmail_result": result,
    })


@router.post("/integrations/notion/sync")
def notion_sync_push(request: Request):
    result = ""
    try:
        from roost.notion.sync import push_all
        count = push_all()
        result = f"Pushed {count} items to Notion."
    except Exception as e:
        result = f"Error: {e}"
    gmail, notion, calendar, dropbox, voice = _get_integration_status()
    return templates.TemplateResponse("integrations.html", {
        **_base_context(request),
        "gmail": gmail, "notion": notion, "calendar": calendar,
        "dropbox": dropbox, "voice": voice,
        "notion_result": result,
    })


@router.post("/integrations/notion/export")
def notion_bulk_export(request: Request):
    result = ""
    try:
        from roost.notion.sync import bulk_export_to_notion
        stats = bulk_export_to_notion()
        result = f"Bulk export: {stats.get('pushed', 0)} items pushed to Notion."
    except Exception as e:
        result = f"Error: {e}"
    gmail, notion, calendar, dropbox, voice = _get_integration_status()
    return templates.TemplateResponse("integrations.html", {
        **_base_context(request),
        "gmail": gmail, "notion": notion, "calendar": calendar,
        "dropbox": dropbox, "voice": voice,
        "notion_result": result,
    })


# ── Curriculum Dashboard ────────────────────────────────────────────

# ── Claude Sessions ───────────────────────────────────────────────

@router.get("/sessions")
def sessions_page(request: Request, active: int | None = None):
    from roost import session_service
    sessions = session_service.list_sessions()
    ttyd_status = session_service.get_ttyd_status()
    active_session = None
    if active:
        active_session = session_service.get_session(active)
    return templates.TemplateResponse("sessions.html", {
        **_base_context(request),
        "sessions": sessions,
        "active_session": active_session,
        "ttyd_status": ttyd_status,
    })


@router.post("/sessions/create")
def session_create(request: Request, name: str = Form(...), project_dir: str = Form("/home/dev/projects")):
    from roost import session_service
    from roost.models import ClaudeSessionCreate
    user = getattr(request.state, "current_user", None) or {}
    user_email = user.get("email", "")
    user_id = user.get("user_id")
    data = ClaudeSessionCreate(name=name, project_dir=project_dir)
    session = session_service.create_session(data, user_id=user_id, user_email=user_email)
    return RedirectResponse(f"/sessions?active={session.id}", status_code=303)


@router.post("/sessions/{session_id}/connect")
def session_connect(session_id: int):
    from roost import session_service
    session = session_service.connect_session(session_id)
    if not session:
        return RedirectResponse("/sessions", status_code=303)
    return RedirectResponse(f"/sessions?active={session_id}", status_code=303)


@router.post("/sessions/{session_id}/close")
def session_close(session_id: int):
    from roost import session_service
    session_service.close_session(session_id)
    return RedirectResponse("/sessions", status_code=303)


@router.get("/curriculum")
def curriculum_page(request: Request, curriculum_id: int | None = None):
    # Load available curricula for dropdown
    curricula = task_service.list_curricula()

    # Determine active curriculum
    active_curriculum = None
    cid = curriculum_id
    if cid and curricula:
        active_curriculum = next((c for c in curricula if c.id == cid), None)

    # Use DB-first accessors with optional curriculum_id
    modules_by_phase = get_modules_by_phase(cid)
    phases = get_phase_names(cid)
    progress = task_service.get_progress()

    # Get all curriculum docs grouped by module
    all_docs = task_service.list_curriculum_docs()
    docs_by_module = {}
    for doc in all_docs:
        docs_by_module.setdefault(doc.module_id, []).append(doc)

    total_docs = len(all_docs)
    final_docs = sum(1 for d in all_docs if d.status == "final")

    # Dynamic subtitle
    if active_curriculum:
        subtitle = f"{active_curriculum.name} — {active_curriculum.total_hours} Hours"
    else:
        subtitle = "Enterprise AI Security Architecture — 10 Modules, 400 Hours"

    return templates.TemplateResponse("curriculum.html", {
        **_base_context(request),
        "modules_by_phase": modules_by_phase,
        "phases": phases,
        "progress": progress,
        "docs_by_module": docs_by_module,
        "total_docs": total_docs,
        "final_docs": final_docs,
        "curricula": curricula,
        "active_curriculum": active_curriculum,
        "subtitle": subtitle,
    })


# ── Docs (Markdown Reader) ──────────────────────────────────────


@router.get("/docs")
def docs_page(request: Request, path: str = ""):
    """Desktop markdown file browser — reuses mobile docs helpers."""
    from roost.web.pages_mobile import (
        _DOCS_ROOTS, _safe_resolve, _path_to_root_relative, _build_breadcrumbs,
        _human_size,
    )

    ctx = _base_context(request)

    if not path:
        entries = []
        for root in _DOCS_ROOTS:
            if root.is_dir():
                entries.append({
                    "name": root.name,
                    "is_dir": True,
                    "url": f"/docs?path={root.name}",
                    "size": None,
                })
        return templates.TemplateResponse("docs.html", {
            **ctx,
            "view_mode": "browse",
            "entries": entries,
            "breadcrumbs": [],
            "parent_path": None,
            "page_title": "Docs",
        })

    resolved = _safe_resolve(path)
    if not resolved:
        return RedirectResponse("/docs", status_code=303)

    if resolved.is_dir():
        dirs, files = [], []
        try:
            for child in sorted(resolved.iterdir(), key=lambda p: p.name.lower()):
                if child.name.startswith("."):
                    continue
                rel = _path_to_root_relative(child)
                if child.is_dir():
                    dirs.append({
                        "name": child.name,
                        "is_dir": True,
                        "url": f"/docs?path={rel}",
                        "size": None,
                    })
                elif child.suffix.lower() in (".md", ".json"):
                    files.append({
                        "name": child.name,
                        "is_dir": False,
                        "url": f"/docs?path={rel}",
                        "size": _human_size(child.stat().st_size),
                    })
        except PermissionError:
            pass

        rel_parts = [p for p in path.strip("/").split("/") if p]
        parent = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""

        return templates.TemplateResponse("docs.html", {
            **ctx,
            "view_mode": "browse",
            "entries": dirs + files,
            "breadcrumbs": _build_breadcrumbs(path),
            "parent_path": parent,
            "page_title": resolved.name,
        })

    elif resolved.is_file() and resolved.suffix.lower() in (".md", ".json"):
        content = resolved.read_text(encoding="utf-8", errors="replace")
        rel = _path_to_root_relative(resolved)
        rel_parts = [p for p in path.strip("/").split("/") if p]
        parent = "/".join(rel_parts[:-1]) if len(rel_parts) > 1 else ""

        user = getattr(request.state, "current_user", None)
        can_edit = bool(user and is_admin_or_owner(user))

        # JSON files: wrap in markdown code fence for rendering
        file_type = resolved.suffix.lower().lstrip(".")
        if file_type == "json":
            import json as _json
            try:
                parsed = _json.loads(content)
                content = "```json\n" + _json.dumps(parsed, indent=2, ensure_ascii=False) + "\n```"
            except _json.JSONDecodeError:
                content = "```\n" + content + "\n```"

        return templates.TemplateResponse("docs.html", {
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

    return RedirectResponse("/docs", status_code=303)


# Allowed extensions for the docs file endpoint (images + common assets)
_DOCS_FILE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".pdf",
    ".mp4", ".webm",
}


@router.get("/docs/file")
def docs_file(path: str = ""):
    """Serve image/media files from within DOCS_ROOTS for markdown rendering."""
    from roost.web.pages_mobile import _safe_resolve

    if not path:
        return JSONResponse({"error": "No path"}, status_code=400)

    resolved = _safe_resolve(path)
    if not resolved or not resolved.is_file():
        return JSONResponse({"error": "File not found"}, status_code=404)

    if resolved.suffix.lower() not in _DOCS_FILE_EXTENSIONS:
        return JSONResponse({"error": "File type not allowed"}, status_code=403)

    return FileResponse(resolved)


# ── Settings ─────────────────────────────────────────────────────────

@router.get("/settings")
def settings_page(request: Request):
    """Desktop settings page — integrations, flags, personality."""
    from roost.config_service import get_flags_status, get_integrations_status
    from roost.context import get_preferences

    user = getattr(request.state, "current_user", None)
    user_id = str(user.get("user_id", 1)) if user else "1"
    prefs = get_preferences(user_id)

    return templates.TemplateResponse("settings.html", {
        **_base_context(request),
        "flags": get_flags_status(),
        "integrations": get_integrations_status(),
        "personality": prefs.get("personality", ""),
        "active_tab": "",
        "page_title": "Settings",
    })
