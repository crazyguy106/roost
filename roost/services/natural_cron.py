"""Natural language cron parser — converts plain English to cron recipes.

Uses Gemini to parse sentences like "Every Monday at 9am, summarize my unread
emails" into a cron trigger_config (HH:MM[:day_spec]) and instructions.

The parser doesn't need to handle full cron syntax — it maps to the existing
recipe trigger_config format: "HH:MM", "HH:MM:weekdays", "HH:MM:0,2,4", etc.
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# ── Prompt for Gemini ──────────────────────────────────────────────

_PARSE_PROMPT = """\
You are a schedule parser. Convert the user's natural language description into a structured JSON schedule.

Output ONLY valid JSON with these fields:
{
  "time": "HH:MM",
  "day_spec": "",
  "name": "",
  "instructions": "",
  "risk_tier": "read_only"
}

Rules for day_spec:
- Empty string "" = every day
- "weekdays" = Monday to Friday
- "weekends" = Saturday and Sunday
- Comma-separated day numbers (0=Monday, 1=Tuesday, ..., 6=Sunday) for specific days
  e.g. "0" = every Monday, "0,2,4" = Mon/Wed/Fri

Rules for risk_tier:
- "read_only" = only reads data (summaries, reports, lookups)
- "internal_write" = writes to internal systems (tasks, notes)
- "external_write" = sends external messages (email, Telegram)

Rules for name:
- Short descriptive name, max 50 chars

Rules for instructions:
- The action to perform, written as a clear instruction to an AI agent
- Include all relevant details from the user's request

Examples:
User: "Every Monday at 9am, summarize my unread emails"
{"time": "09:00", "day_spec": "0", "name": "Monday email summary", "instructions": "Summarize unread emails from the inbox, grouped by sender and urgency.", "risk_tier": "read_only"}

User: "Daily at 6pm, send me a task progress report"
{"time": "18:00", "day_spec": "", "name": "Daily task report", "instructions": "Generate a progress report of today's completed and in-progress tasks.", "risk_tier": "read_only"}

User: "Every weekday morning at 8:30, email me my calendar for the day"
{"time": "08:30", "day_spec": "weekdays", "name": "Weekday calendar email", "instructions": "Get today's calendar events and send a summary email to me.", "risk_tier": "external_write"}

User: "On Fridays at 5pm, create a weekly review note"
{"time": "17:00", "day_spec": "4", "name": "Friday weekly review", "instructions": "Create a note with a weekly review: tasks completed, tasks carried over, and key achievements.", "risk_tier": "internal_write"}

Now parse this:
User: "{user_input}"
"""


# ── Parser ─────────────────────────────────────────────────────────

def parse_natural_schedule(text: str) -> dict:
    """Parse natural language into a schedule config.

    Returns dict with: time, day_spec, name, instructions, risk_tier.
    Falls back to regex heuristics if Gemini is unavailable.
    """
    # Try Gemini first
    try:
        result = _parse_with_gemini(text)
        if result and "time" in result:
            return _validate(result)
    except Exception:
        logger.debug("Gemini parse failed, falling back to regex", exc_info=True)

    # Fallback: regex heuristics
    return _parse_with_regex(text)


def schedule_to_trigger_config(parsed: dict) -> str:
    """Convert parsed schedule to trigger_config format (HH:MM[:day_spec])."""
    time_str = parsed.get("time", "09:00")
    day_spec = parsed.get("day_spec", "")
    if day_spec:
        return f"{time_str}:{day_spec}"
    return time_str


def _parse_with_gemini(text: str) -> dict | None:
    """Use Gemini to parse natural language schedule."""
    from roost.config import GEMINI_API_KEY, GEMINI_MODEL
    if not GEMINI_API_KEY:
        return None

    from google import genai

    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=_PARSE_PROMPT.format(user_input=text),
        config=genai.types.GenerateContentConfig(
            temperature=0.1,
            max_output_tokens=300,
        ),
    )

    raw = response.text.strip()
    # Extract JSON from response (may have markdown fences)
    json_match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
    if json_match:
        return json.loads(json_match.group())
    return None


def _parse_with_regex(text: str) -> dict:
    """Fallback regex parser for common schedule patterns."""
    text_lower = text.lower()

    # Extract time
    time_match = re.search(
        r'(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
        text_lower,
    )
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2) or "0")
        ampm = time_match.group(3)
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        time_str = f"{hour:02d}:{minute:02d}"
    else:
        time_str = "09:00"  # Default

    # Extract day spec
    day_spec = ""
    day_map = {
        "monday": "0", "tuesday": "1", "wednesday": "2",
        "thursday": "3", "friday": "4", "saturday": "5", "sunday": "6",
    }

    if "weekday" in text_lower or "week day" in text_lower:
        day_spec = "weekdays"
    elif "weekend" in text_lower:
        day_spec = "weekends"
    elif "daily" in text_lower or "every day" in text_lower:
        day_spec = ""
    else:
        days = []
        for day_name, day_num in day_map.items():
            if day_name in text_lower:
                days.append(day_num)
        if days:
            day_spec = ",".join(days)

    # Extract instructions (everything after the schedule part)
    # Try to split on comma after time/day specification
    instructions = text
    for separator in [", ", " - ", ": "]:
        if separator in text:
            parts = text.split(separator, 1)
            if len(parts) == 2:
                instructions = parts[1].strip()
                break

    # Determine risk tier from keywords
    risk_tier = "read_only"
    if any(w in text_lower for w in ["send", "email", "message", "notify"]):
        risk_tier = "external_write"
    elif any(w in text_lower for w in ["create", "add", "update", "write", "log"]):
        risk_tier = "internal_write"

    # Generate name
    name = instructions[:50].rstrip(" .,;:")

    return {
        "time": time_str,
        "day_spec": day_spec,
        "name": name,
        "instructions": instructions,
        "risk_tier": risk_tier,
    }


def _validate(parsed: dict) -> dict:
    """Validate and normalize parsed schedule."""
    # Validate time format
    time_str = parsed.get("time", "09:00")
    if not re.match(r'^\d{2}:\d{2}$', time_str):
        parsed["time"] = "09:00"

    # Validate day_spec
    day_spec = parsed.get("day_spec", "")
    if day_spec not in ("", "weekdays", "weekends"):
        # Must be comma-separated digits 0-6
        if not re.match(r'^[0-6](,[0-6])*$', day_spec):
            parsed["day_spec"] = ""

    # Validate risk_tier
    if parsed.get("risk_tier") not in ("read_only", "internal_write", "external_write"):
        parsed["risk_tier"] = "read_only"

    # Ensure required fields
    parsed.setdefault("name", "Scheduled task")
    parsed.setdefault("instructions", "")

    return parsed
