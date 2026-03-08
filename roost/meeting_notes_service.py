"""Meeting notes & presentation content service — Gemini-powered.

Stateless functions for:
- Audio transcription via Gemini (no whisper dependency)
- Meeting note structuring (raw text → key points, actions, decisions)
- Presentation content generation (prompt → structured slide JSON)
"""

import json
import logging
import os

logger = logging.getLogger("roost.meeting_notes")


def transcribe_audio_gemini(audio_path: str) -> str:
    """Transcribe audio using Gemini's multimodal capabilities.

    Sends audio bytes directly to Gemini via google-genai SDK.
    Supports OGG, MP3, WAV, M4A formats.

    Args:
        audio_path: Path to the audio file.

    Returns:
        Transcribed text string.

    Raises:
        FileNotFoundError: If audio file doesn't exist.
        RuntimeError: If Gemini API call fails.
    """
    from google import genai
    from google.genai import types
    from roost.config import GEMINI_API_KEY, GEMINI_MODEL

    if not os.path.isfile(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Map extensions to MIME types
    ext = os.path.splitext(audio_path)[1].lower()
    mime_map = {
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".webm": "audio/webm",
        ".flac": "audio/flac",
    }
    mime_type = mime_map.get(ext, "audio/ogg")

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    client = genai.Client(api_key=GEMINI_API_KEY)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
            "Transcribe this audio accurately. Return only the transcription text, "
            "no commentary or formatting. If there are multiple speakers, indicate "
            "speaker changes with line breaks.",
        ],
    )

    text = response.text.strip() if response.text else ""
    if not text:
        raise RuntimeError("Gemini returned empty transcription")

    logger.info("Transcribed %d bytes audio → %d chars", len(audio_bytes), len(text))
    return text


def structure_meeting_notes(raw_text: str, format: str = "markdown") -> dict:
    """Structure raw meeting notes using Gemini.

    Takes unstructured text (typed, pasted, or transcribed) and returns
    organized meeting notes with key points, action items, and decisions.

    Args:
        raw_text: Raw meeting notes text.
        format: Output format — "markdown" or "json".

    Returns:
        Dict with keys: summary, key_points, action_items, decisions,
        attendees (if mentioned), formatted_output.
    """
    from google import genai
    from google.genai import types
    from roost.config import GEMINI_API_KEY, GEMINI_MODEL

    client = genai.Client(api_key=GEMINI_API_KEY)

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f"""Analyze these meeting notes and return a JSON object with this exact structure:
{{
  "summary": "2-3 sentence summary of the meeting",
  "key_points": ["point 1", "point 2", ...],
  "action_items": [
    {{"owner": "person name or 'unassigned'", "action": "what needs to be done", "deadline": "if mentioned, else null"}}
  ],
  "decisions": ["decision 1", "decision 2", ...],
  "attendees": ["name 1", "name 2", ...]
}}

If attendees are not mentioned, return an empty list.
If no decisions are explicitly stated, infer from context or return empty list.
Extract ALL action items — anything someone needs to do.

Meeting notes:
{raw_text}""",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )

    try:
        result = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        result = {
            "summary": response.text[:500] if response.text else "Failed to parse",
            "key_points": [],
            "action_items": [],
            "decisions": [],
            "attendees": [],
        }

    # Generate formatted output
    if format == "markdown":
        lines = [f"## Meeting Notes\n\n{result.get('summary', '')}\n"]

        if result.get("attendees"):
            lines.append("### Attendees")
            for a in result["attendees"]:
                lines.append(f"- {a}")
            lines.append("")

        if result.get("key_points"):
            lines.append("### Key Points")
            for p in result["key_points"]:
                lines.append(f"- {p}")
            lines.append("")

        if result.get("action_items"):
            lines.append("### Action Items")
            for item in result["action_items"]:
                owner = item.get("owner", "unassigned")
                action = item.get("action", "")
                deadline = f" (by {item['deadline']})" if item.get("deadline") else ""
                lines.append(f"- [ ] **{owner}**: {action}{deadline}")
            lines.append("")

        if result.get("decisions"):
            lines.append("### Decisions")
            for d in result["decisions"]:
                lines.append(f"- {d}")
            lines.append("")

        result["formatted_output"] = "\n".join(lines)
    else:
        result["formatted_output"] = json.dumps(result, indent=2)

    logger.info(
        "Structured notes: %d points, %d actions, %d decisions",
        len(result.get("key_points", [])),
        len(result.get("action_items", [])),
        len(result.get("decisions", [])),
    )
    return result


def generate_presentation_content(
    prompt: str,
    notes: str = "",
    num_slides: int = 8,
) -> dict:
    """Generate structured presentation content using Gemini.

    Returns a JSON structure suitable for building a .pptx file.

    Args:
        prompt: Description of the presentation topic/purpose.
        notes: Optional meeting notes or source material to base slides on.
        num_slides: Target number of slides (default 8).

    Returns:
        Dict with keys: title, subtitle, slides (list of {title, bullets, notes}).
    """
    from google import genai
    from google.genai import types
    from roost.config import GEMINI_API_KEY, GEMINI_MODEL

    client = genai.Client(api_key=GEMINI_API_KEY)

    source_context = ""
    if notes:
        source_context = f"""

Use the following notes/material as the basis for the presentation content:
---
{notes[:8000]}
---"""

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=f"""Create a professional presentation with approximately {num_slides} slides.
Topic: {prompt}{source_context}

Return a JSON object with this exact structure:
{{
  "title": "Presentation Title",
  "subtitle": "Subtitle or tagline",
  "slides": [
    {{
      "title": "Slide Title",
      "bullets": ["Point 1", "Point 2", "Point 3"],
      "notes": "Speaker notes for this slide"
    }}
  ]
}}

Guidelines:
- First slide should be the title/intro slide (no bullets needed, just title)
- Last slide should be a summary or Q&A slide
- Each slide should have 3-5 bullet points (concise, not sentences)
- Speaker notes should expand on the bullets with talking points
- Keep bullet text under 60 characters each
- Make it professional and engaging""",
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.6,
        ),
    )

    try:
        content = json.loads(response.text)
    except (json.JSONDecodeError, TypeError):
        raise RuntimeError(
            f"Failed to parse Gemini response as JSON: {response.text[:200]}"
        )

    # Validate structure
    if "slides" not in content:
        raise RuntimeError("Gemini response missing 'slides' key")
    if not content["slides"]:
        raise RuntimeError("Gemini returned empty slides list")

    logger.info(
        "Generated presentation: '%s' with %d slides",
        content.get("title", "Untitled"),
        len(content["slides"]),
    )
    return content
