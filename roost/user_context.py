"""User context for multi-tenant MCP server.

Each MCP process serves a single user, identified by the ROOST_USER
environment variable (email or Linux username). The user context is resolved
once at startup and used throughout the process lifetime.

Backward compatible: if ROOST_USER is not set, falls back to the first
owner user in the database (single-user mode).
"""

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("roost.user_context")

_current_user: "UserContext | None" = None


@dataclass
class UserContext:
    user_id: int
    email: str
    name: str
    role: str  # owner, admin, member, viewer


def init_user_context() -> UserContext:
    """Resolve ROOST_USER env var to a UserContext.

    Called once at MCP server startup. Resolution order:
    1. ROOST_USER env var (email address or Linux username)
    2. Fallback: first owner user in the database

    For username resolution, tries: username@DEFAULT_EMAIL_DOMAIN, then exact
    match on the name field (case-insensitive).
    """
    global _current_user
    env_user = os.getenv("ROOST_USER", "").strip()

    from roost.database import get_connection

    conn = get_connection()

    row = None
    if env_user:
        # Try exact email match first
        row = conn.execute(
            "SELECT * FROM users WHERE LOWER(email) = LOWER(?)",
            (env_user,),
        ).fetchone()

        # If it looks like a username (no @), try username@DEFAULT_EMAIL_DOMAIN
        if not row and "@" not in env_user:
            from roost.config import DEFAULT_EMAIL_DOMAIN
            if DEFAULT_EMAIL_DOMAIN:
                row = conn.execute(
                    "SELECT * FROM users WHERE LOWER(email) = LOWER(?)",
                    (f"{env_user}@{DEFAULT_EMAIL_DOMAIN}",),
                ).fetchone()

        if not row:
            logger.warning(
                "ROOST_USER=%s not found in users table, falling back to owner",
                env_user,
            )

    # Fallback: first owner user
    if not row:
        row = conn.execute(
            "SELECT * FROM users WHERE role = 'owner' ORDER BY id LIMIT 1"
        ).fetchone()

    conn.close()

    if not row:
        # No users at all - create a default owner from env or system user
        _current_user = _create_default_owner(env_user)
        return _current_user

    _current_user = UserContext(
        user_id=row["id"],
        email=row["email"],
        name=row["name"],
        role=row["role"],
    )
    logger.info(
        "User context initialized: %s (id=%d, role=%s)",
        _current_user.email,
        _current_user.user_id,
        _current_user.role,
    )
    return _current_user


def _create_default_owner(hint: str = "") -> UserContext:
    """Create a default owner user when the users table is empty.

    Uses ROOST_USER hint or falls back to Linux username.
    """
    import getpass

    from roost.database import get_connection

    linux_user = getpass.getuser()
    email = hint if "@" in hint else f"{hint or linux_user}@localhost"
    name = hint.split("@")[0] if hint else linux_user

    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO users (name, email, role) VALUES (?, ?, 'owner')",
        (name, email.lower()),
    )
    conn.commit()
    user_id = cur.lastrowid
    conn.close()

    logger.info("Created default owner user: %s (id=%d)", email, user_id)
    return UserContext(user_id=user_id, email=email.lower(), name=name, role="owner")


def get_current_user() -> UserContext:
    """Get the current user context. Initializes on first call if needed."""
    if _current_user is None:
        return init_user_context()
    return _current_user


def get_current_user_id() -> int:
    """Shorthand for get_current_user().user_id."""
    return get_current_user().user_id


# is_multi_tenant() removed — was defined but never imported anywhere.
# Multi-tenant check is done directly via config.MULTI_TENANT where needed.
