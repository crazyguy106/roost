"""Centralized permission checks for web layer.

Uses the `users` table (global role) and `project_members` table
(per-project role: admin/editor/viewer) to determine access.

Global roles: owner, admin, member, viewer
Project member roles: admin, editor, viewer
"""

from roost.database import get_connection


def is_admin_or_owner(user: dict) -> bool:
    """Check if user has global admin or owner role."""
    return user.get("role") in ("admin", "owner")


def get_user_project_ids(user_id: int) -> set[int]:
    """Return set of project IDs where user is a member (any role)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT project_id FROM project_members WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    conn.close()
    return {r["project_id"] for r in rows}


def get_user_project_role(user_id: int, project_id: int) -> str | None:
    """Return user's role in a specific project, or None if not a member."""
    conn = get_connection()
    row = conn.execute(
        "SELECT role FROM project_members WHERE user_id = ? AND project_id = ?",
        (user_id, project_id),
    ).fetchone()
    conn.close()
    return row["role"] if row else None


def can_access_project(user: dict, project_id: int) -> bool:
    """Can user view this project?

    Owner/admin -> always True.
    Member/viewer -> only if in project_members for this project.
    """
    if is_admin_or_owner(user):
        return True
    user_id = user.get("user_id")
    if not user_id:
        return False
    return get_user_project_role(user_id, project_id) is not None


def can_edit_project(user: dict, project_id: int) -> bool:
    """Can user edit this project?

    Owner/admin -> always True.
    Member -> only if project_members role is admin or editor.
    Viewer -> False.
    """
    if is_admin_or_owner(user):
        return True
    user_id = user.get("user_id")
    if not user_id:
        return False
    role = get_user_project_role(user_id, project_id)
    return role in ("admin", "editor")


def can_edit_task(user: dict, task_project_id: int | None) -> bool:
    """Can user edit a task?

    Owner/admin -> always True.
    Unscoped tasks (no project) -> editable by any logged-in user.
    Scoped tasks -> only if user has admin/editor role on the task's project.
    Global viewer -> False for scoped tasks.
    """
    if is_admin_or_owner(user):
        return True
    if user.get("role") == "viewer":
        # Global viewers can only view
        return False
    if task_project_id is None:
        # Unscoped tasks are editable by any non-viewer
        return True
    return can_edit_project(user, task_project_id)
