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


# ── Tool registry ─────────────────────────────────────────────────────

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


def _build_tool_declarations() -> list[types.Tool]:
    """Build Gemini function declarations for all tools."""
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
    return [types.Tool(functionDeclarations=declarations)]


def _execute_tool(name: str, args: dict[str, Any], user_id: str = "") -> dict[str, Any]:
    """Execute a tool by name with arguments. Logs to command_log."""
    handler = TOOL_HANDLERS.get(name)
    if not handler:
        return {"error": f"Unknown tool: {name}"}

    try:
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

    def __init__(self, system_prompt: str = "", session_id: str | None = None):
        self.client = genai.Client(api_key=GEMINI_API_KEY)
        self.model = GEMINI_MODEL
        self.tools = _build_tool_declarations()
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
