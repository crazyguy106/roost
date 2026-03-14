"""Skill Builder — AI-generated skills from natural language descriptions.

Instead of downloading skills from a marketplace (supply chain risk),
the user describes what they want and the AI generates a clean skill.

Usage:
    /skill <description>   — start building a skill
    /skill list            — list installed skills
    /skill delete <name>   — remove a skill

Session flow: describe -> AI generates -> user reviews (Approve/Revise/Cancel)
"""

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.executor import run_command, check_rate_limit, RateLimitError
from roost.config import AGENT_ENABLED, AGENT_PROVIDER, AGENT_TIMEOUT, PROJECT_ROOT

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(PROJECT_ROOT) / "skills"

# ── Session state ────────────────────────────────────────────────────

@dataclass
class SkillSession:
    """Tracks a skill building conversation."""
    user_id: int
    chat_id: int
    description: str
    state: str = "generating"  # generating | reviewing | revising
    generated_code: str = ""
    skill_name: str = ""
    revision_prompt: str = ""
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    timeout_seconds: int = 600  # 10 minutes


_sessions: dict[int, SkillSession] = {}


def get_session(user_id: int) -> SkillSession | None:
    session = _sessions.get(user_id)
    if session is None:
        return None
    if time.time() - session.last_activity > session.timeout_seconds:
        _sessions.pop(user_id, None)
        return None
    return session


def _touch(user_id: int) -> None:
    s = _sessions.get(user_id)
    if s:
        s.last_activity = time.time()


# ── Skill generation prompt ──────────────────────────────────────────

SKILL_SYSTEM_PROMPT = """You are a skill generator for a personal AI agent platform called Roost.

A "skill" is a Python file that defines a single automation. It will be loaded by the agent
and triggered via Telegram command or natural language.

Generate a complete, working Python skill file with:
1. A module docstring explaining what it does
2. A SKILL_META dict with: name, description, trigger (command word), version
3. A main async function `run(args: dict) -> str` that performs the automation
4. Error handling for common failures
5. No external dependencies beyond the Python standard library and the `roost` package

The skill MUST be safe:
- No shell injection (never pass user input to os.system or subprocess with shell=True)
- No file access outside the Roost data directory
- No network requests to hardcoded external URLs
- No credential storage in the skill file

Respond with ONLY the Python code block. No explanation before or after.
"""


def _build_generation_prompt(description: str) -> str:
    return f"{SKILL_SYSTEM_PROMPT}\n\nUser's request: {description}"


def _build_revision_prompt(code: str, feedback: str) -> str:
    return (
        f"{SKILL_SYSTEM_PROMPT}\n\n"
        f"Here is the current skill code:\n```python\n{code}\n```\n\n"
        f"User's revision request: {feedback}\n\n"
        f"Respond with the complete revised Python code block only."
    )


# ── Code extraction ──────────────────────────────────────────────────

def _extract_python_code(text: str) -> str:
    """Extract Python code from markdown code blocks or raw text."""
    # Try ```python ... ``` first
    match = re.search(r'```(?:python)?\s*\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try ``` ... ``` without language tag
    match = re.search(r'```\s*\n(.*?)```', text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Fallback: return as-is if it looks like Python
    if "def " in text or "import " in text:
        return text.strip()
    return text.strip()


def _extract_skill_name(code: str) -> str:
    """Try to extract skill name from SKILL_META dict in code."""
    match = re.search(r'"name"\s*:\s*"([^"]+)"', code)
    if match:
        return match.group(1)
    match = re.search(r"'name'\s*:\s*'([^']+)'", code)
    if match:
        return match.group(1)
    return "unnamed_skill"


def _sanitize_filename(name: str) -> str:
    """Convert skill name to a safe filename."""
    safe = re.sub(r'[^a-z0-9_]', '_', name.lower())
    safe = re.sub(r'_+', '_', safe).strip('_')
    return safe or "skill"


# ── CLI interaction ──────────────────────────────────────────────────

async def _generate_with_cli(prompt: str, user_id: int) -> str:
    """Run the configured AI CLI with a generation prompt."""
    from roost.bot.handlers.agent import _get_provider_cmd

    result = _get_provider_cmd(prompt)
    if result is None:
        return f"Error: AI provider '{AGENT_PROVIDER}' not available."

    cmd, _ = result
    output = await run_command(cmd, timeout=AGENT_TIMEOUT,
                                source="skill-builder", user_id=str(user_id))
    return output


# ── Command handler ──────────────────────────────────────────────────

@authorized
async def cmd_skill(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/skill <description> — build a custom skill with AI."""
    if not AGENT_ENABLED:
        await update.message.reply_text("Agent is disabled. Set AGENT_ENABLED=true in .env")
        return

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Skill Builder*\n\n"
            "`/skill <description>` — build a new skill\n"
            "`/skill list` — list installed skills\n"
            "`/skill delete <name>` — remove a skill\n\n"
            "Example: `/skill summarise my unread emails every morning`",
            parse_mode="Markdown",
        )
        return

    subcommand = args[0].lower()

    # /skill list
    if subcommand == "list":
        await _list_skills(update)
        return

    # /skill delete <name>
    if subcommand == "delete":
        if len(args) < 2:
            await update.message.reply_text("Usage: `/skill delete <name>`", parse_mode="Markdown")
            return
        await _delete_skill(update, args[1])
        return

    # /skill <description> — start building
    description = " ".join(args)
    user_id = update.effective_user.id

    try:
        check_rate_limit(user_id)
    except RateLimitError as e:
        await update.message.reply_text(f"Slow down! Try again in {e.wait_seconds}s.")
        return

    # Create session
    session = SkillSession(
        user_id=user_id,
        chat_id=update.effective_chat.id,
        description=description,
    )
    _sessions[user_id] = session

    status_msg = await update.message.reply_text(
        f"Building skill: _{description}_\n\nGenerating code...",
        parse_mode="Markdown",
    )

    # Generate
    prompt = _build_generation_prompt(description)
    output = await _generate_with_cli(prompt, user_id)

    code = _extract_python_code(output)
    skill_name = _extract_skill_name(code)

    session.generated_code = code
    session.skill_name = skill_name
    session.state = "reviewing"
    _touch(user_id)

    # Show for review
    preview = code[:3000] if len(code) > 3000 else code
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data="skill:approve"),
            InlineKeyboardButton("Revise", callback_data="skill:revise"),
            InlineKeyboardButton("Cancel", callback_data="skill:cancel"),
        ]
    ])

    await status_msg.edit_text(
        f"*Skill: {skill_name}*\n\n```python\n{preview}\n```\n\n"
        f"Approve to install, Revise to modify, or Cancel.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ── Revision message handler ─────────────────────────────────────────

async def handle_skill_revision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Handle revision text when a skill session is in 'revising' state.

    Called from the agent message handler before CLI passthrough.
    Returns True if this message was consumed (skill revision), False otherwise.
    """
    if not update.message or not update.message.text:
        return False

    user_id = update.effective_user.id
    session = get_session(user_id)
    if session is None or session.state != "revising":
        return False

    feedback = update.message.text.strip()
    if not feedback:
        return False

    try:
        check_rate_limit(user_id)
    except RateLimitError as e:
        await update.message.reply_text(f"Slow down! Try again in {e.wait_seconds}s.")
        return True

    _touch(user_id)
    session.state = "generating"

    status_msg = await update.message.reply_text("Revising skill...")

    prompt = _build_revision_prompt(session.generated_code, feedback)
    output = await _generate_with_cli(prompt, user_id)

    code = _extract_python_code(output)
    skill_name = _extract_skill_name(code)

    session.generated_code = code
    session.skill_name = skill_name
    session.state = "reviewing"

    preview = code[:3000] if len(code) > 3000 else code
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data="skill:approve"),
            InlineKeyboardButton("Revise", callback_data="skill:revise"),
            InlineKeyboardButton("Cancel", callback_data="skill:cancel"),
        ]
    ])

    await status_msg.edit_text(
        f"*Skill: {skill_name}* (revised)\n\n```python\n{preview}\n```\n\n"
        f"Approve to install, Revise to modify, or Cancel.",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return True


# ── Callback handler ─────────────────────────────────────────────────

async def handle_skill_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle skill:approve, skill:revise, skill:cancel callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    user_id = update.effective_user.id

    session = get_session(user_id)
    if session is None:
        await query.edit_message_text("Skill session expired. Start again with /skill")
        return

    if action == "approve":
        # Save the skill
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        filename = _sanitize_filename(session.skill_name) + ".py"
        filepath = SKILLS_DIR / filename

        filepath.write_text(session.generated_code, encoding="utf-8")
        _sessions.pop(user_id, None)

        await query.edit_message_text(
            f"Skill *{session.skill_name}* installed!\n\n"
            f"Saved to `skills/{filename}`\n"
            f"It will be available on next agent restart.",
            parse_mode="Markdown",
        )

    elif action == "revise":
        session.state = "revising"
        _touch(user_id)
        await query.edit_message_text(
            f"*Revising: {session.skill_name}*\n\n"
            f"Type your revision instructions (e.g., \"add error handling for network failures\").\n"
            f"Or /skill to cancel.",
            parse_mode="Markdown",
        )

    elif action == "cancel":
        _sessions.pop(user_id, None)
        await query.edit_message_text("Skill building cancelled.")

    else:
        await query.answer("Unknown action.")


# ── List / Delete helpers ─────────────────────────────────────────────

async def _list_skills(update: Update):
    """List installed skills."""
    if not SKILLS_DIR.exists():
        await update.message.reply_text("No skills installed yet. Use `/skill <description>` to create one.", parse_mode="Markdown")
        return

    skills = sorted(SKILLS_DIR.glob("*.py"))
    if not skills:
        await update.message.reply_text("No skills installed yet. Use `/skill <description>` to create one.", parse_mode="Markdown")
        return

    lines = ["*Installed Skills*\n"]
    for path in skills:
        name = path.stem
        # Try to read SKILL_META for description
        try:
            content = path.read_text(encoding="utf-8")
            match = re.search(r'"description"\s*:\s*"([^"]+)"', content)
            desc = match.group(1) if match else ""
        except Exception:
            desc = ""

        lines.append(f"  `{name}` — {desc}" if desc else f"  `{name}`")

    lines.append(f"\n{len(skills)} skill(s) installed")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def _delete_skill(update: Update, name: str):
    """Delete a skill by name."""
    filename = _sanitize_filename(name) + ".py"
    filepath = SKILLS_DIR / filename

    if not filepath.exists():
        await update.message.reply_text(f"Skill `{name}` not found.", parse_mode="Markdown")
        return

    filepath.unlink()
    await update.message.reply_text(f"Skill `{name}` deleted.", parse_mode="Markdown")
