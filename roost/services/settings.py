"""User settings service — read, write, and delete key-value settings.

Multi-tenant: settings are scoped by (user_id, key). The user_id is
auto-resolved from the current user context if not explicitly provided.
"""

import logging

from roost.database import get_connection

logger = logging.getLogger("roost.services.settings")

__all__ = ["get_setting", "set_setting", "delete_setting"]


def _resolve_uid(user_id: int | None = None) -> int:
    """Resolve user_id from context if not provided."""
    if user_id is not None:
        return user_id
    from roost.user_context import get_current_user_id
    return get_current_user_id()


def get_setting(key: str, user_id: int | None = None) -> str | None:
    """Read a user setting by key."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    row = conn.execute(
        "SELECT value FROM user_settings WHERE user_id = ? AND key = ?", (uid, key)
    ).fetchone()
    conn.close()
    return row["value"] if row else None


def set_setting(key: str, value: str, user_id: int | None = None) -> None:
    """Upsert a user setting."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    conn.execute(
        """INSERT INTO user_settings (user_id, key, value, updated_at)
           VALUES (?, ?, ?, datetime('now'))
           ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value,
                                                    updated_at = excluded.updated_at""",
        (uid, key, value),
    )
    conn.commit()
    conn.close()


def delete_setting(key: str, user_id: int | None = None) -> None:
    """Delete a user setting."""
    uid = _resolve_uid(user_id)
    conn = get_connection()
    conn.execute("DELETE FROM user_settings WHERE user_id = ? AND key = ?", (uid, key))
    conn.commit()
    conn.close()
