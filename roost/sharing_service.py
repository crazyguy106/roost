"""Sharing service — users, project members, share links, assignments.

Handles read-only share links (token-based, no auth required),
team membership, and task assignment.
"""

import logging
import secrets
from datetime import datetime
from roost.database import get_connection
from roost.models import User, UserCreate, ShareLink, ShareLinkCreate

logger = logging.getLogger("roost.sharing")


# ── Access Control ──────────────────────────────────────────────────

def is_email_allowed(email: str) -> bool:
    """Check if an email address is allowed to log in.

    Returns True if:
    - No allowlists configured (open access, backward compat)
    - Email is in ALLOWED_EMAILS
    - Email domain is in ALLOWED_DOMAINS
    - User already exists in DB (pre-created by admin)
    """
    from roost.config import ALLOWED_EMAILS, ALLOWED_DOMAINS
    email_lower = email.strip().lower()

    # If no allowlists configured, allow anyone (backward compat for dev VPS)
    if not ALLOWED_EMAILS and not ALLOWED_DOMAINS:
        return True

    # Check exact email match
    if email_lower in ALLOWED_EMAILS:
        return True

    # Check domain match
    domain = email_lower.rsplit("@", 1)[-1] if "@" in email_lower else ""
    if domain and domain in ALLOWED_DOMAINS:
        return True

    # Check if user was pre-created by admin (always allowed even if not in allowlist)
    existing = get_user_by_email(email_lower)
    if existing:
        return True

    return False


# ── Users ────────────────────────────────────────────────────────────

def create_user(data: UserCreate) -> User | None:
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO users (name, email, telegram_id, role) VALUES (?, ?, ?, ?)",
        (data.name, data.email, data.telegram_id, data.role),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()
    return get_user(user_id)


def get_user(user_id: int) -> User | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return User(**dict(row))


def get_user_by_telegram_id(telegram_id: int) -> User | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return User(**dict(row))


def list_users() -> list[User]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM users ORDER BY name").fetchall()
    conn.close()
    return [User(**dict(r)) for r in rows]


def delete_user(user_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_user_by_email(email: str) -> User | None:
    """Case-insensitive email lookup."""
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE LOWER(email) = LOWER(?)", (email.strip(),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return User(**dict(row))


def update_user(user_id: int, name: str | None = None, role: str | None = None) -> User | None:
    """Partial update of a user record."""
    updates = {}
    if name is not None:
        updates["name"] = name
    if role is not None:
        updates["role"] = role
    if not updates:
        return get_user(user_id)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [user_id]
    conn = get_connection()
    conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return get_user(user_id)


class AccessDeniedError(Exception):
    """Raised when an email is not allowed to log in or auto-provision is off."""
    pass


def upsert_user_from_oauth(email: str, name: str) -> User:
    """Find-or-create a user from OAuth login.

    First user gets 'owner' role, subsequent users get 'member'.
    Name is updated on every login.

    Raises AccessDeniedError if:
    - Email not in allowlist and user doesn't already exist
    - User doesn't exist and AUTO_PROVISION is False
    """
    existing = get_user_by_email(email)
    if existing:
        # Update name on every login
        if existing.name != name and name:
            update_user(existing.id, name=name)
            return get_user(existing.id)
        return existing

    # New user — check access control
    if not is_email_allowed(email):
        logger.warning("OAuth login denied: %s not in allowlist", email)
        raise AccessDeniedError(f"{email} is not authorized to access this instance")

    from roost.config import AUTO_PROVISION
    if not AUTO_PROVISION:
        logger.warning("OAuth login denied: %s not pre-created and AUTO_PROVISION=false", email)
        raise AccessDeniedError(
            f"{email} is not a registered user. Ask an admin to create your account."
        )

    # Determine role: first user is owner
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    conn.close()
    role = "owner" if count == 0 else "member"

    user = create_user(UserCreate(name=name, email=email.strip().lower(), role=role))
    logger.info("Auto-provisioned user: %s (id=%d, role=%s)", email, user.id, role)

    # Create starter tasks for the new user
    _create_welcome_tasks(user.id)

    return user


def _create_welcome_tasks(user_id: int) -> None:
    """Create starter tasks for a newly provisioned user."""
    try:
        from roost.services.tasks import create_task
        from roost.models import TaskCreate
        welcome = [
            "Welcome! Your account is set up and ready to use",
            "Try creating a task from the + button or the sidebar",
            "Check your calendar and email from the dashboard",
        ]
        for title in welcome:
            create_task(
                TaskCreate(title=title),
                source="system",
                user_id=user_id,
            )
    except Exception:
        logger.debug("Failed to create welcome tasks for user %d", user_id, exc_info=True)


# ── Project Members ──────────────────────────────────────────────────

def add_project_member(project_id: int, user_id: int, role: str = "viewer") -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO project_members (project_id, user_id, role) VALUES (?, ?, ?)",
            (project_id, user_id, role),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def remove_project_member(project_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM project_members WHERE project_id = ? AND user_id = ?",
        (project_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def list_project_members(project_id: int) -> list[dict]:
    conn = get_connection()
    rows = conn.execute(
        """SELECT u.*, pm.role as member_role
           FROM project_members pm
           JOIN users u ON pm.user_id = u.id
           WHERE pm.project_id = ?
           ORDER BY u.name""",
        (project_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Share Links ──────────────────────────────────────────────────────

def create_share_link(data: ShareLinkCreate) -> ShareLink:
    token = secrets.token_urlsafe(24)
    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO share_links (token, label, scope, scope_id, permissions, expires_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (token, data.label, data.scope, data.scope_id, data.permissions, data.expires_at),
    )
    conn.commit()
    link_id = cur.lastrowid
    conn.close()
    return get_share_link(link_id)


def get_share_link(link_id: int) -> ShareLink | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM share_links WHERE id = ?", (link_id,)).fetchone()
    conn.close()
    if not row:
        return None
    return ShareLink(**dict(row))


def get_share_link_by_token(token: str) -> ShareLink | None:
    conn = get_connection()
    row = conn.execute("SELECT * FROM share_links WHERE token = ?", (token,)).fetchone()
    conn.close()
    if not row:
        return None
    link = ShareLink(**dict(row))

    # Check expiry
    if link.expires_at:
        try:
            expires = datetime.strptime(link.expires_at[:19], "%Y-%m-%d %H:%M:%S")
            if datetime.now() > expires:
                return None  # Expired
        except (ValueError, TypeError):
            pass

    return link


def list_share_links() -> list[ShareLink]:
    conn = get_connection()
    rows = conn.execute("SELECT * FROM share_links ORDER BY created_at DESC").fetchall()
    conn.close()
    return [ShareLink(**dict(r)) for r in rows]


def revoke_share_link(link_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM share_links WHERE id = ?", (link_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Task Assignments ─────────────────────────────────────────────────

def assign_task(task_id: int, user_id: int) -> bool:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO task_assignments (task_id, user_id) VALUES (?, ?)",
            (task_id, user_id),
        )
        conn.execute(
            "UPDATE tasks SET assigned_to = ? WHERE id = ?",
            (user_id, task_id),
        )
        conn.commit()
        conn.close()
        return True
    except Exception:
        conn.close()
        return False


def unassign_task(task_id: int, user_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute(
        "DELETE FROM task_assignments WHERE task_id = ? AND user_id = ?",
        (task_id, user_id),
    )
    conn.execute(
        "UPDATE tasks SET assigned_to = NULL WHERE id = ? AND assigned_to = ?",
        (task_id, user_id),
    )
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def get_shared_view(token: str) -> dict | None:
    """Get data for a shared view based on token.

    Returns None if token is invalid/expired.
    """
    link = get_share_link_by_token(token)
    if not link:
        return None

    from roost import task_service
    from roost.triage import get_today_tasks

    result = {
        "link": link,
        "label": link.label or "Shared Dashboard",
    }

    if link.scope == "project" and link.scope_id:
        project = task_service.get_project(link.scope_id)
        if not project:
            return None
        tasks = task_service.list_tasks(project=project.name)
        active = [t for t in tasks if t.status.value != "done"]
        result["project"] = project
        result["tasks"] = active
        result["progress"] = task_service.get_progress(project=project.name)
    else:
        # All tasks view
        triage = get_today_tasks()
        result["triage"] = triage
        progress = task_service.get_progress()
        result["progress"] = progress

    return result
