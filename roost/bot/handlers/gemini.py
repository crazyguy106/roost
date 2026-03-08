"""Gemini AI command handlers — agentic with function calling.

All Gemini commands (/gem, /new, /lesson, /outline, /doc, /refine,
/labguide, /assessment) route through _run_gemini(), which uses
GeminiAgent when GEMINI_AGENTIC=true, falling back to CLI otherwise.
"""

import logging
import os

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.executor import (
    run_command_streaming,
    check_rate_limit, RateLimitError, _truncate_output,
)
from roost.bot.handlers.common import (
    _build_context, _save_doc,
    CURRICULUM_SYSTEM_PROMPT,
)
from roost.config import GEMINI_AGENTIC, GEMINI_API_KEY
try:
    from roost.curriculum_context import (
        build_curriculum_system_prompt,
        build_labguide_prompt,
        build_assessment_prompt,
        get_module,
    )
except ImportError:
    def build_curriculum_system_prompt(*a, **kw): return ""
    def build_labguide_prompt(*a, **kw): return ""
    def build_assessment_prompt(*a, **kw): return ""
    def get_module(*a, **kw): return None

logger = logging.getLogger("roost.bot.handlers.gemini")


async def _run_gemini(update: Update, context: ContextTypes.DEFAULT_TYPE,
                      continue_session: bool, system_prefix: str = ""):
    """Run Gemini — agentic (SDK + tools) or CLI fallback."""
    if not context.args:
        await update.message.reply_text("Usage: /gem Your prompt here")
        return

    user_id = update.effective_user.id
    try:
        check_rate_limit(user_id)
    except RateLimitError as e:
        await update.message.reply_text(str(e))
        return

    user_prompt = " ".join(context.args)

    # ── Agentic mode (google-genai SDK with function calling) ─────────
    if GEMINI_AGENTIC and GEMINI_API_KEY:
        try:
            from roost.gemini_agent import GeminiAgent
        except Exception:
            logger.exception("Failed to import GeminiAgent, falling back to CLI")
            return await _run_gemini_cli(
                update, user_id, user_prompt,
                continue_session, system_prefix,
            )

        session_id = f"{user_id}:latest" if continue_session else None
        mode = "agentic, continuing" if continue_session else "agentic, new"
        status_msg = await update.message.reply_text(
            f"```\nGemini ({mode})...\n```", parse_mode="Markdown",
        )

        # Build full system prompt with task context
        task_context = _build_context()
        full_system = (system_prefix or CURRICULUM_SYSTEM_PROMPT) + "\n" + task_context

        agent = GeminiAgent(
            system_prompt=full_system,
            session_id=session_id,
        )

        async def on_progress(text):
            try:
                display = _truncate_output(f"```\n{text}\n```")
                await status_msg.edit_text(display, parse_mode="Markdown")
            except Exception:
                logger.debug("Gemini progress edit failed", exc_info=True)

        try:
            output = await agent.run(
                user_prompt,
                user_id=str(user_id),
                on_progress=on_progress,
            )
        except Exception as e:
            logger.exception("GeminiAgent.run() failed")
            output = f"Gemini agent error: {e}"

        display = _truncate_output(output)
        try:
            await status_msg.edit_text(display)
        except Exception:
            await update.message.reply_text(display)

        # Save long output as file
        if len(output) > 2000:
            slug = user_prompt[:40].replace(" ", "_").lower()
            path = _save_doc(output, slug)
            await update.message.reply_document(
                document=open(path, "rb"),
                filename=os.path.basename(path),
                caption="Full output attached.",
            )
        return

    # ── CLI fallback ──────────────────────────────────────────────────
    await _run_gemini_cli(
        update, user_id, user_prompt,
        continue_session, system_prefix,
    )


async def _run_gemini_cli(update, user_id, user_prompt,
                          continue_session, system_prefix):
    """Original CLI shell-out to gemini binary."""
    task_context = _build_context()
    full_prompt = system_prefix + task_context + user_prompt

    mode = "continuing" if continue_session else "new session"
    status_msg = await update.message.reply_text(
        f"```\nGemini ({mode}, CLI)...\n```", parse_mode="Markdown",
    )

    cmd = ["gemini"]
    if continue_session:
        cmd.extend(["--resume", "latest"])
    cmd.extend(["-p", full_prompt])

    output = await run_command_streaming(
        cmd, status_msg=status_msg, timeout=300,
        source="telegram:gemini", user_id=str(user_id),
    )

    display = _truncate_output(output)
    try:
        await status_msg.edit_text(display)
    except Exception:
        await update.message.reply_text(display)

    if len(output) > 2000:
        slug = user_prompt[:40].replace(" ", "_").lower()
        path = _save_doc(output, slug)
        await update.message.reply_document(
            document=open(path, "rb"),
            filename=os.path.basename(path),
            caption="Full output attached.",
        )


# ── Command handlers ─────────────────────────────────────────────────

@authorized
async def cmd_gem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Continue last Gemini session with curriculum context."""
    await _run_gemini(update, context, continue_session=True,
                      system_prefix=CURRICULUM_SYSTEM_PROMPT)


@authorized
async def cmd_gem_new(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start fresh Gemini session."""
    await _run_gemini(update, context, continue_session=False,
                      system_prefix=CURRICULUM_SYSTEM_PROMPT)


@authorized
async def cmd_lesson(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a lesson plan."""
    if not context.args:
        await update.message.reply_text("Usage: /lesson Topic Name")
        return

    topic = " ".join(context.args)
    context.args = [
        f"Generate a detailed lesson plan for: {topic}. "
        "Include: learning objectives, prerequisite knowledge, "
        "lesson structure (intro/development/conclusion), "
        "activities, assessment methods, and resources. "
        "Map to relevant competency standards where applicable."
    ]
    await _run_gemini(update, context, continue_session=False,
                      system_prefix=CURRICULUM_SYSTEM_PROMPT)


@authorized
async def cmd_outline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate a course outline."""
    if not context.args:
        await update.message.reply_text("Usage: /outline Course Name")
        return

    course = " ".join(context.args)
    context.args = [
        f"Generate a comprehensive course outline for: {course}. "
        "Include: course description, learning outcomes, "
        "weekly/module breakdown with topics and sub-topics, "
        "assessment strategy (weightings, types), "
        "recommended resources, and prerequisite requirements. "
        "Align with relevant qualification framework standards."
    ]
    await _run_gemini(update, context, continue_session=False,
                      system_prefix=CURRICULUM_SYSTEM_PROMPT)


@authorized
async def cmd_doc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """General document generation (uses Gemini one-shot)."""
    if not context.args:
        await update.message.reply_text("Usage: /doc Write a README for...")
        return

    await _run_gemini(update, context, continue_session=False, system_prefix="")


@authorized
async def cmd_refine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Continue last Gemini session to iterate on output."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /refine Your refinement instructions\n"
            "e.g. /refine Make the rubric more detailed for Tier B"
        )
        return

    await _run_gemini(update, context, continue_session=True,
                      system_prefix=CURRICULUM_SYSTEM_PROMPT)


@authorized
async def cmd_labguide(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate detailed lab guide with module-specific Gemini context."""
    if not context.args:
        await update.message.reply_text("Usage: /labguide M5 [optional lab ref]")
        return

    module_id = context.args[0]
    lab_ref = " ".join(context.args[1:]) if len(context.args) > 1 else ""
    mod = get_module(module_id)
    if not mod:
        await update.message.reply_text(
            f"Module '{module_id}' not found. Use /modules to see all."
        )
        return

    lab_prompt = build_labguide_prompt(module_id, lab_ref)
    system = build_curriculum_system_prompt(module_id)

    context.args = [lab_prompt]
    await _run_gemini(update, context, continue_session=False,
                      system_prefix=system)


@authorized
async def cmd_assessment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Draft assessment rubric (Tier A gate / Tier B showcase)."""
    if not context.args:
        await update.message.reply_text(
            "Usage: /assessment M5 [tier-a|tier-b]\n"
            "Omit tier to generate both."
        )
        return

    module_id = context.args[0]
    tier = context.args[1] if len(context.args) > 1 else ""
    mod = get_module(module_id)
    if not mod:
        await update.message.reply_text(
            f"Module '{module_id}' not found. Use /modules to see all."
        )
        return

    assessment_prompt = build_assessment_prompt(module_id, tier)
    system = build_curriculum_system_prompt(module_id)

    context.args = [assessment_prompt]
    await _run_gemini(update, context, continue_session=False,
                      system_prefix=system)
