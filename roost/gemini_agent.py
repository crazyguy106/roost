"""Agentic Gemini integration with Roost function calling.

Provides a multi-turn agentic loop where Gemini can invoke tools (file I/O,
task management, curriculum knowledge, search) and receive results back,
enabling substantive feedback and document generation.

Uses the google-genai SDK (replacement for deprecated google-generativeai).
"""

import asyncio
import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types

from roost.config import (
    GEMINI_API_KEY, GEMINI_MODEL,
    UPLOADS_DIR, DOCS_DIR,
)
from roost import task_service
from roost.models import TaskCreate, TaskUpdate, NoteCreate

logger = logging.getLogger("roost.gemini_agent")

# ── Safety: allowed directories for file operations ──────────────────

ALLOWED_DIRS = [
    "/home/dev/projects/",
    "/home/dev/documents/",
    DOCS_DIR,
    UPLOADS_DIR,
]

MAX_TOOL_CALLS_PER_RUN = 20
MAX_ITERATIONS = 15
TOOL_TIMEOUT = 10  # seconds per tool execution
MAX_HISTORY_TURNS = 20


def _path_allowed(path: str) -> bool:
    """Check if a file path is within allowed directories."""
    resolved = os.path.realpath(os.path.expanduser(path))
    return any(resolved.startswith(os.path.realpath(d)) for d in ALLOWED_DIRS)


# ── Tool implementations ─────────────────────────────────────────────

def _tool_read_file(path: str) -> dict[str, Any]:
    """Read file contents. Restricted to allowed directories."""
    if not _path_allowed(path):
        return {"error": f"Access denied: {path} is outside allowed directories"}
    try:
        content = Path(path).read_text(encoding="utf-8")
        if len(content) > 15000:
            content = content[:15000] + "\n\n[...truncated at 15000 chars]"
        return {"path": path, "content": content, "size": len(content)}
    except FileNotFoundError:
        return {"error": f"File not found: {path}"}
    except Exception as e:
        return {"error": str(e)}


def _tool_write_file(path: str, content: str) -> dict[str, Any]:
    """Write content to a file. Restricted to allowed directories."""
    if not _path_allowed(path):
        return {"error": f"Access denied: {path} is outside allowed directories"}
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(content, encoding="utf-8")
        return {"path": path, "bytes_written": len(content.encode("utf-8")), "success": True}
    except Exception as e:
        return {"error": str(e)}


def _tool_list_directory(path: str) -> dict[str, Any]:
    """List files and directories at path."""
    if not _path_allowed(path):
        return {"error": f"Access denied: {path} is outside allowed directories"}
    try:
        p = Path(path)
        if not p.is_dir():
            return {"error": f"Not a directory: {path}"}
        entries = []
        for entry in sorted(p.iterdir()):
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else None,
            })
            if len(entries) >= 100:
                entries.append({"name": "...", "type": "truncated"})
                break
        return {"path": path, "entries": entries, "count": len(entries)}
    except Exception as e:
        return {"error": str(e)}


def _tool_search_files(query: str, path: str = "/home/dev/projects/",
                       glob: str = "") -> dict[str, Any]:
    """Search file contents using grep. Returns matching lines."""
    if not _path_allowed(path):
        return {"error": f"Access denied: {path} is outside allowed directories"}
    try:
        cmd = ["grep", "-r", "-n", "-i", "--include=*.md", "--include=*.py",
               "--include=*.txt", "--include=*.yaml", "--include=*.yml",
               "--include=*.json", "-l", query, path]
        if glob:
            cmd = ["grep", "-r", "-n", "-i", f"--include={glob}", "-l", query, path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TOOL_TIMEOUT)
        files = result.stdout.strip().split("\n") if result.stdout.strip() else []
        files = files[:20]  # Limit results

        # Get context for first few matches
        matches = []
        for f in files[:5]:
            ctx_cmd = ["grep", "-n", "-i", "-m", "3", query, f]
            ctx = subprocess.run(ctx_cmd, capture_output=True, text=True, timeout=5)
            matches.append({"file": f, "lines": ctx.stdout.strip()})

        return {"query": query, "file_count": len(files), "files": files, "matches": matches}
    except subprocess.TimeoutExpired:
        return {"error": "Search timed out"}
    except Exception as e:
        return {"error": str(e)}


def _tool_list_tasks(status: str = "", project: str = "",
                     limit: int = 20) -> dict[str, Any]:
    """List tasks with optional filters."""
    try:
        tasks = task_service.list_tasks(
            status=status or None,
            project=project or None,
            limit=limit,
            exclude_paused_projects=True,
        )
        return {
            "count": len(tasks),
            "tasks": [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status.value,
                    "priority": t.priority.value,
                    "project": t.project_name or "",
                    "deadline": str(t.deadline) if t.deadline else None,
                    "context_note": t.context_note or "",
                }
                for t in tasks
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_get_task(task_id: int) -> dict[str, Any]:
    """Get full task details by ID."""
    try:
        t = task_service.get_task(task_id)
        if not t:
            return {"error": f"Task #{task_id} not found"}
        return {
            "id": t.id,
            "title": t.title,
            "description": t.description or "",
            "status": t.status.value,
            "priority": t.priority.value,
            "project": t.project_name or "",
            "deadline": str(t.deadline) if t.deadline else None,
            "context_note": t.context_note or "",
            "task_type": t.task_type or "",
            "energy_level": t.energy_level or "",
            "subtask_count": t.subtask_count or 0,
            "subtask_done": t.subtask_done or 0,
            "created_at": str(t.created_at),
            "updated_at": str(t.updated_at),
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_create_task(title: str, project: str = "", priority: str = "medium",
                      deadline: str = "", description: str = "") -> dict[str, Any]:
    """Create a new task."""
    try:
        # Resolve project_id from name
        project_id = None
        if project:
            p = task_service.get_project_by_name(project)
            if p:
                project_id = p.id

        dl = None
        if deadline:
            try:
                dl = datetime.fromisoformat(deadline)
            except ValueError:
                pass

        data = TaskCreate(
            title=title,
            description=description,
            priority=priority,
            project_id=project_id,
            deadline=dl,
        )
        task = task_service.create_task(data, source="gemini:tool_use")
        return {"id": task.id, "title": task.title, "status": task.status.value, "created": True}
    except Exception as e:
        return {"error": str(e)}


def _tool_update_task(task_id: int, status: str = "", priority: str = "",
                      context_note: str = "") -> dict[str, Any]:
    """Update an existing task."""
    try:
        updates = {}
        if status:
            updates["status"] = status
        if priority:
            updates["priority"] = priority
        if context_note:
            updates["context_note"] = context_note

        if not updates:
            return {"error": "No updates provided"}

        data = TaskUpdate(**updates)
        task = task_service.update_task(task_id, data, source="gemini:tool_use")
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return {"id": task.id, "title": task.title, "status": task.status.value, "updated": True}
    except Exception as e:
        return {"error": str(e)}


def _tool_create_note(content: str, tag: str = "") -> dict[str, Any]:
    """Create a note."""
    try:
        data = NoteCreate(content=content, tag=tag or None)
        note = task_service.create_note(data, source="gemini:tool_use")
        return {"id": note.id, "content": note.content[:100], "created": True}
    except Exception as e:
        return {"error": str(e)}


def _tool_list_notes(tag: str = "", limit: int = 20) -> dict[str, Any]:
    """List notes with optional tag filter."""
    try:
        notes = task_service.list_notes(tag=tag or None, limit=limit)
        return {
            "count": len(notes),
            "notes": [
                {"id": n.id, "content": n.content[:200], "tag": n.tag or "", "created_at": str(n.created_at)}
                for n in notes
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_get_module(module_id: str, programme: str = "") -> dict[str, Any]:
    """Get module details from curriculum."""
    try:
        from roost.curriculum_context import (
            get_module, get_module_detail_from_docs, PHASE_NAMES,
        )
        mod = get_module(module_id)
        if not mod:
            return {"error": f"Module {module_id} not found"}
        result = dict(mod)
        result["phase_name"] = PHASE_NAMES.get(mod["phase"], "")

        # Try to get detailed notes
        detail = get_module_detail_from_docs(module_id)
        if detail:
            result["detailed_notes"] = detail
        return result
    except Exception as e:
        return {"error": str(e)}


def _tool_list_modules(programme: str = "") -> dict[str, Any]:
    """List all modules in the programme."""
    try:
        from roost.curriculum_context import get_modules, PHASE_NAMES
        mods = get_modules()
        return {
            "count": len(mods),
            "modules": [
                {
                    "id": m["id"],
                    "title": m["title"],
                    "phase": m["phase"],
                    "phase_name": PHASE_NAMES.get(m["phase"], ""),
                    "hours": m["hours"],
                    "core_tsc": m["core_tsc"],
                }
                for m in mods.values()
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_search_curriculum_docs(query: str, module_id: str = "") -> dict[str, Any]:
    """Search generated curriculum documents."""
    try:
        docs = task_service.list_curriculum_docs(module_id=module_id or None)
        matches = []
        query_lower = query.lower()
        for d in docs:
            content_str = (d.title or "") + " " + (d.content or "")
            if query_lower in content_str.lower():
                matches.append({
                    "id": d.id,
                    "module_id": d.module_id,
                    "doc_type": d.doc_type,
                    "title": d.title,
                    "status": d.status,
                    "file_path": d.file_path or "",
                })
        return {"query": query, "count": len(matches), "docs": matches[:10]}
    except Exception as e:
        return {"error": str(e)}


def _tool_get_task_with_context(task_id: int) -> dict[str, Any]:
    """Get a task with related files and curriculum context."""
    try:
        task_data = _tool_get_task(task_id)
        if "error" in task_data:
            return task_data

        result = {"task": task_data, "related_files": [], "curriculum_context": None}

        # Check for module reference in task title/description
        title = task_data.get("title", "") + " " + task_data.get("description", "")
        import re
        mod_match = re.search(r'\bM(\d{1,2})\b', title, re.IGNORECASE)
        if mod_match:
            mod_id = f"M{mod_match.group(1)}"
            result["curriculum_context"] = _tool_get_module(mod_id)

        # Search for related files
        search_terms = task_data.get("title", "").split()[:3]
        if search_terms:
            search_result = _tool_search_files(
                " ".join(search_terms),
                path="/home/dev/projects/",
            )
            if "files" in search_result:
                result["related_files"] = search_result["files"][:5]

        # Get subtasks if it's a milestone
        if task_data.get("subtask_count", 0) > 0:
            subtasks = task_service.list_subtasks(task_id)
            result["subtasks"] = [
                {"id": s.id, "title": s.title, "status": s.status.value}
                for s in subtasks
            ]

        return result
    except Exception as e:
        return {"error": str(e)}


# ── Personal assistant tools ──────────────────────────────────────────

def _tool_get_today_events() -> dict[str, Any]:
    """Get today's calendar events."""
    try:
        from roost.calendar_service import get_today_events
        events = get_today_events()
        return {
            "count": len(events),
            "events": [
                {
                    "summary": e["summary"],
                    "start": str(e["start"]) if e.get("start") else None,
                    "end": str(e["end"]) if e.get("end") else None,
                    "location": e.get("location", ""),
                    "calendar": e.get("calendar", ""),
                }
                for e in events
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_get_week_events(days: int = 7) -> dict[str, Any]:
    """Get calendar events for the next N days."""
    try:
        from roost.calendar_service import get_week_events
        events = get_week_events(days=min(days, 30))
        return {
            "count": len(events),
            "events": [
                {
                    "summary": e["summary"],
                    "start": str(e["start"]) if e.get("start") else None,
                    "end": str(e["end"]) if e.get("end") else None,
                    "location": e.get("location", ""),
                    "calendar": e.get("calendar", ""),
                }
                for e in events
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_search_emails(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search Gmail messages. Uses Gmail query syntax."""
    try:
        from roost.mcp.gmail_helpers import search_messages
        messages = search_messages(query, max_results=min(max_results, 10))
        return {"count": len(messages), "messages": messages}
    except Exception as e:
        return {"error": str(e)}


def _tool_draft_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Draft an email for the user to review. Does NOT send — returns the draft for approval.

    The agent MUST present the draft to the user and wait for explicit approval
    before any email is sent.
    """
    return {
        "draft": {
            "to": to,
            "subject": subject,
            "body": body,
        },
        "status": "draft_created",
        "note": "Present this draft to the user. Do NOT send without explicit approval.",
    }


def _tool_complete_task(task_id: int) -> dict[str, Any]:
    """Mark a task as completed."""
    try:
        task = task_service.complete_task(task_id, source="gemini:agent")
        if not task:
            return {"error": f"Task #{task_id} not found"}
        return {"id": task.id, "title": task.title, "status": "done", "completed": True}
    except Exception as e:
        return {"error": str(e)}


def _tool_list_skills() -> dict[str, Any]:
    """List all installed skills with their metadata."""
    try:
        from pathlib import Path
        from roost.config import PROJECT_ROOT
        import re as _re

        skills_dir = Path(PROJECT_ROOT) / "skills"
        if not skills_dir.exists():
            return {"count": 0, "skills": [], "note": "No skills directory yet. Use /skill to create one."}

        skills = []
        for path in sorted(skills_dir.glob("*.py")):
            content = path.read_text(encoding="utf-8")
            meta = {}
            # Extract SKILL_META fields
            for field in ("name", "description", "trigger"):
                match = _re.search(rf'"{field}"\s*:\s*"([^"]+)"', content)
                if not match:
                    match = _re.search(rf"'{field}'\s*:\s*'([^']+)'", content)
                if match:
                    meta[field] = match.group(1)

            skills.append({
                "file": path.name,
                "name": meta.get("name", path.stem),
                "description": meta.get("description", ""),
                "trigger": meta.get("trigger", ""),
            })

        return {"count": len(skills), "skills": skills}
    except Exception as e:
        return {"error": str(e)}


def _tool_run_skill(skill_name: str, args: str = "") -> dict[str, Any]:
    """Run an installed skill by name. The skill's async run() function is executed.

    Args:
        skill_name: Name of the skill (from SKILL_META) or filename (without .py).
        args: JSON string of arguments to pass to the skill's run() function.
    """
    try:
        import importlib.util
        from pathlib import Path
        from roost.config import PROJECT_ROOT
        import re as _re

        skills_dir = Path(PROJECT_ROOT) / "skills"
        if not skills_dir.exists():
            return {"error": "No skills installed. Use /skill to create one."}

        # Find the skill file — match by SKILL_META name or filename
        target_path = None
        skill_name_lower = skill_name.lower().replace(" ", "_")

        for path in skills_dir.glob("*.py"):
            # Match by filename
            if path.stem == skill_name_lower:
                target_path = path
                break
            # Match by SKILL_META name
            content = path.read_text(encoding="utf-8")
            match = _re.search(r'"name"\s*:\s*"([^"]+)"', content)
            if not match:
                match = _re.search(r"'name'\s*:\s*'([^']+)'", content)
            if match and match.group(1).lower() == skill_name.lower():
                target_path = path
                break
            # Match by trigger
            match = _re.search(r'"trigger"\s*:\s*"([^"]+)"', content)
            if not match:
                match = _re.search(r"'trigger'\s*:\s*'([^']+)'", content)
            if match and match.group(1).lower() == skill_name.lower():
                target_path = path
                break

        if not target_path:
            available = _tool_list_skills()
            skill_list = ", ".join(s["name"] for s in available.get("skills", []))
            return {"error": f"Skill '{skill_name}' not found. Available: {skill_list or 'none'}"}

        # Parse args
        skill_args = {}
        if args:
            try:
                skill_args = json.loads(args)
            except json.JSONDecodeError:
                # Treat as a simple string arg
                skill_args = {"input": args}

        # Load and execute the skill module
        spec = importlib.util.spec_from_file_location(f"roost_skill_{target_path.stem}", target_path)
        if not spec or not spec.loader:
            return {"error": f"Failed to load skill module: {target_path.name}"}

        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        if not hasattr(module, "run"):
            return {"error": f"Skill '{target_path.stem}' has no run() function"}

        # Execute — handle both sync and async run()
        import asyncio
        import inspect

        if inspect.iscoroutinefunction(module.run):
            # Run async function
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're already in an async context — use a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(
                        asyncio.run, module.run(skill_args)
                    ).result(timeout=TOOL_TIMEOUT)
            else:
                result = asyncio.run(module.run(skill_args))
        else:
            result = module.run(skill_args)

        return {
            "skill": target_path.stem,
            "result": str(result) if result else "(no output)",
            "success": True,
        }

    except TimeoutError:
        return {"error": f"Skill '{skill_name}' timed out after {TOOL_TIMEOUT}s"}
    except Exception as e:
        logger.exception("Skill execution error: %s", skill_name)
        return {"error": f"Skill error: {e}"}


def _tool_get_today_briefing() -> dict[str, Any]:
    """Get the daily briefing: today's calendar events, overdue tasks, due today, in-progress tasks."""
    try:
        from roost.calendar_service import get_merged_today
        result = get_merged_today()

        events = result.get("events", [])
        triage = result.get("triage", {})

        return {
            "events": [
                {"summary": e["summary"], "start": str(e.get("start", "")), "location": e.get("location", "")}
                for e in events[:10]
            ],
            "overdue": [
                {"id": t["id"], "title": t["title"], "priority": t.get("priority", "")}
                for t in triage.get("overdue", [])[:5]
            ],
            "due_today": [
                {"id": t["id"], "title": t["title"], "priority": t.get("priority", "")}
                for t in triage.get("due_today", [])[:5]
            ],
            "in_progress": [
                {"id": t["id"], "title": t["title"], "context_note": t.get("context_note", "")}
                for t in triage.get("in_progress", [])[:5]
            ],
            "top_urgent": [
                {"id": t["id"], "title": t["title"]}
                for t in triage.get("top_urgent", [])[:3]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _tool_set_preference(key: str, value: str, _user_id: str = "") -> dict[str, Any]:
    """Save a user preference so the agent remembers it across sessions."""
    from roost.context import set_preference
    set_preference(_user_id, key, value)
    return {"saved": True, "key": key, "value": value}


def _tool_get_preferences(_user_id: str = "") -> dict[str, Any]:
    """Get all saved user preferences."""
    from roost.context import get_preferences
    prefs = get_preferences(_user_id)
    return {"preferences": prefs} if prefs else {"preferences": {}, "note": "No preferences saved yet."}


# ── Tool registry ─────────────────────────────────────────────────────

# Core tools (file, task, note, curriculum)
TOOL_HANDLERS: dict[str, Callable] = {
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
    "list_directory": _tool_list_directory,
    "search_files": _tool_search_files,
    "list_tasks": _tool_list_tasks,
    "get_task": _tool_get_task,
    "create_task": _tool_create_task,
    "update_task": _tool_update_task,
    "create_note": _tool_create_note,
    "list_notes": _tool_list_notes,
    "get_module": _tool_get_module,
    "list_modules": _tool_list_modules,
    "search_curriculum_docs": _tool_search_curriculum_docs,
    "get_task_with_context": _tool_get_task_with_context,
}

# Personal assistant tools (calendar, email, briefing, skills)
AGENT_TOOL_HANDLERS: dict[str, Callable] = {
    "get_today_events": _tool_get_today_events,
    "get_week_events": _tool_get_week_events,
    "search_emails": _tool_search_emails,
    "draft_email": _tool_draft_email,
    "complete_task": _tool_complete_task,
    "get_today_briefing": _tool_get_today_briefing,
    "list_skills": _tool_list_skills,
    "run_skill": _tool_run_skill,
    "set_preference": _tool_set_preference,
    "get_preferences": _tool_get_preferences,
}


def _build_agent_tool_declarations() -> list[types.FunctionDeclaration]:
    """Build function declarations for personal assistant tools."""
    return [
        types.FunctionDeclaration(
            name="get_today_events",
            description="Get today's calendar events. Returns summary, start/end times, location.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="get_week_events",
            description="Get calendar events for the next N days.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "days": types.Schema(type="INTEGER", description="Number of days ahead (default 7, max 30)"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="search_emails",
            description="Search Gmail messages using Gmail query syntax (e.g. 'is:unread', 'from:alice', 'subject:invoice after:2026/01/01').",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="Gmail search query"),
                    "max_results": types.Schema(type="INTEGER", description="Max messages to return (default 5, max 10)"),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="draft_email",
            description="Draft an email for the user to review. Does NOT send — you MUST present the draft and get explicit approval before sending. Never call send directly.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "to": types.Schema(type="STRING", description="Recipient email address"),
                    "subject": types.Schema(type="STRING", description="Email subject"),
                    "body": types.Schema(type="STRING", description="Email body text"),
                },
                required=["to", "subject", "body"],
            ),
        ),
        types.FunctionDeclaration(
            name="complete_task",
            description="Mark a task as completed by its ID.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "task_id": types.Schema(type="INTEGER", description="Task ID to complete"),
                },
                required=["task_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_today_briefing",
            description="Get the daily briefing: today's calendar events, overdue tasks, tasks due today, in-progress work, and suggested focus. Use this when the user asks for their daily overview or morning briefing.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="list_skills",
            description="List all installed custom skills. Shows each skill's name, description, and trigger word. Use this to discover what skills are available before running one.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
        types.FunctionDeclaration(
            name="run_skill",
            description="Run an installed custom skill by name. First use list_skills to see what's available. The skill's run() function is executed with the provided arguments.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "skill_name": types.Schema(type="STRING", description="Skill name, trigger word, or filename (without .py)"),
                    "args": types.Schema(type="STRING", description="JSON string of arguments to pass to the skill (optional)"),
                },
                required=["skill_name"],
            ),
        ),
        types.FunctionDeclaration(
            name="set_preference",
            description="Save a user preference so the agent remembers it across sessions and restarts. Use when the user says 'remember that...', 'I prefer...', 'always...', 'never...'. Examples: key='email_style' value='under 3 sentences', key='tone' value='formal', key='summary_format' value='bullet points'.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "key": types.Schema(type="STRING", description="Preference name (short, descriptive)"),
                    "value": types.Schema(type="STRING", description="Preference value"),
                },
                required=["key", "value"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_preferences",
            description="Get all saved user preferences.",
            parameters=types.Schema(type="OBJECT", properties={}),
        ),
    ]


def _build_tool_declarations(include_agent_tools: bool = False) -> list[types.Tool]:
    """Build Gemini function declarations for all tools.

    Args:
        include_agent_tools: If True, include personal assistant tools (calendar, email, briefing).
    """
    declarations = [
        types.FunctionDeclaration(
            name="read_file",
            description="Read the contents of a file at the given path. Restricted to project and document directories.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "path": types.Schema(type="STRING", description="Absolute file path to read"),
                },
                required=["path"],
            ),
        ),
        types.FunctionDeclaration(
            name="write_file",
            description="Write content to a file. Creates parent directories if needed. Use for saving generated documents.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "path": types.Schema(type="STRING", description="Absolute file path to write to"),
                    "content": types.Schema(type="STRING", description="Content to write to the file"),
                },
                required=["path", "content"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_directory",
            description="List files and subdirectories at a path.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "path": types.Schema(type="STRING", description="Directory path to list"),
                },
                required=["path"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_files",
            description="Search for text content across files using grep. Returns matching files and context lines.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="Text to search for (case-insensitive)"),
                    "path": types.Schema(type="STRING", description="Directory to search in (default: /home/dev/projects/)"),
                    "glob": types.Schema(type="STRING", description="Glob pattern to filter files (e.g. '*.md')"),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_tasks",
            description="List tasks with optional filters. Returns task ID, title, status, priority, project, deadline.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "status": types.Schema(type="STRING", description="Filter by status: todo, in_progress, done, blocked"),
                    "project": types.Schema(type="STRING", description="Filter by project name"),
                    "limit": types.Schema(type="INTEGER", description="Max results (default 20)"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="get_task",
            description="Get full details of a specific task by its ID.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "task_id": types.Schema(type="INTEGER", description="Task ID number"),
                },
                required=["task_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="create_task",
            description="Create a new task with title, optional project, priority, deadline, and description.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "title": types.Schema(type="STRING", description="Task title"),
                    "project": types.Schema(type="STRING", description="Project name to assign to"),
                    "priority": types.Schema(type="STRING", description="Priority: low, medium, high, urgent"),
                    "deadline": types.Schema(type="STRING", description="Deadline in ISO format (YYYY-MM-DD)"),
                    "description": types.Schema(type="STRING", description="Detailed description"),
                },
                required=["title"],
            ),
        ),
        types.FunctionDeclaration(
            name="update_task",
            description="Update an existing task's status, priority, or context note.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "task_id": types.Schema(type="INTEGER", description="Task ID to update"),
                    "status": types.Schema(type="STRING", description="New status: todo, in_progress, done, blocked"),
                    "priority": types.Schema(type="STRING", description="New priority: low, medium, high, urgent"),
                    "context_note": types.Schema(type="STRING", description="Context breadcrumb or note"),
                },
                required=["task_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="create_note",
            description="Create a quick note, optionally tagged for categorisation.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "content": types.Schema(type="STRING", description="Note content"),
                    "tag": types.Schema(type="STRING", description="Optional tag for categorisation"),
                },
                required=["content"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_notes",
            description="List recent notes, optionally filtered by tag.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "tag": types.Schema(type="STRING", description="Filter by tag"),
                    "limit": types.Schema(type="INTEGER", description="Max results (default 20)"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="get_module",
            description="Get curriculum module details: title, topics, hours, signature lab, TSC mapping, detailed notes.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "module_id": types.Schema(type="STRING", description="Module ID (e.g. 'M1', 'M7', 'M10')"),
                    "programme": types.Schema(type="STRING", description="Programme name (default: AI Security Architecture)"),
                },
                required=["module_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="list_modules",
            description="List all modules in the curriculum programme with phase, title, and hours.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "programme": types.Schema(type="STRING", description="Programme name (optional)"),
                },
            ),
        ),
        types.FunctionDeclaration(
            name="search_curriculum_docs",
            description="Search generated curriculum documents (lesson plans, lab guides, assessments) by keyword.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "query": types.Schema(type="STRING", description="Search keyword"),
                    "module_id": types.Schema(type="STRING", description="Filter by module (e.g. 'M3')"),
                },
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_task_with_context",
            description="Get a task with full context: related files, curriculum module details, and subtasks. Use this for reviewing or giving feedback on a task.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "task_id": types.Schema(type="INTEGER", description="Task ID to get context for"),
                },
                required=["task_id"],
            ),
        ),
    ]
    if include_agent_tools:
        declarations.extend(_build_agent_tool_declarations())

    return [types.Tool(functionDeclarations=declarations)]


def _execute_tool(name: str, args: dict[str, Any], user_id: str = "") -> dict[str, Any]:
    """Execute a tool by name with arguments. Logs to command_log."""
    handler = TOOL_HANDLERS.get(name) or AGENT_TOOL_HANDLERS.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}

    try:
        # Inject user_id for tools that accept it (prefixed with _user_id)
        import inspect
        sig = inspect.signature(handler)
        if "_user_id" in sig.parameters:
            args = {**args, "_user_id": user_id}
        result = handler(**args)

        # Audit log
        try:
            task_service.log_command(
                source="gemini:tool_use",
                command=f"{name}({json.dumps(args, default=str)[:500]})",
                output=json.dumps(result, default=str)[:2000],
                user_id=user_id,
            )
        except Exception:
            logger.debug("Failed to log tool call: %s", name)

        return result
    except TypeError as e:
        return {"error": f"Invalid arguments for {name}: {e}"}
    except Exception as e:
        logger.exception("Tool execution error: %s", name)
        return {"error": f"Tool error: {e}"}


# ── Session management ───────────────────────────────────────────────

_sessions: dict[str, list[types.Content]] = {}


def _get_session_key(user_id: str, tag: str = "latest") -> str:
    return f"{user_id}:{tag}"


def _load_session(session_id: str | None) -> list[types.Content]:
    """Load conversation history for a session."""
    if not session_id:
        return []
    history = _sessions.get(session_id, [])
    # Truncate to last N turns
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
        _sessions[session_id] = history
    return list(history)


def _save_session(session_id: str, history: list[types.Content]) -> None:
    """Save conversation history for a session."""
    if not session_id:
        return
    # Keep only the last N turns
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
    _sessions[session_id] = history


# ── GeminiAgent ──────────────────────────────────────────────────────

class GeminiAgent:
    """Agentic Gemini with Roost function calling."""

    def __init__(self, system_prompt: str = "", session_id: str | None = None,
                 include_agent_tools: bool = False):
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model = GEMINI_MODEL
        self.tools = _build_tool_declarations(include_agent_tools=include_agent_tools)
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.history = _load_session(session_id)

    async def run(self, user_prompt: str, user_id: str = "",
                  on_progress: Callable | None = None) -> str:
        """Run the agentic loop. Returns final text response.

        Args:
            user_prompt: The user's message.
            user_id: For audit logging.
            on_progress: Optional async callback(text) for streaming updates.
        """
        # Add user message to history
        self.history.append(types.Content(
            role="user",
            parts=[types.Part.from_text(text=user_prompt)],
        ))

        tool_call_count = 0

        for iteration in range(MAX_ITERATIONS):
            # Call Gemini
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=self.history,
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt or None,
                        tools=self.tools,
                        temperature=0.7,
                        max_output_tokens=8192,
                    ),
                )
            except Exception as e:
                logger.exception("Gemini API error")
                return f"Gemini API error: {e}"

            if not response.candidates:
                return "No response from Gemini (empty candidates)."

            candidate = response.candidates[0]
            if not candidate.content or not candidate.content.parts:
                return "No response from Gemini (empty content)."

            # Add model response to history
            self.history.append(candidate.content)

            # Check for function calls
            function_calls = [
                p for p in candidate.content.parts
                if p.function_call and p.function_call.name
            ]

            if not function_calls:
                # No tool calls — extract text and return
                text_parts = [
                    p.text for p in candidate.content.parts
                    if p.text
                ]
                final_text = "\n".join(text_parts) if text_parts else "(no text response)"

                # Save session
                _save_session(self.session_id, self.history)

                return final_text

            # Execute tool calls
            if tool_call_count + len(function_calls) > MAX_TOOL_CALLS_PER_RUN:
                # Hit the safety limit
                self.history.append(types.Content(
                    role="user",
                    parts=[types.Part.from_text(
                        text="[SYSTEM: Tool call limit reached. Please provide your final answer with the information gathered so far.]"
                    )],
                ))
                continue

            # Progress update
            if on_progress:
                tool_names = [fc.function_call.name for fc in function_calls]
                try:
                    await on_progress(f"Using tools: {', '.join(tool_names)}...")
                except Exception:
                    logger.debug("Progress callback failed during tool execution", exc_info=True)

            # Execute each function call and build response parts
            function_response_parts = []
            for fc_part in function_calls:
                fc = fc_part.function_call
                args = dict(fc.args) if fc.args else {}
                logger.info("Tool call: %s(%s)", fc.name, json.dumps(args, default=str)[:200])

                result = _execute_tool(fc.name, args, user_id=user_id)
                tool_call_count += 1

                function_response_parts.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response=result,
                    )
                )

            # Add tool results to history
            self.history.append(types.Content(
                role="user",
                parts=function_response_parts,
            ))

        # Exhausted iterations
        _save_session(self.session_id, self.history)
        return "Reached maximum iteration limit. Here's what I found so far — please try a more specific request."
