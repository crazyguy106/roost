"""AI-assisted email drafting using Gemini.

Generates reply drafts that match the user's writing style, guided by
a style guide document. Uses the google-genai SDK (same as gemini_agent.py).

Automatically detects scheduling intent in user instructions and
injects calendar availability into the prompt.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

from google import genai
from google.genai import types

from roost.config import GEMINI_API_KEY, GEMINI_MODEL

logger = logging.getLogger("roost.bot.email_draft")

# Module-level cache for the style guide text
_style_guide_cache: str | None = None

# Path to the style guide
_STYLE_GUIDE_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "email-style-guide.md"


def _load_style_guide() -> str:
    """Load the email style guide from docs/email-style-guide.md.

    Reads once and caches in a module-level variable.
    Extracts the key sections needed for drafting.
    """
    global _style_guide_cache
    if _style_guide_cache is not None:
        return _style_guide_cache

    try:
        text = _STYLE_GUIDE_PATH.read_text(encoding="utf-8")
        _style_guide_cache = text
        return _style_guide_cache
    except FileNotFoundError:
        logger.warning("Style guide not found at %s", _STYLE_GUIDE_PATH)
        _style_guide_cache = ""
        return _style_guide_cache


# Keywords that suggest the reply involves scheduling
_SCHEDULING_KEYWORDS = re.compile(
    r"\b("
    r"time|times|schedule|scheduling|available|availability|"
    r"free|slot|slots|meet|meeting|call|calendar|"
    r"when.*free|find.*time|suggest.*time|propose.*time|"
    r"set up.*call|arrange.*meeting|book|date|dates"
    r")\b",
    re.IGNORECASE,
)


def _detect_scheduling_intent(instruction: str, thread_text: str = "") -> bool:
    """Check if the instruction or recent thread suggests scheduling."""
    return bool(_SCHEDULING_KEYWORDS.search(instruction))


def _fetch_calendar_context(days: int = 7) -> str:
    """Fetch upcoming calendar events and format as context for the prompt."""
    try:
        from roost.calendar_service import get_week_events

        events = get_week_events(days=days)
        if not events:
            return "No calendar events in the next 7 days — schedule is open."

        lines = [f"Calendar for the next {days} days (today is {datetime.now().strftime('%A, %d %b %Y')}):"]
        current_date = None
        for ev in events:
            start = ev.get("start")
            if not start:
                continue
            date_str = start.strftime("%a %d %b")
            if date_str != current_date:
                current_date = date_str
                lines.append(f"\n  {date_str}:")
            time_str = start.strftime("%H:%M")
            end = ev.get("end")
            end_str = end.strftime("%H:%M") if end else "?"
            lines.append(f"    {time_str}-{end_str}  {ev.get('summary', '(busy)')}")

        return "\n".join(lines)

    except Exception:
        logger.exception("Failed to fetch calendar for draft context")
        return ""


def _build_draft_prompt(
    thread_messages: list[dict],
    user_instruction: str,
    style_guide: str,
    calendar_context: str = "",
) -> str:
    """Build the prompt for Gemini to draft a reply.

    Args:
        thread_messages: List of message dicts from read_thread() (last 2-3).
        user_instruction: The user's brief instruction for what the reply should say.
        style_guide: The loaded style guide text.

    Returns:
        A structured prompt string.
    """
    # Format thread history (last 3 messages max)
    recent = thread_messages[-3:] if len(thread_messages) > 3 else thread_messages
    thread_text = ""
    for msg in recent:
        body = msg.get("body", "")
        # Truncate very long bodies for prompt context
        if len(body) > 1500:
            body = body[:1500] + "\n[... truncated ...]"
        thread_text += (
            f"--- Message ---\n"
            f"From: {msg.get('from', 'Unknown')}\n"
            f"To: {msg.get('to', '')}\n"
            f"Date: {msg.get('date', '')}\n"
            f"Subject: {msg.get('subject', '')}\n\n"
            f"{body}\n\n"
        )

    # Extract the sender we're replying to
    last_msg = recent[-1] if recent else {}
    reply_to = last_msg.get("from", "")

    # Build optional calendar section
    cal_section = ""
    if calendar_context:
        cal_section = f"""
## Your Calendar (use this to suggest or confirm times)
{calendar_context}

IMPORTANT: When proposing times, only suggest slots that are NOT already blocked.
Suggest 2-3 specific options when possible. Use natural phrasing like "How about
Tuesday 11am?" rather than listing time slots mechanically.
"""

    prompt = f"""You are drafting an email reply on behalf of the user. Follow the style guide EXACTLY.

## Style Guide
{style_guide}

## Email Thread (most recent last)
{thread_text}
{cal_section}
## User's Instruction
{user_instruction}

## Your Task
Write ONLY the email body (greeting through sign-off). Do not include Subject line or headers.
Reply to: {reply_to}

Rules:
- Match the greeting tier to the relationship (see style guide Section 1 and 14)
- Use the correct sign-off pattern (see style guide Section 2)
- Keep the tone authentic to the style guide's voice
- Be direct and action-oriented
- No emoji, no "Best regards", no corporate filler
- Do not use "Please find attached" — contextualise any references naturally
- Output plain text only, no markdown formatting
"""

    return prompt


async def draft_reply(
    thread_messages: list[dict],
    user_instruction: str,
) -> str:
    """Generate an AI-drafted email reply using Gemini.

    Args:
        thread_messages: Messages from read_thread()["messages"].
        user_instruction: Brief instruction from the user (e.g. "confirm dates and ask about venue").

    Returns:
        The drafted email body text, or an error message string.
    """
    if not GEMINI_API_KEY:
        return "[Error: GEMINI_API_KEY not configured]"

    style_guide = _load_style_guide()

    # Auto-detect scheduling intent and fetch calendar if needed
    calendar_context = ""
    if _detect_scheduling_intent(user_instruction):
        logger.info("Scheduling intent detected — fetching calendar context")
        calendar_context = _fetch_calendar_context(days=7)

    prompt = _build_draft_prompt(
        thread_messages, user_instruction, style_guide,
        calendar_context=calendar_context,
    )

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = await client.aio.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,
                max_output_tokens=1024,
            ),
        )
        if response.text:
            return response.text.strip()
        return "[Error: Gemini returned empty response]"
    except Exception as e:
        logger.exception("Failed to generate email draft")
        return f"[Error: {e}]"
