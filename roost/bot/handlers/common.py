"""Shared helpers and common imports for handler modules."""

import os
import logging
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.executor import (
    run_command, run_command_streaming,
    check_rate_limit, RateLimitError, _truncate_output,
)
from roost.models import TaskCreate, TaskUpdate, NoteCreate, ProjectUpdate
from roost import task_service
from roost.config import UPLOADS_DIR, DOCS_DIR, GMAIL_SEND_FROM
try:
    from roost.curriculum_context import (
        CURRICULUM_SYSTEM_PROMPT,
        build_curriculum_system_prompt,
        build_concept_context,
        build_labguide_prompt,
        build_assessment_prompt,
        get_module, get_modules_by_phase, get_phase_names,
        MODULES, PHASE_NAMES,
    )
except ImportError:
    # curriculum_context module removed — provide safe defaults
    CURRICULUM_SYSTEM_PROMPT = ""
    MODULES: dict = {}
    PHASE_NAMES: dict = {}
    def build_curriculum_system_prompt(*a, **kw): return ""
    def build_concept_context(*a, **kw): return ""
    def build_labguide_prompt(*a, **kw): return ""
    def build_assessment_prompt(*a, **kw): return ""
    def get_module(*a, **kw): return None
    def get_modules_by_phase(*a, **kw): return {}
    def get_phase_names(*a, **kw): return {}

_bot_logger = logging.getLogger("roost.bot.common")

# Cache: telegram_id → user_id (populated on first lookup)
_telegram_user_cache: dict[int, int | None] = {}


def resolve_bot_user_id(update: Update) -> int | None:
    """Resolve a Telegram user to a Roost user_id.

    Looks up the telegram_id in the users table. Returns user_id if found,
    None otherwise. Results are cached for the process lifetime.
    """
    if not update.effective_user:
        return None

    tg_id = update.effective_user.id
    if tg_id in _telegram_user_cache:
        return _telegram_user_cache[tg_id]

    try:
        from roost.database import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (tg_id,)
        ).fetchone()
        conn.close()
        uid = row["id"] if row else None
        _telegram_user_cache[tg_id] = uid
        if uid:
            _bot_logger.debug("Telegram user %d → Roost user %d", tg_id, uid)
        return uid
    except Exception:
        _bot_logger.debug("Failed to resolve telegram user %d", tg_id, exc_info=True)
        return None


# ── Markdown escaping ──────────────────────────────────────────────

def escape_md(text: str) -> str:
    """Escape special Markdown characters for Telegram MarkdownV1."""
    if not text:
        return ""
    for char in ["_", "*", "`", "[", "]"]:
        text = text.replace(char, f"\\{char}")
    return text


# ── Formatting helpers ──────────────────────────────────────────────

def _format_task_line(t: dict, show_context: bool = False) -> str:
    """Format a task dict (from triage) as a single Telegram line.

    Shows sort_order as the display number if available, falls back to #id.
    """
    icon = {"todo": "⬜", "in_progress": "🔵", "done": "✅", "blocked": "🔴"}.get(
        t.get("status", "todo"), "⬜"
    )
    prio = {"urgent": "🔥", "high": "⚡"}.get(t.get("priority", ""), "")
    proj = f" [{t.get('project_name', '')}]" if t.get("project_name") else ""
    pos = t.get("sort_order", 0)
    ref = f"{pos}." if pos else f"#{t['id']}"
    line = f"{icon} {ref} {escape_md(t['title'])}{proj} {prio}"

    if show_context and t.get("context_note"):
        line += f"\n    💬 {escape_md(t['context_note'])}"

    return line


def _format_task_obj(t, show_context: bool = False) -> str:
    """Format a Task model object as a single Telegram line.

    Shows sort_order as the display number if available, falls back to #id.
    """
    icon = {"todo": "⬜", "in_progress": "🔵", "done": "✅", "blocked": "🔴"}.get(
        t.status.value, "⬜"
    )
    prio = {"urgent": "🔥", "high": "⚡"}.get(t.priority.value, "")
    proj = f" [{t.project_name}]" if t.project_name else ""
    pos = getattr(t, "sort_order", 0)
    ref = f"{pos}." if pos else f"#{t.id}"
    line = f"{icon} {ref} {escape_md(t.title)}{proj} {prio}"

    if show_context and t.context_note:
        line += f"\n    💬 {escape_md(t.context_note)}"

    return line


# ── Context builders ─────────────────────────────────────────────────

def _build_context() -> str:
    """Build task + notes summary for AI prompts."""
    parts = []

    tasks = task_service.list_tasks()
    active = [t for t in tasks if t.status.value != "done"]
    if active:
        lines = ["Active tasks:"]
        for t in active[:15]:
            proj = f" [{t.project_name}]" if t.project_name else ""
            prio = f" !{t.priority.value}" if t.priority.value != "medium" else ""
            lines.append(f"  #{t.id} {t.title}{proj}{prio} ({t.status.value})")
        parts.append("\n".join(lines))

    notes = task_service.list_notes(limit=10)
    if notes:
        lines = ["Recent notes:"]
        for n in notes:
            tag = f" [{n.tag}]" if n.tag else ""
            lines.append(f"  - {n.display_title}{tag}")
        parts.append("\n".join(lines))

    if parts:
        return "\n\n".join(parts) + "\n\n"
    return ""


def _save_doc(content: str, filename: str) -> str:
    """Save generated content to a file and return the path."""
    Path(DOCS_DIR).mkdir(parents=True, exist_ok=True)
    # Sanitize filename
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in filename)
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    path = os.path.join(DOCS_DIR, safe_name)
    with open(path, "w") as f:
        f.write(content)
    return path
