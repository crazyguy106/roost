"""DB layer for nurture cadences, enrollments, and pre-approval rules.

Pure CRUD + a handful of query helpers. No external calls, no scheduling
logic — that lives in `roost.extras.lead_nurture.services.nurture`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from roost.database import get_connection

logger = logging.getLogger("roost.cadences.store")


# ── Cadence definitions ────────────────────────────────────────────────


def _cadence_row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["steps"] = json.loads(d.pop("steps_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["steps"] = []
    d["enabled"] = bool(d.get("enabled", 1))
    return d


def set_cadence(
    *,
    slug: str,
    name: str,
    steps: list[dict],
    description: str = "",
    vertical: str = "generic",
    enabled: bool = True,
    source: str = "library",
    user_id: str = "",
) -> dict:
    """Upsert a cadence by (slug, user_id). Source = 'library' for shipped, 'user' for custom."""
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT id FROM nurture_cadences WHERE slug = ? AND user_id = ?",
            (slug, user_id),
        ).fetchone()
        steps_json = json.dumps(steps or [])
        if existing:
            conn.execute(
                """UPDATE nurture_cadences
                   SET name = ?, description = ?, vertical = ?, steps_json = ?,
                       enabled = ?, source = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (name, description, vertical, steps_json,
                 1 if enabled else 0, source, existing["id"]),
            )
            cid = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO nurture_cadences
                   (slug, name, description, vertical, steps_json, enabled, source, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (slug, name, description, vertical, steps_json,
                 1 if enabled else 0, source, user_id),
            )
            cid = cur.lastrowid
        conn.commit()
        return get_cadence(cid)
    finally:
        conn.close()


def get_cadence(cadence_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nurture_cadences WHERE id = ?", (cadence_id,)
        ).fetchone()
        return _cadence_row_to_dict(row) if row else None
    finally:
        conn.close()


def get_cadence_by_slug(slug: str, *, user_id: str = "") -> dict | None:
    """Look up a cadence by slug — prefer the user's override, fall back to library default."""
    conn = get_connection()
    try:
        # Prefer user override if present.
        if user_id:
            row = conn.execute(
                "SELECT * FROM nurture_cadences WHERE slug = ? AND user_id = ?",
                (slug, user_id),
            ).fetchone()
            if row:
                return _cadence_row_to_dict(row)
        row = conn.execute(
            "SELECT * FROM nurture_cadences WHERE slug = ? AND user_id = ''",
            (slug,),
        ).fetchone()
        return _cadence_row_to_dict(row) if row else None
    finally:
        conn.close()


def list_cadences(
    *, vertical: str = "", enabled_only: bool = True, user_id: str = ""
) -> list[dict]:
    conn = get_connection()
    try:
        q = "SELECT * FROM nurture_cadences WHERE 1=1"
        params: list[Any] = []
        if vertical:
            q += " AND vertical = ?"
            params.append(vertical)
        if enabled_only:
            q += " AND enabled = 1"
        if user_id:
            q += " AND (user_id = ? OR user_id = '')"
            params.append(user_id)
        q += " ORDER BY vertical, slug, user_id DESC"
        rows = conn.execute(q, params).fetchall()
        return [_cadence_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def delete_cadence(cadence_id: int) -> dict:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM nurture_cadences WHERE id = ?", (cadence_id,))
        conn.commit()
        return {"ok": True, "deleted": cadence_id}
    finally:
        conn.close()


# ── Enrollments ────────────────────────────────────────────────────────


def _enrollment_row_to_dict(row) -> dict:
    d = dict(row)
    try:
        d["fields"] = json.loads(d.pop("fields_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        d["fields"] = {}
    return d


def enroll_lead(
    *,
    cadence_slug: str,
    crm_person_id: str = "",
    crm_deal_id: str = "",
    contact_email: str = "",
    contact_phone: str = "",
    contact_name: str = "",
    channel: str = "email",
    fields: dict | None = None,
    source: str = "",
    user_id: str = "",
    next_run_at: str | None = None,
) -> dict:
    """Enroll a lead in a cadence by slug. Returns the enrollment row.

    Raises ValueError if the cadence slug does not exist.
    """
    cadence = get_cadence_by_slug(cadence_slug, user_id=user_id)
    if not cadence:
        raise ValueError(f"cadence not found: {cadence_slug}")

    if next_run_at is None:
        next_run_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO nurture_enrollments
               (cadence_id, cadence_slug, crm_person_id, crm_deal_id,
                contact_email, contact_phone, contact_name, channel,
                fields_json, status, current_step, next_run_at, source, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, ?, ?, ?)""",
            (cadence["id"], cadence_slug, crm_person_id, crm_deal_id,
             contact_email, contact_phone, contact_name, channel,
             json.dumps(fields or {}), next_run_at, source, user_id),
        )
        conn.commit()
        return get_enrollment(cur.lastrowid)
    finally:
        conn.close()


def get_enrollment(enrollment_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nurture_enrollments WHERE id = ?", (enrollment_id,)
        ).fetchone()
        return _enrollment_row_to_dict(row) if row else None
    finally:
        conn.close()


def list_enrollments(
    *,
    status: str = "",
    crm_person_id: str = "",
    cadence_slug: str = "",
    user_id: str = "",
    limit: int = 200,
) -> list[dict]:
    conn = get_connection()
    try:
        q = "SELECT * FROM nurture_enrollments WHERE 1=1"
        params: list[Any] = []
        if status:
            q += " AND status = ?"
            params.append(status)
        if crm_person_id:
            q += " AND crm_person_id = ?"
            params.append(crm_person_id)
        if cadence_slug:
            q += " AND cadence_slug = ?"
            params.append(cadence_slug)
        if user_id:
            q += " AND user_id = ?"
            params.append(user_id)
        q += " ORDER BY next_run_at IS NULL, next_run_at, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        return [_enrollment_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def list_due_enrollments(*, now_utc: str | None = None, limit: int = 100) -> list[dict]:
    """Active enrollments whose next_run_at has elapsed."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT * FROM nurture_enrollments
               WHERE status = 'active'
                 AND next_run_at IS NOT NULL
                 AND next_run_at <= ?
               ORDER BY next_run_at LIMIT ?""",
            (now_utc, limit),
        ).fetchall()
        return [_enrollment_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_enrollment(enrollment_id: int, **fields: Any) -> dict | None:
    if not fields:
        return get_enrollment(enrollment_id)
    if "fields" in fields and isinstance(fields["fields"], dict):
        fields["fields_json"] = json.dumps(fields.pop("fields"))
    sets = ", ".join(f"{k} = ?" for k in fields)
    params = list(fields.values()) + [enrollment_id]
    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE nurture_enrollments SET {sets}, updated_at = datetime('now') "
            f"WHERE id = ?",
            params,
        )
        conn.commit()
        return get_enrollment(enrollment_id)
    finally:
        conn.close()


def pause_enrollment(enrollment_id: int, *, reason: str = "") -> dict | None:
    return update_enrollment(enrollment_id, status="paused", pause_reason=reason)


def resume_enrollment(enrollment_id: int) -> dict | None:
    return update_enrollment(enrollment_id, status="active", pause_reason="")


def _bulk_update_by_deal(
    crm_deal_id: str, *, new_status: str, reason: str
) -> int:
    """Set status+pause_reason on every active enrollment for a deal.
    Returns number of rows touched."""
    if not crm_deal_id:
        return 0
    conn = get_connection()
    try:
        cur = conn.execute(
            """UPDATE nurture_enrollments
                  SET status = ?, pause_reason = ?, updated_at = datetime('now')
                WHERE crm_deal_id = ? AND status IN ('active','paused')""",
            (new_status, reason, crm_deal_id),
        )
        conn.commit()
        return cur.rowcount or 0
    finally:
        conn.close()


def exit_enrollments_by_deal(crm_deal_id: str, *, reason: str = "") -> int:
    """Mark every enrollment for the deal as exited (won/lost/disqualified)."""
    return _bulk_update_by_deal(crm_deal_id, new_status="exited", reason=reason)


def pause_enrollments_by_deal(crm_deal_id: str, *, reason: str = "") -> int:
    """Pause every active enrollment for the deal (human takeover)."""
    return _bulk_update_by_deal(crm_deal_id, new_status="paused", reason=reason)


# ── CRM stage-change router ────────────────────────────────────────────

# Stages that mean "stop nurturing — outcome reached"
_EXIT_STAGES = {
    "won", "closed won", "lost", "closed lost",
    "disqualified", "unqualified", "do not contact",
}

# Stages that mean "human is now driving — pause automation"
_PAUSE_STAGES = {
    "qualified", "meeting booked", "demo scheduled",
    "negotiation", "proposal sent", "in conversation",
}


def handle_stage_change(crm_deal_id: str, new_stage: str) -> dict:
    """Apply nurture policy for an Attio deal stage change.

    Returns {action, applied} where action is one of
    'exited' | 'paused' | 'noop'.
    """
    stage_norm = (new_stage or "").strip().lower()
    if stage_norm in _EXIT_STAGES:
        n = exit_enrollments_by_deal(
            crm_deal_id, reason=f"stage:{new_stage}"
        )
        return {"action": "exited", "applied": n}
    if stage_norm in _PAUSE_STAGES:
        n = pause_enrollments_by_deal(
            crm_deal_id, reason=f"stage:{new_stage}"
        )
        return {"action": "paused", "applied": n}
    return {"action": "noop", "applied": 0}


# ── Pre-approval rules ─────────────────────────────────────────────────


def create_preapproval(
    *,
    cadence_slug: str = "*",
    source: str = "*",
    channel: str = "*",
    vertical: str = "*",
    note: str = "",
    user_id: str = "",
) -> dict:
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO cadence_preapprovals
               (cadence_slug, source, channel, vertical, note, user_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cadence_slug, source, channel, vertical, note, user_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM cadence_preapprovals WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_preapprovals(*, user_id: str = "") -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM cadence_preapprovals WHERE user_id = ? OR user_id = '' "
            "ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def delete_preapproval(preapproval_id: int) -> dict:
    conn = get_connection()
    try:
        conn.execute("DELETE FROM cadence_preapprovals WHERE id = ?", (preapproval_id,))
        conn.commit()
        return {"ok": True, "deleted": preapproval_id}
    finally:
        conn.close()


def match_preapproval(
    *,
    cadence_slug: str,
    source: str,
    channel: str,
    vertical: str = "generic",
    user_id: str = "",
) -> dict | None:
    """Return the most-specific matching preapproval rule, or None.

    Matching: each axis (cadence_slug/source/channel/vertical) accepts '*' as
    wildcard. We score specificity (exact match = 1 per axis) and pick the
    highest-scoring rule that applies.
    """
    rules = list_preapprovals(user_id=user_id)
    best: tuple[int, dict] | None = None
    for r in rules:
        score = 0
        if r["cadence_slug"] != "*":
            if r["cadence_slug"] != cadence_slug:
                continue
            score += 1
        if r["source"] != "*":
            if r["source"] != source:
                continue
            score += 1
        if r["channel"] != "*":
            if r["channel"] != channel:
                continue
            score += 1
        if r["vertical"] != "*":
            if r["vertical"] != vertical:
                continue
            score += 1
        if best is None or score > best[0]:
            best = (score, r)
    return best[1] if best else None
