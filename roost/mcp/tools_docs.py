"""MCP tools for Google Docs — read, replace, append, template creation."""

from roost.mcp.server import mcp


@mcp.tool()
def gdocs_read_content(document_id: str) -> dict:
    """Read the full text content of a Google Doc.

    Returns the document title, full text, and character count.

    Args:
        document_id: The Google Docs document ID (from the URL).
    """
    try:
        from roost.docs_service import gdocs_read_content as _read
        return _read(document_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gdocs_replace_text(document_id: str, replacements: dict) -> dict:
    """Replace placeholder text throughout a Google Doc.

    Searches all text in the document and replaces matches (case-sensitive).

    Args:
        document_id: The Google Docs document ID.
        replacements: JSON object mapping placeholder strings to replacement values,
            e.g. {"{{name}}": "Alice", "{{date}}": "2026-02-13"}.
    """
    try:
        from roost.docs_service import gdocs_replace_text as _replace
        return _replace(document_id, replacements)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gdocs_create_from_template(
    template_id: str,
    name: str,
    replacements: str = "",
    folder_id: str = "",
) -> dict:
    """Create a new Google Doc by copying a template and filling placeholders.

    This is the primary workflow: copy a template, replace all {{placeholders}}, done.

    Args:
        template_id: ID of the template document to copy.
        name: Name for the new document.
        replacements: Optional JSON string of placeholder replacements,
            e.g. '{"{{name}}": "Alice"}'.  Empty = no replacements.
        folder_id: Optional Google Drive folder ID to place the copy in.
    """
    try:
        import json
        from roost.docs_service import gdocs_create_from_template as _create
        repl = json.loads(replacements) if replacements else None
        return _create(template_id, name, repl, folder_id or None)
    except json.JSONDecodeError as e:
        return {"error": f"Invalid JSON in replacements: {e}"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gdocs_append_text(
    document_id: str,
    text: str,
    style: str = "NORMAL_TEXT",
) -> dict:
    """Append text to the end of a Google Doc.

    Args:
        document_id: The Google Docs document ID.
        text: Text to append. Use newlines for paragraph breaks.
        style: Paragraph style — NORMAL_TEXT, HEADING_1, HEADING_2, HEADING_3,
            HEADING_4, HEADING_5, HEADING_6, TITLE, SUBTITLE.
    """
    try:
        from roost.docs_service import gdocs_append_text as _append
        return _append(document_id, text, style)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def gdocs_insert_image(
    document_id: str,
    image_url: str,
    width_pts: float = 468.0,
    height_pts: float = 300.0,
) -> dict:
    """Insert an image at the end of a Google Doc.

    The image must be accessible via a public URL.

    Args:
        document_id: The Google Docs document ID.
        image_url: Public URL of the image to insert.
        width_pts: Image width in points (default 468 = full page width).
        height_pts: Image height in points (default 300).
    """
    try:
        from roost.docs_service import gdocs_insert_image as _insert
        return _insert(document_id, image_url, width_pts, height_pts)
    except Exception as e:
        return {"error": str(e)}
