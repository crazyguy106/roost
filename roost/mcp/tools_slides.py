"""MCP tools for slide editing — PowerPoint (.pptx) and Google Slides."""

from roost.mcp.server import mcp


# ── PowerPoint (local .pptx files) ───────────────────────────────────


@mcp.tool()
def pptx_list_placeholders(file_path: str) -> dict:
    """List all slides with their shapes, placeholders, and text content from a .pptx file.

    Use this to inspect a PowerPoint template before replacing placeholders.

    Args:
        file_path: Absolute path to the .pptx file.
    """
    try:
        from roost.slides_service import pptx_list_placeholders as _list
        slides = _list(file_path)
        return {"slide_count": len(slides), "slides": slides}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def pptx_replace_text(
    file_path: str,
    replacements: dict,
    output_path: str = "",
) -> dict:
    """Replace placeholder text across all slides, tables, and notes in a .pptx file.

    Preserves the original formatting of the text runs — only the text content changes.

    Args:
        file_path: Absolute path to the source .pptx file.
        replacements: JSON object mapping placeholder strings to replacement values,
            e.g. {"{{title}}": "My Title", "{{date}}": "2026-02-13"}.
        output_path: Where to save the result. Empty string = overwrite the source file.
    """
    try:
        from roost.slides_service import pptx_replace_text as _replace
        return _replace(file_path, replacements, output_path or None)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def pptx_get_slide_notes(file_path: str) -> dict:
    """Read speaker notes from all slides in a .pptx file.

    Args:
        file_path: Absolute path to the .pptx file.
    """
    try:
        from roost.slides_service import pptx_get_slide_notes as _notes
        notes = _notes(file_path)
        return {"slide_count": len(notes), "slides": notes}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def pptx_duplicate_slide(
    file_path: str,
    slide_index: int,
    output_path: str = "",
) -> dict:
    """Duplicate a slide within a .pptx file.

    Args:
        file_path: Absolute path to the .pptx file.
        slide_index: 0-based index of the slide to duplicate.
        output_path: Where to save. Empty string = overwrite the source file.
    """
    try:
        from roost.slides_service import pptx_duplicate_slide as _dup
        return _dup(file_path, slide_index, output_path or None)
    except Exception as e:
        return {"error": str(e)}


# ── Google Slides ─────────────────────────────────────────────────────


@mcp.tool()
def gslides_list_placeholders(presentation_id: str) -> dict:
    """List all slides and text elements in a Google Slides presentation.

    Use this to discover placeholder tags (e.g. {{title}}) before replacing them.

    Args:
        presentation_id: The Google Slides presentation ID (from the URL).
    """
    try:
        from roost.slides_service import gslides_list_placeholders as _list
        slides = _list(presentation_id)
        return {"slide_count": len(slides), "slides": slides}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gslides_replace_text(presentation_id: str, replacements: dict) -> dict:
    """Replace placeholder text across all slides in a Google Slides presentation.

    Searches all text in the presentation and replaces matches (case-sensitive).

    Args:
        presentation_id: The Google Slides presentation ID.
        replacements: JSON object mapping placeholder strings to replacement values,
            e.g. {"{{title}}": "My Title", "{{date}}": "2026-02-13"}.
    """
    try:
        from roost.slides_service import gslides_replace_text as _replace
        return _replace(presentation_id, replacements)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gslides_create_from_template(
    template_id: str,
    name: str,
    replacements: str = "",
    folder_id: str = "",
) -> dict:
    """Create a new Google Slides presentation by copying a template and filling placeholders.

    This is the primary workflow: copy a template, replace all {{placeholders}}, done.

    Args:
        template_id: ID of the template presentation to copy.
        name: Name for the new presentation.
        replacements: Optional JSON string of placeholder replacements,
            e.g. '{"{{title}}": "My Title"}'.  Empty string = no replacements.
        folder_id: Optional Google Drive folder ID to place the copy in.
    """
    try:
        import json
        from roost.slides_service import gslides_create_from_template as _create

        repl = json.loads(replacements) if replacements else None
        return _create(template_id, name, repl, folder_id or None)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in replacements: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gslides_replace_image(
    presentation_id: str,
    placeholder_text: str,
    image_url: str,
) -> dict:
    """Replace shapes containing placeholder text with an image in a Google Slides presentation.

    The image replaces the entire shape, sized to fit within the shape's bounds.
    The image must be accessible via a public URL.

    Args:
        presentation_id: The Google Slides presentation ID.
        placeholder_text: Text in shapes to match (e.g. "{{logo}}").
        image_url: Public URL of the replacement image.
    """
    try:
        from roost.slides_service import gslides_replace_image as _replace
        return _replace(presentation_id, placeholder_text, image_url)
    except Exception as e:
        return {"error": str(e)}
