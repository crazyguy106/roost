"""Bidirectional conversion: Roost models <-> Notion page properties.

Maps local SQLite rows to Notion page property dicts and vice versa.
"""

import logging
from datetime import datetime

logger = logging.getLogger("roost.notion.mappers")


# ── Local -> Notion (push) ────────────────────────────────────────────

def task_to_notion(task) -> dict:
    """Convert a Task model to Notion page properties."""
    props = {
        "Title": {"title": [{"text": {"content": task.title}}]},
        "Status": {"select": {"name": task.status.value if hasattr(task.status, "value") else task.status}},
        "Priority": {"select": {"name": task.priority.value if hasattr(task.priority, "value") else task.priority}},
        "Energy Level": {"select": {"name": task.energy_level or "medium"}},
        "Urgency Score": {"number": task.urgency_score or 0},
        "Local ID": {"number": task.id},
    }

    if task.deadline:
        dl = task.deadline
        if isinstance(dl, str):
            props["Deadline"] = {"date": {"start": dl[:10]}}
        else:
            props["Deadline"] = {"date": {"start": dl.strftime("%Y-%m-%d")}}

    if task.context_note:
        props["Context Note"] = {
            "rich_text": [{"text": {"content": task.context_note[:2000]}}]
        }

    return props


def project_to_notion(project) -> dict:
    """Convert a Project model to Notion page properties."""
    props = {
        "Name": {"title": [{"text": {"content": project.name}}]},
        "Status": {"select": {"name": project.status or "active"}},
        "Pinned": {"checkbox": bool(project.pinned)},
        "Local ID": {"number": project.id},
    }
    if project.category:
        props["Category"] = {
            "rich_text": [{"text": {"content": project.category}}]
        }
    return props


def note_to_notion(note) -> dict:
    """Convert a Note model to Notion page properties."""
    content = note.content[:2000] if note.content else ""
    props = {
        "Content": {"title": [{"text": {"content": content}}]},
        "Local ID": {"number": note.id},
    }
    if note.tag:
        props["Tag"] = {"select": {"name": note.tag}}
    return props


def curriculum_to_notion(curriculum, module_count: int = 0) -> dict:
    """Convert a Curriculum model to Notion page properties for the Programmes DB."""
    # Determine tier from slug — configure per deployment
    tier_map: dict[str, str] = {}
    tier = tier_map.get(curriculum.slug, "Other")

    # Determine provider from slug — configure per deployment
    provider_map: dict[str, str] = {}
    provider = provider_map.get(curriculum.slug, "")

    props = {
        "Name": {"title": [{"text": {"content": curriculum.name}}]},
        "Slug": {"rich_text": [{"text": {"content": curriculum.slug}}]},
        "Tier": {"select": {"name": tier}},
        "Total Hours": {"number": curriculum.total_hours},
        "Module Count": {"number": module_count},
        "Status": {"select": {"name": "active" if curriculum.is_active else "inactive"}},
        "Local ID": {"number": curriculum.id},
    }

    if provider:
        props["Provider"] = {"rich_text": [{"text": {"content": provider}}]}

    if curriculum.description:
        props["Description"] = {
            "rich_text": [{"text": {"content": curriculum.description[:2000]}}]
        }

    return props


def curriculum_module_to_notion(module, programme_notion_id: str | None = None,
                                 phase_name: str = "") -> dict:
    """Convert a CurriculumModule to Notion page properties for the Programme Modules DB."""
    import json

    # Build display title: "M1: OS & Networking Foundations"
    display_title = f"{module.module_id}: {module.title}"

    # Parse topics from JSON string
    try:
        topics_list = json.loads(module.topics) if module.topics else []
    except (ValueError, TypeError):
        topics_list = []
    topics_text = ", ".join(topics_list) if topics_list else ""

    props = {
        "Title": {"title": [{"text": {"content": display_title}}]},
        "Module ID": {"rich_text": [{"text": {"content": module.module_id}}]},
        "Phase": {"number": module.phase},
        "Hours": {"number": module.hours},
        "Sort Order": {"number": module.sort_order},
        "Local ID": {"number": module.id},
    }

    if phase_name:
        props["Phase Name"] = {"rich_text": [{"text": {"content": phase_name}}]}

    if module.core_tsc:
        props["Core TSC"] = {"rich_text": [{"text": {"content": module.core_tsc}}]}

    if topics_text:
        props["Topics"] = {"rich_text": [{"text": {"content": topics_text[:2000]}}]}

    if module.signature_lab:
        props["Signature Lab"] = {
            "rich_text": [{"text": {"content": module.signature_lab[:2000]}}]
        }

    if programme_notion_id:
        props["Programme"] = {
            "relation": [{"id": programme_notion_id}]
        }

    return props


def curriculum_doc_to_notion(doc) -> dict:
    """Convert a CurriculumDoc model to Notion page properties."""
    props = {
        "Title": {"title": [{"text": {"content": doc.title}}]},
        "Module": {"rich_text": [{"text": {"content": doc.module_id}}]},
        "Doc Type": {"select": {"name": doc.doc_type}},
        "Status": {"select": {"name": doc.status}},
        "Local ID": {"number": doc.id},
    }
    if doc.framework:
        props["Framework"] = {
            "rich_text": [{"text": {"content": doc.framework}}]
        }
    return props


# ── Notion -> Local (pull) ────────────────────────────────────────────

def _get_title(props: dict, key: str) -> str:
    """Extract title text from Notion property."""
    title_prop = props.get(key, {})
    title_arr = title_prop.get("title", [])
    if title_arr:
        return title_arr[0].get("text", {}).get("content", "")
    return ""


def _get_select(props: dict, key: str) -> str:
    """Extract select value from Notion property."""
    sel = props.get(key, {}).get("select")
    if sel:
        return sel.get("name", "")
    return ""


def _get_rich_text(props: dict, key: str) -> str:
    """Extract rich text from Notion property."""
    rt = props.get(key, {}).get("rich_text", [])
    if rt:
        return rt[0].get("text", {}).get("content", "")
    return ""


def _get_number(props: dict, key: str) -> int | float | None:
    """Extract number from Notion property."""
    return props.get(key, {}).get("number")


def _get_date(props: dict, key: str) -> str | None:
    """Extract date string from Notion property."""
    date_prop = props.get(key, {}).get("date")
    if date_prop:
        return date_prop.get("start")
    return None


def _get_checkbox(props: dict, key: str) -> bool:
    """Extract checkbox value from Notion property."""
    return props.get(key, {}).get("checkbox", False)


def notion_to_task_updates(props: dict) -> dict:
    """Convert Notion page properties to a task update dict.

    Returns only the fields that can be safely updated locally.
    """
    updates = {}

    title = _get_title(props, "Title")
    if title:
        updates["title"] = title

    status = _get_select(props, "Status")
    if status in ("todo", "in_progress", "done", "blocked"):
        updates["status"] = status

    priority = _get_select(props, "Priority")
    if priority in ("low", "medium", "high", "urgent"):
        updates["priority"] = priority

    energy = _get_select(props, "Energy Level")
    if energy in ("low", "medium", "high"):
        updates["energy_level"] = energy

    context = _get_rich_text(props, "Context Note")
    if context is not None:
        updates["context_note"] = context

    deadline = _get_date(props, "Deadline")
    if deadline:
        updates["deadline"] = deadline

    return updates


def notion_to_project_updates(props: dict) -> dict:
    """Convert Notion properties to project update dict."""
    updates = {}

    name = _get_title(props, "Name")
    if name:
        updates["name"] = name

    status = _get_select(props, "Status")
    if status in ("active", "paused", "archived"):
        updates["status"] = status

    category = _get_rich_text(props, "Category")
    if category is not None:
        updates["category"] = category

    pinned = _get_checkbox(props, "Pinned")
    updates["pinned"] = 1 if pinned else 0

    return updates


def notion_to_note_updates(props: dict) -> dict:
    """Convert Notion properties to note update dict."""
    updates = {}

    content = _get_title(props, "Content")
    if content:
        updates["content"] = content

    tag = _get_select(props, "Tag")
    if tag:
        updates["tag"] = tag

    return updates
