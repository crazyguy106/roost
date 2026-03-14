"""Natural language agent — routes free-text Telegram messages to AI.

Provider-agnostic agentic loop with tool use:
- Gemini: google-genai SDK (free with Google account)
- Claude: Anthropic SDK (API key required)
- OpenAI/ChatGPT: OpenAI SDK (API key required)
- Ollama: OpenAI-compatible SDK (free, local)

All providers share the same tool registry (22 tools: calendar, email,
tasks, notes, files, skills, briefing).

Falls back to CLI passthrough when no API key is available.

Registered in group 0 (after capture/triage in group -1).
"""

import logging
import shutil

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.executor import (
    check_rate_limit, RateLimitError,
    run_command_streaming, _truncate_output,
)
from roost.config import (
    AGENT_ENABLED, AGENT_PROVIDER, AGENT_TIMEOUT, AI_RATE_LIMIT,
    GEMINI_API_KEY, GEMINI_AGENTIC,
    CLAUDE_API_KEY, CLAUDE_MODEL,
    OPENAI_API_KEY, OPENAI_MODEL,
    OLLAMA_URL, OLLAMA_MODEL,
)

logger = logging.getLogger(__name__)

# CLI fallback providers (when no API key available)
_CLI_PROVIDERS = {
    "gemini": {"bin": "gemini", "args": lambda p: ["gemini", p], "label": "Gemini"},
    "claude": {"bin": "claude", "args": lambda p: ["claude", "-p", p], "label": "Claude"},
    "codex": {"bin": "codex", "args": lambda p: ["codex", p], "label": "Codex"},
    "openai": {"bin": "codex", "args": lambda p: ["codex", p], "label": "OpenAI"},
}

# System prompt shared by all agentic providers
AGENT_SYSTEM_PROMPT = """You are a personal AI agent running on the user's private server via Roost.
You help with daily productivity: managing tasks, checking calendar, triaging email, taking notes, and running custom skills.

Guidelines:
- Be concise and direct. This is a Telegram chat, not an essay.
- When asked about today's schedule, use get_today_briefing or get_today_events.
- When asked about email, use search_emails. Common queries: "is:unread", "is:unread label:INBOX", "from:person".
- When asked to send/reply to email, use draft_email to create a draft. NEVER send without showing the draft first.
- When asked to create a task or note, use create_task or create_note.
- When asked to complete/finish a task, use complete_task.
- When the user's request might match a custom skill, use list_skills to check what's available, then run_skill to execute it.
- For general questions that don't need tools, just answer directly.
- Keep responses under 2000 characters when possible (Telegram limit).
"""


def _get_agentic_mode() -> str | None:
    """Determine which agentic mode is available for the configured provider.

    Returns: 'gemini', 'claude', 'openai', 'ollama', or None (CLI fallback).
    """
    if AGENT_PROVIDER == "gemini" and GEMINI_API_KEY and GEMINI_AGENTIC:
        return "gemini"
    if AGENT_PROVIDER == "claude" and CLAUDE_API_KEY:
        return "claude"
    if AGENT_PROVIDER == "openai" and OPENAI_API_KEY:
        return "openai"
    if AGENT_PROVIDER == "ollama":
        return "ollama"  # No API key needed — local
    return None


def _get_cli_cmd(prompt: str) -> tuple[list[str], str] | None:
    """Build CLI command for fallback mode."""
    provider = _CLI_PROVIDERS.get(AGENT_PROVIDER)
    if not provider:
        return None
    if not shutil.which(provider["bin"]):
        return None
    return provider["args"](prompt), provider["label"]


@authorized
async def handle_agent_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages by routing to AI agent."""
    if not update.message or not update.message.text:
        return
    if not AGENT_ENABLED:
        return

    prompt = update.message.text.strip()
    if not prompt:
        return

    user_id = update.effective_user.id

    # Check for active skill revision session first
    try:
        from roost.bot.handlers.skill_builder import handle_skill_revision
        if await handle_skill_revision(update, context):
            return
    except ImportError:
        pass

    # Rate limiting
    try:
        check_rate_limit(user_id)
    except RateLimitError as e:
        await update.message.reply_text(f"Slow down! Try again in {e.wait_seconds}s.")
        return

    # Try agentic mode first
    mode = _get_agentic_mode()
    if mode:
        await _run_agentic(update, prompt, user_id, mode)
        return

    # CLI fallback
    await _run_cli_passthrough(update, prompt, user_id)


async def _run_agentic(update: Update, prompt: str, user_id: int, mode: str):
    """Run the agentic loop with the appropriate provider."""
    session_id = f"{user_id}:agent"

    labels = {"gemini": "Gemini", "claude": "Claude", "openai": "ChatGPT", "ollama": "Ollama"}
    status_msg = await update.message.reply_text(f"Thinking ({labels.get(mode, mode)})...")

    async def on_progress(text):
        try:
            await status_msg.edit_text(_truncate_output(text, limit=4000))
        except Exception:
            pass

    try:
        agent = _create_agent(mode, session_id)
    except ImportError as e:
        logger.warning("Agent import failed for %s: %s", mode, e)
        await status_msg.edit_text(f"Provider '{mode}' SDK not installed: {e}")
        return
    except Exception as e:
        logger.exception("Agent creation failed for %s", mode)
        await status_msg.edit_text(f"Agent error: {e}")
        return

    try:
        output = await agent.run(prompt, user_id=str(user_id), on_progress=on_progress)
    except Exception as e:
        logger.exception("Agent.run() failed for %s", mode)
        output = f"Agent error: {e}"

    if output:
        display = _truncate_output(output, limit=4000)
        try:
            await status_msg.edit_text(display)
        except Exception:
            try:
                await update.message.reply_text(display)
            except Exception:
                logger.debug("Failed to send agent output", exc_info=True)


def _create_agent(mode: str, session_id: str):
    """Factory: create the right agent class for the provider."""
    if mode == "gemini":
        from roost.gemini_agent import GeminiAgent
        return GeminiAgent(
            system_prompt=AGENT_SYSTEM_PROMPT,
            session_id=session_id,
            include_agent_tools=True,
        )

    if mode == "claude":
        from roost.agents import ClaudeAgent
        return ClaudeAgent(
            system_prompt=AGENT_SYSTEM_PROMPT,
            session_id=session_id,
            include_agent_tools=True,
            api_key=CLAUDE_API_KEY,
            model=CLAUDE_MODEL,
        )

    if mode == "openai":
        from roost.agents import OpenAIAgent
        return OpenAIAgent(
            system_prompt=AGENT_SYSTEM_PROMPT,
            session_id=session_id,
            include_agent_tools=True,
            api_key=OPENAI_API_KEY,
            model=OPENAI_MODEL,
        )

    if mode == "ollama":
        from roost.agents import OpenAIAgent
        return OpenAIAgent(
            system_prompt=AGENT_SYSTEM_PROMPT,
            session_id=session_id,
            include_agent_tools=True,
            api_key="ollama",  # Ollama doesn't need a real key
            base_url=OLLAMA_URL,
            model=OLLAMA_MODEL,
        )

    raise ValueError(f"Unknown agent mode: {mode}")


async def _run_cli_passthrough(update: Update, prompt: str, user_id: int):
    """Fallback: pipe message to CLI binary (chat only, no tools)."""
    result = _get_cli_cmd(prompt)
    if result is None:
        await update.message.reply_text(
            f"Agent provider '{AGENT_PROVIDER}' is not available.\n"
            f"No API key set and no CLI binary found.\n\n"
            f"Set one of these in .env:\n"
            f"  AGENT_PROVIDER=gemini + GEMINI_API_KEY\n"
            f"  AGENT_PROVIDER=claude + CLAUDE_API_KEY\n"
            f"  AGENT_PROVIDER=openai + OPENAI_API_KEY\n"
            f"  AGENT_PROVIDER=ollama (free, local)"
        )
        return

    cmd, label = result
    status_msg = await update.message.reply_text(f"Thinking ({label})...")

    output = await run_command_streaming(
        cmd, status_msg,
        timeout=AGENT_TIMEOUT,
        source="telegram-agent",
        user_id=str(user_id),
    )

    if output:
        if len(output) > 4000:
            output = output[:4000] + "\n\n... [truncated]"
        try:
            await status_msg.edit_text(output)
        except Exception:
            try:
                await update.message.reply_text(output)
            except Exception:
                logger.debug("Failed to send agent output", exc_info=True)


@authorized
async def cmd_agent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/agent — show agent status and provider info."""
    if not AGENT_ENABLED:
        await update.message.reply_text("Agent is disabled. Set AGENT_ENABLED=true in .env")
        return

    mode = _get_agentic_mode()
    lines = ["*AI Agent Status*\n"]

    providers = [
        ("gemini", "Gemini", bool(GEMINI_API_KEY and GEMINI_AGENTIC), "Free (Google account)"),
        ("claude", "Claude", bool(CLAUDE_API_KEY), "API key ($)"),
        ("openai", "ChatGPT", bool(OPENAI_API_KEY), "API key ($)"),
        ("ollama", "Ollama", True, f"Local ({OLLAMA_MODEL})"),
    ]

    for key, label, ready, cost in providers:
        marker = ">" if key == AGENT_PROVIDER else " "
        state = "ready" if (key == AGENT_PROVIDER and mode) else ("configured" if ready else "not configured")
        lines.append(f"`{marker} {label:8s}` — {state} ({cost})")

    lines.append(f"\n*Active:* `{AGENT_PROVIDER}`")
    if mode:
        lines.append(f"*Mode:* Agentic (22 tools)")
    else:
        cli = _get_cli_cmd("test")
        if cli:
            lines.append("*Mode:* CLI passthrough (chat only)")
        else:
            lines.append("*Mode:* Not available (no API key or CLI)")
    lines.append(f"*Rate limit:* {AI_RATE_LIMIT}s between messages")
    lines.append("\nJust type a message — no command needed!")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
