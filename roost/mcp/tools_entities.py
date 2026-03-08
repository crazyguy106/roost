"""MCP tools for entity (company/org) queries."""

from roost.mcp.server import mcp


@mcp.tool()
def list_entities(status: str | None = None) -> dict:
    """List all entities (companies/organisations).

    Args:
        status: Optional filter by status (e.g. 'active').
    """
    try:
        from roost.task_service import list_entities as _list

        entities = _list(status=status)
        return {
            "count": len(entities),
            "entities": [_entity_dict(e) for e in entities],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_entity(entity_id: int) -> dict:
    """Get an entity with its associated projects and people.

    Args:
        entity_id: The entity ID.
    """
    try:
        from roost.task_service import get_entity_tree

        tree = get_entity_tree(entity_id)
        if not tree:
            return {"error": f"Entity {entity_id} not found"}

        entity = tree["entity"]
        return {
            "entity": _entity_dict(entity),
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "status": p.status,
                    "task_count": p.task_count,
                }
                for p in tree["projects"]
            ],
            "people": [
                {
                    "contact_id": ce.contact_id,
                    "contact_name": ce.contact_name,
                    "title": ce.title,
                    "is_primary": bool(ce.is_primary),
                }
                for ce in tree["people"]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


def _entity_dict(entity) -> dict:
    """Convert an Entity model to a plain dict."""
    return {
        "id": entity.id,
        "name": entity.name,
        "description": entity.description,
        "status": entity.status,
        "notes": entity.notes,
        "project_count": entity.project_count,
        "contact_count": entity.contact_count,
        "created_at": entity.created_at,
        "updated_at": entity.updated_at,
    }
