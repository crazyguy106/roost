"""Response template service — canned messages in the user's own voice.

Templates are pre-written messages with {{variable}} placeholders.
AI selects the best template based on intent classification.
Variables are filled from extracted fields. Human approves before sending.
"""

import json
import logging
import re

from roost.database import get_connection

logger = logging.getLogger("roost.response_templates")


def create_template(
    *,
    name: str,
    body: str,
    category: str = "general",
    intent_tags: list[str] | None = None,
    subject: str = "",
    channel: str = "any",
    sequence_group: str = "",
    sequence_day: int = 0,
    user_id: str = "",
) -> dict:
    """Create a new response template."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO response_templates
               (name, category, intent_tags, subject, body, channel,
                sequence_group, sequence_day, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, category, json.dumps(intent_tags or []), subject, body,
             channel, sequence_group, sequence_day, user_id),
        )
        conn.commit()
        return get_template(cur.lastrowid)
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return {"error": f"Template '{name}' already exists"}
        raise
    finally:
        conn.close()


def get_template(template_id: int) -> dict:
    """Get a template by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM response_templates WHERE id = ?", (template_id,)
        ).fetchone()
        if not row:
            return {"error": f"Template {template_id} not found"}
        return _row_to_dict(row)
    finally:
        conn.close()


def get_template_by_name(name: str) -> dict:
    """Get a template by name."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM response_templates WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return {"error": f"Template '{name}' not found"}
        return _row_to_dict(row)
    finally:
        conn.close()


def list_templates(
    category: str = "",
    channel: str = "",
    active_only: bool = True,
    sequence_group: str = "",
    user_id: str = "",
) -> list[dict]:
    """List templates with optional filters."""
    conn = get_connection()
    try:
        query = "SELECT * FROM response_templates WHERE 1=1"
        params: list = []

        if active_only:
            query += " AND is_active = 1"
        if category:
            query += " AND category = ?"
            params.append(category)
        if channel:
            query += " AND (channel = ? OR channel = 'any')"
            params.append(channel)
        if sequence_group:
            query += " AND sequence_group = ?"
            params.append(sequence_group)

        query += " ORDER BY category, sequence_group, sequence_day, name"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_template(template_id: int, **kwargs) -> dict:
    """Update a template."""
    conn = get_connection()
    try:
        updates = []
        params = []
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "intent_tags" and isinstance(value, list):
                value = json.dumps(value)
            if key == "is_active" and isinstance(value, bool):
                value = 1 if value else 0
            updates.append(f"{key} = ?")
            params.append(value)

        if not updates:
            return get_template(template_id)

        updates.append("updated_at = datetime('now')")
        params.append(template_id)

        conn.execute(
            f"UPDATE response_templates SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return get_template(template_id)
    finally:
        conn.close()


def delete_template(template_id: int) -> dict:
    """Delete a template."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM response_templates WHERE id = ?", (template_id,))
        conn.commit()
        return {"ok": True, "deleted": template_id}
    finally:
        conn.close()


def increment_usage(template_id: int) -> None:
    """Bump usage_count for a template."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE response_templates SET usage_count = usage_count + 1 WHERE id = ?",
            (template_id,),
        )
        conn.commit()
    finally:
        conn.close()


def fill_template(body: str, fields: dict) -> str:
    """Replace {{variable}} placeholders with extracted fields.

    Unfilled variables are cleaned up (removed).
    """
    result = body
    for key, value in fields.items():
        result = result.replace(f"{{{{{key}}}}}", str(value) if value else "")
    # Clean up any unfilled placeholders
    result = re.sub(r"\{\{[^}]+\}\}", "", result)
    return result.strip()


def select_template(
    intent: str,
    urgency: str,
    channel: str = "any",
    templates: list[dict] | None = None,
) -> dict | None:
    """Select the best matching template for a classified intent.

    Matching priority:
    1. Exact intent_tag match + channel match
    2. Exact intent_tag match + channel 'any'
    3. Category match (intent maps to category)
    4. None (no match found)
    """
    if templates is None:
        templates = list_templates(channel=channel, active_only=True)

    # Score each template
    scored = []
    for t in templates:
        score = 0
        tags = t.get("intent_tags", [])

        # Intent tag match
        if intent in tags:
            score += 10
        # Category match (intent often maps to category name)
        if t["category"] == _intent_to_category(intent):
            score += 5
        # Channel match
        if t["channel"] == channel:
            score += 2
        elif t["channel"] == "any":
            score += 1
        # Urgency hint: greeting templates for cold, closing for hot
        if urgency == "hot" and t["category"] == "closing":
            score += 3
        elif urgency == "cold" and t["category"] in ("greeting", "nurture"):
            score += 2

        if score > 0:
            scored.append((score, t))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


def _intent_to_category(intent: str) -> str:
    """Map intent classification to template category."""
    mapping = {
        "buying_enquiry": "qualification",
        "rental_enquiry": "qualification",
        "policy_review": "qualification",
        "investment_interest": "qualification",
        "insurance_enquiry": "qualification",
        "viewing_request": "qualification",
        "pricing_enquiry": "qualification",
        "follow_up": "nurture",
        "general": "greeting",
        "unknown": "greeting",
    }
    return mapping.get(intent, "general")


def _row_to_dict(row) -> dict:
    """Convert a database row to a template dict."""
    d = dict(row)
    # Parse JSON fields
    if "intent_tags" in d:
        try:
            d["intent_tags"] = json.loads(d["intent_tags"])
        except (json.JSONDecodeError, TypeError):
            d["intent_tags"] = []
    if "is_active" in d:
        d["is_active"] = bool(d["is_active"])
    return d
