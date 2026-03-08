"""Project CRUD operations.

Multi-tenant: projects have a user_id (owner). Auto-resolved from current
user context if not explicitly provided.
"""

import logging
from datetime import datetime
from roost.database import get_connection
from roost.models import Project, ProjectCreate, ProjectUpdate

logger = logging.getLogger("roost.services.projects")

__all__ = [
    "create_project",
    "get_project",
    "get_project_by_name",
    "list_projects",
    "list_child_projects",
    "update_project",
    "delete_project",
]


def _resolve_uid(user_id: int | None = None) -> int:
    """Resolve user_id from context if not provided."""
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


def create_project(data: ProjectCreate, source: str = "",
                   user_id: int | None = None) -> Project:
    uid = _resolve_uid(user_id)
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO projects (name, description, category, parent_project_id,
                                project_type, entity_id, user_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (data.name, data.description, data.category, data.parent_project_id,
         data.project_type, data.entity_id, uid),
    )
    conn.commit()
    project = get_project(cur.lastrowid)
    conn.close()

    try:
        from roost.events import emit, PROJECT_CREATED
        emit(PROJECT_CREATED, {"project": project, "source": source})
    except ImportError:
        pass

    return project


def get_project(project_id: int) -> Project | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM tasks WHERE project_id = p.id) as task_count,
                  (SELECT COUNT(*) FROM projects WHERE parent_project_id = p.id) as children_count,
                  (SELECT name FROM projects WHERE id = p.parent_project_id) as parent_project_name,
                  e.name as entity_name
           FROM projects p
           LEFT JOIN entities e ON p.entity_id = e.id
           WHERE p.id = ?""",
        (project_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Project(**dict(row))


def get_project_by_name(name: str) -> Project | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT p.*,
                  (SELECT COUNT(*) FROM tasks WHERE project_id = p.id) as task_count,
                  (SELECT COUNT(*) FROM projects WHERE parent_project_id = p.id) as children_count,
                  (SELECT name FROM projects WHERE id = p.parent_project_id) as parent_project_name,
                  e.name as entity_name
           FROM projects p
           LEFT JOIN entities e ON p.entity_id = e.id
           WHERE p.name = ?""",
        (name,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Project(**dict(row))


def list_projects(
    status: str | None = None,
    category: str | None = None,
    parent_id: int | None = None,
    top_level_only: bool = False,
    project_type: str | None = None,
    entity_id: int | None = None,
    visible_to_user_id: int | None = None,
) -> list[Project]:
    conn = get_connection()
    query = """SELECT p.*,
                      (SELECT COUNT(*) FROM tasks WHERE project_id = p.id) as task_count,
                      (SELECT COUNT(*) FROM projects WHERE parent_project_id = p.id) as children_count,
                      (SELECT name FROM projects WHERE id = p.parent_project_id) as parent_project_name,
                      e.name as entity_name
               FROM projects p
               LEFT JOIN entities e ON p.entity_id = e.id
               WHERE 1=1"""
    params: list = []

    if status:
        query += " AND p.status = ?"
        params.append(status)
    if category:
        query += " AND p.category = ?"
        params.append(category)
    if parent_id is not None:
        query += " AND p.parent_project_id = ?"
        params.append(parent_id)
    if top_level_only:
        query += " AND p.parent_project_id IS NULL"
    if project_type:
        query += " AND p.project_type = ?"
        params.append(project_type)
    if entity_id is not None:
        query += " AND p.entity_id = ?"
        params.append(entity_id)

    # Visibility scoping: non-admin users see only their projects
    if visible_to_user_id is not None:
        query += " AND p.id IN (SELECT pm.project_id FROM project_members pm WHERE pm.user_id = ?)"
        params.append(visible_to_user_id)

    query += " ORDER BY p.pinned DESC, p.name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [Project(**dict(r)) for r in rows]


def list_child_projects(parent_id: int) -> list[Project]:
    """List direct children of a project."""
    return list_projects(parent_id=parent_id)


def update_project(project_id: int, data: ProjectUpdate, source: str = "") -> Project | None:
    """Update project fields."""
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return get_project(project_id)

    # Convert enums
    for key in ("status",):
        if key in updates and hasattr(updates[key], "value"):
            updates[key] = updates[key].value

    # Convert pinned bool to int
    if "pinned" in updates:
        updates["pinned"] = 1 if updates["pinned"] else 0

    updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [project_id]

    conn = get_connection()
    conn.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()

    project = get_project(project_id)

    try:
        from roost.events import emit, PROJECT_UPDATED
        emit(PROJECT_UPDATED, {"project": project, "source": source})
    except ImportError:
        pass

    return project


def delete_project(project_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted
