"""Presentation and meeting notes bot commands: /deck, /mnotes."""

import logging
import os
import tempfile

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized

logger = logging.getLogger(__name__)


@authorized
async def cmd_deck(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/deck <prompt> — Generate a presentation (.pptx) using Gemini.

    Also supports: /deck from notes: <pasted notes>
    """
    if not context.args:
        await update.message.reply_text(
            "*Generate Presentation*\n\n"
            "Usage:\n"
            "`/deck AI governance overview for board members`\n"
            "`/deck from notes: John said deadline is Friday...`\n\n"
            "Generates a .pptx file with ~8 slides using Gemini AI.",
            parse_mode="Markdown",
        )
        return

    full_text = " ".join(context.args)

    # Detect "from notes:" prefix
    notes = ""
    prompt = full_text
    lower = full_text.lower()
    if lower.startswith("from notes:") or lower.startswith("from notes "):
        sep_idx = full_text.index(":") if ":" in full_text[:15] else 10
        notes = full_text[sep_idx + 1:].strip()
        prompt = "Create a presentation based on these meeting notes"

    status_msg = await update.message.reply_text(
        "Generating presentation content with Gemini..."
    )

    try:
        from roost.meeting_notes_service import generate_presentation_content
        from roost.presentation_builder import build_presentation_from_content

        # Generate content
        content = generate_presentation_content(prompt=prompt, notes=notes)

        await status_msg.edit_text(
            f"Building .pptx: \"{content.get('title', 'Presentation')}\" "
            f"({len(content.get('slides', []))} slides)..."
        )

        # Build .pptx
        title_slug = content.get("title", "presentation")[:40].replace(" ", "_")
        output_path = os.path.join(tempfile.gettempdir(), f"{title_slug}.pptx")
        build_presentation_from_content(content, output_path=output_path)

        # Send file
        size_kb = os.path.getsize(output_path) / 1024
        caption = (
            f"*{content.get('title', 'Presentation')}*\n"
            f"{len(content.get('slides', []))} slides | {size_kb:.0f} KB"
        )
        with open(output_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=os.path.basename(output_path),
                caption=caption,
                parse_mode="Markdown",
            )

        await status_msg.delete()

        # Cleanup
        os.remove(output_path)

    except Exception as e:
        logger.exception("Deck generation failed")
        await status_msg.edit_text(f"Presentation generation failed: {e}")


@authorized
async def cmd_mnotes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/mnotes <text> — Structure meeting notes using Gemini AI.

    Takes raw meeting notes and returns organized key points,
    action items, and decisions.
    """
    if not context.args:
        await update.message.reply_text(
            "*Structure Meeting Notes*\n\n"
            "Usage:\n"
            "`/mnotes John said deadline is Friday. Budget approved at 50k.`\n\n"
            "Returns structured notes with key points, action items, and decisions.",
            parse_mode="Markdown",
        )
        return

    raw_text = " ".join(context.args)

    status_msg = await update.message.reply_text(
        "Structuring meeting notes with Gemini..."
    )

    try:
        from roost.meeting_notes_service import structure_meeting_notes

        result = structure_meeting_notes(raw_text)

        # Format for Telegram
        lines = []
        if result.get("summary"):
            lines.append(f"*Summary:* {result['summary']}\n")

        if result.get("attendees"):
            lines.append("*Attendees:* " + ", ".join(result["attendees"]) + "\n")

        if result.get("key_points"):
            lines.append("*Key Points:*")
            for p in result["key_points"]:
                lines.append(f"  \u2022 {p}")
            lines.append("")

        if result.get("action_items"):
            lines.append("*Action Items:*")
            for item in result["action_items"]:
                owner = item.get("owner", "?")
                action = item.get("action", "")
                deadline = f" (by {item['deadline']})" if item.get("deadline") else ""
                lines.append(f"  \u2610 *{owner}*: {action}{deadline}")
            lines.append("")

        if result.get("decisions"):
            lines.append("*Decisions:*")
            for d in result["decisions"]:
                lines.append(f"  \u2714 {d}")

        if not lines:
            lines.append("No structured content could be extracted.")

        await status_msg.edit_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        logger.exception("Meeting notes structuring failed")
        await status_msg.edit_text(f"Meeting notes structuring failed: {e}")
