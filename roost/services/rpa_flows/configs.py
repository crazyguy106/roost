"""CRUD for `rpa_flow_configs` — data-driven RPA flow definitions.

A config is a list of steps + OTP-source settings, scoped per (user, portal).
The interpreter (`_interpreter.run_config`) walks the steps to execute the flow.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from roost.database import get_connection

__all__ = [
    "set_config",
    "get_config",
    "list_configs",
    "delete_config",
]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def set_config(
    portal_slug: str,
    *,
    user_id: str = "",
    name: str = "",
    login_url: str = "",
    steps: list[dict] | None = None,
    otp_config: dict | None = None,
    enabled: bool = True,
) -> dict:
    """Create or update a flow config for `(portal_slug, user_id)`."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM rpa_flow_configs WHERE portal_slug = ? AND user_id = ?",
            (portal_slug, user_id),
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE rpa_flow_configs
                   SET name = ?, login_url = ?, steps_json = ?, otp_config_json = ?,
                       enabled = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    name,
                    login_url,
                    json.dumps(steps or []),
                    json.dumps(otp_config or {}),
                    1 if enabled else 0,
                    _now(),
                    existing["id"],
                ),
            )
        else:
            conn.execute(
                """INSERT INTO rpa_flow_configs
                   (portal_slug, name, login_url, steps_json, otp_config_json,
                    user_id, enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    portal_slug,
                    name,
                    login_url,
                    json.dumps(steps or []),
                    json.dumps(otp_config or {}),
                    user_id,
                    1 if enabled else 0,
                    _now(),
                    _now(),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return get_config(portal_slug, user_id=user_id) or {}


def get_config(portal_slug: str, user_id: str = "") -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM rpa_flow_configs WHERE portal_slug = ? AND user_id = ?",
            (portal_slug, user_id),
        ).fetchone()
        if not row:
            return None
        return _row_to_dict(row)
    finally:
        conn.close()


def list_configs(user_id: str = "") -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM rpa_flow_configs WHERE user_id = ? ORDER BY portal_slug",
            (user_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def delete_config(portal_slug: str, user_id: str = "") -> bool:
    conn = get_connection()
    try:
        cur = conn.execute(
            "DELETE FROM rpa_flow_configs WHERE portal_slug = ? AND user_id = ?",
            (portal_slug, user_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("steps_json", "otp_config_json"):
        if isinstance(d.get(k), str):
            try:
                d[k.replace("_json", "")] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                d[k.replace("_json", "")] = [] if k == "steps_json" else {}
            del d[k]
    d["enabled"] = bool(d.get("enabled", 1))
    return d
