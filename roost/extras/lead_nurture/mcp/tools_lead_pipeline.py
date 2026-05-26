"""MCP tools for Practical Cyber lead pipeline management."""

from roost.mcp.server import mcp


@mcp.tool()
def list_pipeline_leads(status: str = "") -> dict:
    """List leads in the Practical Cyber pipeline.

    Args:
        status: Filter by task status (todo, in_progress, completed). Empty = all active.
    """
    try:
        from roost.services.tasks import list_tasks
        from roost.services.projects import list_projects

        # Find pipeline project
        projects = list_projects()
        proj = next((p for p in projects if p.name == "Practical Cyber Pipeline"), None)
        if not proj:
            return {"leads": [], "message": "No pipeline project found. No leads captured yet."}

        tasks = list_tasks(
            project_id=proj.id,
            status=status or None,
        )

        leads = []
        for t in tasks:
            leads.append({
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "deadline": t.deadline.isoformat() if t.deadline else None,
                "created": t.created_at.isoformat() if t.created_at else None,
            })

        return {"project_id": proj.id, "count": len(leads), "leads": leads}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_pipeline_stage(
    task_id: int,
    stage: str,
    notes: str = "",
) -> dict:
    """Update a lead's pipeline stage.

    Args:
        task_id: The pipeline task ID.
        stage: New stage — one of: lead, contacted, meeting_booked, proposal, won, lost.
        notes: Optional notes about the stage change.
    """
    try:
        from roost.services.tasks import get_task, update_task
        from roost.models import TaskUpdate, TaskStatus

        valid_stages = ["lead", "contacted", "meeting_booked", "proposal", "won", "lost"]
        if stage not in valid_stages:
            return {"error": f"Invalid stage. Must be one of: {', '.join(valid_stages)}"}

        task = get_task(task_id)
        if not task:
            return {"error": f"Task {task_id} not found"}

        # Map stages to task statuses
        status_map = {
            "lead": TaskStatus.TODO,
            "contacted": TaskStatus.IN_PROGRESS,
            "meeting_booked": TaskStatus.IN_PROGRESS,
            "proposal": TaskStatus.IN_PROGRESS,
            "won": TaskStatus.COMPLETED,
            "lost": TaskStatus.COMPLETED,
        }

        # Update description with stage info
        desc = task.description or ""
        # Replace existing pipeline stage line
        lines = desc.split("\n")
        new_lines = [l for l in lines if not l.startswith("Pipeline stage:")]
        new_lines.append(f"Pipeline stage: {stage}")
        if notes:
            new_lines.append(f"[{stage}] {notes}")

        update_task(
            task_id,
            TaskUpdate(
                status=status_map[stage],
                description="\n".join(new_lines),
            ),
        )

        return {"ok": True, "task_id": task_id, "stage": stage}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def cancel_lead_emails(email: str) -> dict:
    """Cancel all pending scheduled emails for a lead.

    Use when a lead converts (meeting booked) or opts out.

    Args:
        email: The lead's email address.
    """
    try:
        from roost.services.scheduled_emails import list_scheduled_emails, cancel_scheduled_email

        pending = list_scheduled_emails(status="pending")
        cancelled = 0
        for e in pending:
            if e.get("to_addr", "").lower() == email.lower():
                cancel_scheduled_email(e["id"])
                cancelled += 1

        return {"ok": True, "cancelled": cancelled, "email": email}
    except Exception as e:
        return {"error": str(e)}
