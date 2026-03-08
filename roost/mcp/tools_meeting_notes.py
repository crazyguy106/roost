"""MCP tools for meeting notes and presentation generation.

Three tools:
- structure_meeting_notes — raw text → structured notes (key points, actions, decisions)
- generate_presentation — prompt → .pptx file via Gemini + python-pptx
- transcribe_audio — audio file → text via Gemini multimodal
"""

import os

from roost.mcp.server import mcp


@mcp.tool()
def structure_meeting_notes(
    text: str,
    format: str = "markdown",
) -> dict:
    """Structure raw meeting notes into key points, action items, and decisions.

    Uses Gemini AI to analyze unstructured meeting notes and extract:
    - Summary
    - Key points
    - Action items (with owners and deadlines)
    - Decisions made
    - Attendees mentioned

    Args:
        text: Raw meeting notes text (typed, pasted, or transcribed).
        format: Output format — "markdown" or "json". Default "markdown".
    """
    try:
        from roost.meeting_notes_service import structure_meeting_notes as _structure
        return _structure(text, format=format)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def generate_presentation(
    prompt: str,
    notes: str = "",
    template_path: str = "",
    output_path: str = "/tmp/presentation.pptx",
    num_slides: int = 8,
) -> dict:
    """Generate a PowerPoint presentation (.pptx) using Gemini AI.

    Gemini generates structured slide content, then python-pptx builds the file.
    Optionally uses meeting notes as source material.

    Args:
        prompt: Description of the presentation topic/purpose.
        notes: Optional meeting notes or source material to base slides on.
        template_path: Optional .pptx template file to use for styling.
        output_path: Where to save the .pptx file. Default "/tmp/presentation.pptx".
        num_slides: Target number of slides (default 8).
    """
    try:
        from roost.meeting_notes_service import generate_presentation_content
        from roost.presentation_builder import build_presentation_from_content

        content = generate_presentation_content(
            prompt=prompt, notes=notes, num_slides=num_slides,
        )

        build_presentation_from_content(
            content,
            template_path=template_path or None,
            output_path=output_path,
        )

        size_bytes = os.path.getsize(output_path)

        return {
            "ok": True,
            "output_path": output_path,
            "title": content.get("title", "Untitled"),
            "subtitle": content.get("subtitle", ""),
            "slide_count": len(content.get("slides", [])),
            "size_bytes": size_bytes,
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def transcribe_audio(
    audio_path: str,
) -> dict:
    """Transcribe an audio file using Gemini multimodal AI.

    Sends audio bytes directly to Gemini for transcription.
    Supports OGG, MP3, WAV, M4A, WebM, FLAC formats.
    No whisper or local model required.

    Args:
        audio_path: Path to the audio file to transcribe.
    """
    try:
        from roost.meeting_notes_service import transcribe_audio_gemini
        text = transcribe_audio_gemini(audio_path)
        return {
            "ok": True,
            "text": text,
            "length": len(text),
            "audio_path": audio_path,
        }
    except Exception as e:
        return {"error": str(e)}
