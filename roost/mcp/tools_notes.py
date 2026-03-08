"""MCP tools for Notes CRUD."""

from roost.mcp.server import mcp


@mcp.tool()
def create_note(
    content: str,
    title: str = "",
    tag: str = "",
    project_id: int | None = None,
) -> dict:
    """Create a quick note.

    Args:
        content: Note text content.
        title: Optional short title (shown in list views). Auto-generated from first line if empty.
        tag: Optional tag for categorisation (e.g. "idea", "meeting", "voice").
        project_id: Optional project ID to link the note to.
    """
    try:
        from roost.services.notes import create_note as _create
        from roost.models import NoteCreate

        data = NoteCreate(title=title, content=content, tag=tag, project_id=project_id)
        note = _create(data, source="mcp")
        return note.model_dump()
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_note(note_id: int) -> dict:
    """Get a single note by ID.

    Args:
        note_id: The note ID.
    """
    try:
        from roost.services.notes import get_note as _get

        note = _get(note_id)
        if not note:
            return {"error": f"Note {note_id} not found"}
        return {**note.model_dump(), "display_title": note.display_title}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_notes(
    tag: str | None = None,
    project: str | None = None,
    limit: int = 50,
) -> dict:
    """List recent notes with optional filters.

    Args:
        tag: Filter by exact tag match.
        project: Filter by project name (case-insensitive).
        limit: Maximum notes to return (default 50).
    """
    try:
        from roost.services.notes import list_notes as _list

        notes = _list(tag=tag, project=project, limit=limit)
        return {
            "count": len(notes),
            "notes": [{**n.model_dump(), "display_title": n.display_title} for n in notes],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def delete_note(note_id: int) -> dict:
    """Delete a note by ID.

    Args:
        note_id: The note ID to delete.
    """
    try:
        from roost.services.notes import delete_note as _delete

        deleted = _delete(note_id)
        if not deleted:
            return {"error": f"Note {note_id} not found"}
        return {"ok": True, "deleted_id": note_id}
    except Exception as e:
        return {"error": str(e)}
