"""Normalize terse/natural-language inputs to standard values via Gemini Flash Lite.

Accepts shorthand like "tomorrow", "h", "wip", "next tue 2pm" and returns
ISO dates, full enum strings, and resolved IDs. Only calls the LLM when
inputs aren't already in standard format — zero overhead for clean inputs.
"""

import json
import logging
import re
from datetime import datetime

logger = logging.getLogger(__name__)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$")

_ENUMS = {
    "priority": {"low", "medium", "high", "urgent"},
    "status": {"todo", "in_progress", "done", "blocked"},
    "energy_level": {"low", "medium", "high"},
    "effort_estimate": {"light", "moderate", "heavy"},
}

_DATE_FIELDS = {"deadline", "focus_date"}
_DATETIME_FIELDS = {"start", "end", "occurred_at"}

_MODEL = "gemini-flash-lite-latest"


def _needs_normalization(fields: dict) -> dict:
    """Return only fields that need LLM normalization."""
    dirty = {}
    for key, val in fields.items():
        if not val or val is None:
            continue
        val_str = str(val).strip()
        if not val_str:
            continue
        if key in _DATE_FIELDS:
            if not _ISO_DATE.match(val_str) and not _ISO_DATETIME.match(val_str):
                dirty[key] = val_str
        elif key in _DATETIME_FIELDS:
            if not _ISO_DATETIME.match(val_str):
                dirty[key] = val_str
        elif key in _ENUMS:
            if val_str not in _ENUMS[key]:
                dirty[key] = val_str
        elif key == "project_name":
            dirty[key] = val_str
    return dirty


def normalize(fields: dict, project_list: list[dict] | None = None) -> dict:
    """Normalize terse inputs to standard values.

    Args:
        fields: Dict of field_name → raw_value. Only non-standard values
                are sent to the LLM. Standard values pass through unchanged.
        project_list: Optional list of {"id": int, "name": str} for project
                      resolution. Fetched automatically if not provided.

    Returns:
        Dict with same keys as input, values replaced with normalized forms.
        Original values preserved for fields that were already standard.
    """
    dirty = _needs_normalization(fields)
    if not dirty:
        return fields

    # Build context
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    weekday = now.strftime("%A")

    # Project list for resolution
    project_context = ""
    if "project_name" in dirty:
        if project_list is None:
            try:
                from roost.task_service import list_projects
                projects = list_projects()
                project_list = [
                    {"id": p.id, "name": p.name} for p in projects
                ]
            except Exception:
                project_list = []
        if project_list:
            project_context = (
                "\nAvailable projects (pick closest match):\n"
                + "\n".join(f"- id={p['id']}: {p['name']}" for p in project_list[:30])
            )

    # Build field specs
    field_specs = []
    for key, val in dirty.items():
        if key in _DATE_FIELDS:
            field_specs.append(f'"{key}": "{val}" → ISO date YYYY-MM-DD')
        elif key in _DATETIME_FIELDS:
            field_specs.append(f'"{key}": "{val}" → ISO datetime YYYY-MM-DDTHH:MM:SS')
        elif key in _ENUMS:
            valid = ", ".join(sorted(_ENUMS[key]))
            field_specs.append(f'"{key}": "{val}" → one of: {valid}')
        elif key == "project_name":
            field_specs.append(
                f'"project_name": "{val}" → best matching project name from list below'
            )

    prompt = (
        f"You are a field normalizer. Today: {today} ({weekday}). "
        f"Timezone: Asia/Singapore.\n\n"
        f"Normalize these fields. Reply with ONLY valid JSON, no markdown:\n"
        + "\n".join(f"  {s}" for s in field_specs)
        + project_context
        + "\n\nJSON output:"
    )

    try:
        from roost.mcp.tools_gemini import _get_client

        client = _get_client()
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
            config={"temperature": 0.0, "max_output_tokens": 256},
        )
        text = (response.text or "").strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            text = re.sub(r"^```\w*\n?", "", text)
            text = re.sub(r"\n?```$", "", text)

        normalized = json.loads(text)
        logger.info("Normalized %d fields via %s: %s", len(dirty), _MODEL, normalized)
    except Exception as e:
        logger.warning("Normalization failed (using raw values): %s", e)
        return fields

    # Merge normalized values back into original fields
    result = dict(fields)
    for key, val in normalized.items():
        if key in dirty:
            result[key] = val
        elif key == "project_id" and "project_name" in dirty:
            result["project_id"] = val

    return result
