"""MCP tools for project management."""

from roost.mcp.server import mcp


@mcp.tool()
def list_projects(
    status: str | None = None,
    category: str | None = None,
    top_level_only: bool = True,
    entity_id: int | None = None,
) -> dict:
    """List projects with optional filters.

    Args:
        status: Filter by status: active, paused, archived.
        category: Filter by category.
        top_level_only: If true, exclude sub-projects (default true).
        entity_id: Filter by entity/organisation ID.
    """
    try:
        from roost.task_service import list_projects as _list

        projects = _list(
            status=status,
            category=category,
            top_level_only=top_level_only,
            entity_id=entity_id,
        )
        return {
            "count": len(projects),
            "projects": [_project_dict(p) for p in projects],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_project(project_id: int) -> dict:
    """Get a project with its children and team assignments.

    Args:
        project_id: The project ID.
    """
    try:
        from roost.task_service import get_project_tree

        tree = get_project_tree(project_id)
        if not tree:
            return {"error": f"Project {project_id} not found"}

        project = tree["project"]
        return {
            "project": _project_dict(project),
            "children": [_project_dict(c) for c in tree["children"]],
            "assignments": [
                {
                    "contact_id": a.contact_id,
                    "contact_name": a.contact_name,
                    "entity_name": a.entity_name,
                    "role": a.role,
                    "role_label": a.role_label,
                }
                for a in tree["assignments"]
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def create_project(
    name: str,
    description: str = "",
    category: str = "",
    project_type: str = "project",
    entity_id: int | None = None,
    parent_project_id: int | None = None,
) -> dict:
    """Create a new project.

    Args:
        name: Project name.
        description: Project description.
        category: Optional category label.
        project_type: One of: project, initiative, programme.
        entity_id: Entity/organisation ID to associate with.
        parent_project_id: Parent project ID (for sub-projects).
    """
    try:
        from roost.models import ProjectCreate
        from roost.task_service import create_project as _create

        data = ProjectCreate(
            name=name,
            description=description,
            category=category,
            project_type=project_type,
            entity_id=entity_id,
            parent_project_id=parent_project_id,
        )
        project = _create(data, source="mcp")
        return _project_dict(project)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_project(
    project_id: int,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    status: str | None = None,
    project_type: str | None = None,
    entity_id: int | None = None,
    parent_project_id: int | None = None,
    pinned: bool | None = None,
) -> dict:
    """Update an existing project.

    Args:
        project_id: The project ID to update.
        name: New name.
        description: New description.
        category: New category.
        status: New status: active, paused, archived.
        project_type: New type: project, initiative, programme.
        entity_id: New entity ID.
        parent_project_id: New parent project ID.
        pinned: Pin/unpin the project.
    """
    try:
        from roost.models import ProjectUpdate
        from roost.task_service import update_project as _update

        data = ProjectUpdate(
            name=name,
            description=description,
            category=category,
            status=status,
            project_type=project_type,
            entity_id=entity_id,
            parent_project_id=parent_project_id,
            pinned=pinned,
        )
        project = _update(project_id, data, source="mcp")
        if not project:
            return {"error": f"Project {project_id} not found"}
        return _project_dict(project)
    except Exception as e:
        return {"error": str(e)}


def _project_dict(project) -> dict:
    """Convert a Project model to a plain dict."""
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "category": project.category,
        "status": project.status,
        "project_type": project.project_type,
        "entity_id": project.entity_id,
        "entity_name": project.entity_name,
        "parent_project_id": project.parent_project_id,
        "parent_project_name": project.parent_project_name,
        "task_count": project.task_count,
        "children_count": project.children_count,
        "pinned": bool(project.pinned),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }
