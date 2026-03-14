"""Multi-provider agentic backends — Claude, OpenAI, Ollama.

Each agent class implements the same interface as GeminiAgent:
    agent = XAgent(system_prompt, session_id, include_agent_tools)
    result = await agent.run(user_prompt, user_id, on_progress)

All providers share the same tool registry from gemini_agent.py
(_execute_tool, TOOL_HANDLERS, AGENT_TOOL_HANDLERS).
"""

import json
import logging
from typing import Any, Callable

from roost.gemini_agent import (
    TOOL_HANDLERS, AGENT_TOOL_HANDLERS, _execute_tool,
    MAX_TOOL_CALLS_PER_RUN, MAX_ITERATIONS, MAX_HISTORY_TURNS,
)

logger = logging.getLogger("roost.agents")


# ── Shared tool schema (OpenAI/Claude format) ────────────────────────

def _build_openai_tools(include_agent_tools: bool = False) -> list[dict]:
    """Build tool definitions in OpenAI function-calling format.

    This format is shared by OpenAI, Ollama, and (with minor wrapping) Claude.
    """
    handlers = dict(TOOL_HANDLERS)
    if include_agent_tools:
        handlers.update(AGENT_TOOL_HANDLERS)

    # Tool parameter schemas — matches the Gemini declarations
    TOOL_SCHEMAS: dict[str, dict] = {
        "read_file": {
            "description": "Read the contents of a file at the given path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Absolute file path"}},
                "required": ["path"],
            },
        },
        "write_file": {
            "description": "Write content to a file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute file path"},
                    "content": {"type": "string", "description": "Content to write"},
                },
                "required": ["path", "content"],
            },
        },
        "list_directory": {
            "description": "List files and subdirectories at a path.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path"}},
                "required": ["path"],
            },
        },
        "search_files": {
            "description": "Search file contents using grep.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text to search for"},
                    "path": {"type": "string", "description": "Directory to search in"},
                    "glob": {"type": "string", "description": "Glob pattern filter"},
                },
                "required": ["query"],
            },
        },
        "list_tasks": {
            "description": "List tasks with optional filters.",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "Filter: todo, in_progress, done, blocked"},
                    "project": {"type": "string", "description": "Filter by project name"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
            },
        },
        "get_task": {
            "description": "Get full task details by ID.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "Task ID"}},
                "required": ["task_id"],
            },
        },
        "create_task": {
            "description": "Create a new task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "project": {"type": "string", "description": "Project name"},
                    "priority": {"type": "string", "description": "low, medium, high, urgent"},
                    "deadline": {"type": "string", "description": "ISO date (YYYY-MM-DD)"},
                    "description": {"type": "string", "description": "Description"},
                },
                "required": ["title"],
            },
        },
        "update_task": {
            "description": "Update a task's status, priority, or context note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "integer", "description": "Task ID"},
                    "status": {"type": "string", "description": "New status"},
                    "priority": {"type": "string", "description": "New priority"},
                    "context_note": {"type": "string", "description": "Context note"},
                },
                "required": ["task_id"],
            },
        },
        "create_note": {
            "description": "Create a quick note.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Note content"},
                    "tag": {"type": "string", "description": "Tag for categorisation"},
                },
                "required": ["content"],
            },
        },
        "list_notes": {
            "description": "List recent notes, optionally filtered by tag.",
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "Filter by tag"},
                    "limit": {"type": "integer", "description": "Max results"},
                },
            },
        },
        "get_module": {
            "description": "Get curriculum module details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "module_id": {"type": "string", "description": "Module ID (e.g. M1)"},
                    "programme": {"type": "string", "description": "Programme name"},
                },
                "required": ["module_id"],
            },
        },
        "list_modules": {
            "description": "List all curriculum modules.",
            "parameters": {
                "type": "object",
                "properties": {"programme": {"type": "string"}},
            },
        },
        "search_curriculum_docs": {
            "description": "Search generated curriculum documents by keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search keyword"},
                    "module_id": {"type": "string", "description": "Filter by module"},
                },
                "required": ["query"],
            },
        },
        "get_task_with_context": {
            "description": "Get a task with related files, curriculum context, and subtasks.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "Task ID"}},
                "required": ["task_id"],
            },
        },
        # Agent tools
        "get_today_events": {
            "description": "Get today's calendar events.",
            "parameters": {"type": "object", "properties": {}},
        },
        "get_week_events": {
            "description": "Get calendar events for the next N days.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "Days ahead (default 7, max 30)"}},
            },
        },
        "search_emails": {
            "description": "Search Gmail using Gmail query syntax (e.g. 'is:unread', 'from:alice').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Gmail search query"},
                    "max_results": {"type": "integer", "description": "Max messages (default 5)"},
                },
                "required": ["query"],
            },
        },
        "draft_email": {
            "description": "Draft an email for user review. Does NOT send. Present draft and get explicit approval first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email"},
                    "subject": {"type": "string", "description": "Subject"},
                    "body": {"type": "string", "description": "Body text"},
                },
                "required": ["to", "subject", "body"],
            },
        },
        "complete_task": {
            "description": "Mark a task as completed.",
            "parameters": {
                "type": "object",
                "properties": {"task_id": {"type": "integer", "description": "Task ID"}},
                "required": ["task_id"],
            },
        },
        "get_today_briefing": {
            "description": "Get the daily briefing: calendar, overdue tasks, due today, in-progress, suggested focus.",
            "parameters": {"type": "object", "properties": {}},
        },
        "list_skills": {
            "description": "List all installed custom skills.",
            "parameters": {"type": "object", "properties": {}},
        },
        "run_skill": {
            "description": "Run an installed custom skill by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_name": {"type": "string", "description": "Skill name or trigger word"},
                    "args": {"type": "string", "description": "JSON arguments (optional)"},
                },
                "required": ["skill_name"],
            },
        },
    }

    tools = []
    for name in handlers:
        schema = TOOL_SCHEMAS.get(name)
        if not schema:
            # Fallback for tools without explicit schema
            schema = {
                "description": f"Execute the {name} tool.",
                "parameters": {"type": "object", "properties": {}},
            }
        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        })
    return tools


# ── Session management (shared, provider-agnostic) ───────────────────

_agent_sessions: dict[str, list[dict]] = {}


def _load_agent_session(session_id: str | None) -> list[dict]:
    if not session_id:
        return []
    history = _agent_sessions.get(session_id, [])
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
        _agent_sessions[session_id] = history
    return list(history)


def _save_agent_session(session_id: str | None, history: list[dict]) -> None:
    if not session_id:
        return
    if len(history) > MAX_HISTORY_TURNS * 2:
        history = history[-(MAX_HISTORY_TURNS * 2):]
    _agent_sessions[session_id] = history


# ── OpenAI Agent (ChatGPT + Ollama) ──────────────────────────────────

class OpenAIAgent:
    """Agentic loop using OpenAI-compatible API (ChatGPT, Ollama, any OpenAI-compatible endpoint)."""

    def __init__(self, system_prompt: str = "", session_id: str | None = None,
                 include_agent_tools: bool = False,
                 api_key: str = "", base_url: str = "", model: str = ""):
        try:
            from openai import AsyncOpenAI
        except ImportError:
            raise ImportError("openai package not installed. Run: pip install openai")

        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url

        self.client = AsyncOpenAI(**kwargs)
        self.model = model or "gpt-4o"
        self.tools = _build_openai_tools(include_agent_tools)
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.history = _load_agent_session(session_id)

    async def run(self, user_prompt: str, user_id: str = "",
                  on_progress: Callable | None = None) -> str:
        # Add system prompt if starting fresh
        if not self.history and self.system_prompt:
            self.history.append({"role": "system", "content": self.system_prompt})

        self.history.append({"role": "user", "content": user_prompt})

        tool_call_count = 0

        for iteration in range(MAX_ITERATIONS):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=self.history,
                    tools=self.tools if self.tools else None,
                    temperature=0.7,
                    max_tokens=8192,
                )
            except Exception as e:
                logger.exception("OpenAI API error")
                return f"API error: {e}"

            choice = response.choices[0]
            message = choice.message

            # Add assistant message to history
            msg_dict: dict[str, Any] = {"role": "assistant", "content": message.content or ""}
            if message.tool_calls:
                msg_dict["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in message.tool_calls
                ]
            self.history.append(msg_dict)

            # No tool calls — return text
            if not message.tool_calls:
                _save_agent_session(self.session_id, self.history)
                return message.content or "(no response)"

            # Execute tool calls
            if tool_call_count + len(message.tool_calls) > MAX_TOOL_CALLS_PER_RUN:
                self.history.append({"role": "user", "content": "[SYSTEM: Tool call limit reached. Provide your final answer.]"})
                continue

            if on_progress:
                names = [tc.function.name for tc in message.tool_calls]
                try:
                    await on_progress(f"Using tools: {', '.join(names)}...")
                except Exception:
                    pass

            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                except json.JSONDecodeError:
                    args = {}

                logger.info("Tool call: %s(%s)", tc.function.name, json.dumps(args, default=str)[:200])
                result = _execute_tool(tc.function.name, args, user_id=user_id)
                tool_call_count += 1

                self.history.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, default=str),
                })

        _save_agent_session(self.session_id, self.history)
        return "Reached maximum iteration limit."


# ── Claude Agent (Anthropic) ─────────────────────────────────────────

class ClaudeAgent:
    """Agentic loop using the Anthropic Claude API with tool use."""

    def __init__(self, system_prompt: str = "", session_id: str | None = None,
                 include_agent_tools: bool = False,
                 api_key: str = "", model: str = ""):
        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            raise ImportError("anthropic package not installed. Run: pip install anthropic")

        kwargs: dict[str, Any] = {}
        if api_key:
            kwargs["api_key"] = api_key

        self.client = AsyncAnthropic(**kwargs)
        self.model = model or "claude-sonnet-4-20250514"
        self.tools = self._build_claude_tools(include_agent_tools)
        self.system_prompt = system_prompt
        self.session_id = session_id
        self.history = _load_agent_session(session_id)

    @staticmethod
    def _build_claude_tools(include_agent_tools: bool) -> list[dict]:
        """Convert OpenAI-format tools to Claude's tool format."""
        openai_tools = _build_openai_tools(include_agent_tools)
        claude_tools = []
        for t in openai_tools:
            fn = t["function"]
            claude_tools.append({
                "name": fn["name"],
                "description": fn["description"],
                "input_schema": fn["parameters"],
            })
        return claude_tools

    async def run(self, user_prompt: str, user_id: str = "",
                  on_progress: Callable | None = None) -> str:
        self.history.append({"role": "user", "content": user_prompt})

        tool_call_count = 0

        for iteration in range(MAX_ITERATIONS):
            try:
                response = await self.client.messages.create(
                    model=self.model,
                    system=self.system_prompt or "",
                    messages=self.history,
                    tools=self.tools if self.tools else [],
                    max_tokens=8192,
                    temperature=0.7,
                )
            except Exception as e:
                logger.exception("Claude API error")
                return f"API error: {e}"

            # Build assistant message from content blocks
            assistant_content = []
            text_parts = []
            tool_uses = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    tool_uses.append(block)
                    assistant_content.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            self.history.append({"role": "assistant", "content": assistant_content})

            # No tool use — return text
            if not tool_uses:
                _save_agent_session(self.session_id, self.history)
                return "\n".join(text_parts) if text_parts else "(no response)"

            # Execute tool calls
            if tool_call_count + len(tool_uses) > MAX_TOOL_CALLS_PER_RUN:
                self.history.append({"role": "user", "content": "[SYSTEM: Tool call limit reached. Provide your final answer.]"})
                continue

            if on_progress:
                names = [tu.name for tu in tool_uses]
                try:
                    await on_progress(f"Using tools: {', '.join(names)}...")
                except Exception:
                    pass

            tool_results = []
            for tu in tool_uses:
                args = dict(tu.input) if tu.input else {}
                logger.info("Tool call: %s(%s)", tu.name, json.dumps(args, default=str)[:200])
                result = _execute_tool(tu.name, args, user_id=user_id)
                tool_call_count += 1

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": json.dumps(result, default=str),
                })

            self.history.append({"role": "user", "content": tool_results})

        _save_agent_session(self.session_id, self.history)
        return "Reached maximum iteration limit."
