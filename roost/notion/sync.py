"""Notion sync operations: push local changes, pull Notion edits, retry queue.

SQLite is the source of truth. Push operations are fire-and-forget
(queued on failure). Pull operations compare timestamps to resolve conflicts.
"""

import logging
from datetime import datetime

from roost.database import get_connection
from roost.notion.client import get_client, rate_limited_call
from roost.notion.databases import get_database_id, get_data_source_id
from roost.notion import mappers

logger = logging.getLogger("roost.notion.sync")


def _log_sync(table_name: str, local_id: int, notion_page_id: str,
              direction: str, status: str, error: str = "") -> None:
    """Record a sync operation in the log."""
    conn = get_connection()
    conn.execute(
        """INSERT INTO notion_sync_log
           (table_name, local_id, notion_page_id, direction, status, error_message)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (table_name, local_id, notion_page_id, direction, status, error),
    )
    conn.commit()
    conn.close()


def _update_sync_state(table_name: str) -> None:
    """Update the last sync timestamp for a table."""
    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO notion_sync_state (table_name, last_synced_at)
           VALUES (?, ?)
           ON CONFLICT(table_name) DO UPDATE SET
           last_synced_at = excluded.last_synced_at""",
        (table_name, now),
    )
    conn.commit()
    conn.close()


# ── Push operations ──────────────────────────────────────────────────

def push_task(task) -> bool:
    """Push a task to Notion. Creates or updates the Notion page."""
    client = get_client()
    if not client:
        return False

    db_id = get_database_id("tasks")
    if not db_id:
        return False

    properties = mappers.task_to_notion(task)
    notion_page_id = getattr(task, "notion_page_id", None)

    try:
        if notion_page_id:
            # Update existing page
            rate_limited_call(
                client.pages.update,
                page_id=notion_page_id,
                properties=properties,
            )
        else:
            # Create new page
            result = rate_limited_call(
                client.pages.create,
                parent={"database_id": db_id},
                properties=properties,
            )
            notion_page_id = result["id"]
            # Store the Notion page ID locally
            conn = get_connection()
            conn.execute(
                "UPDATE tasks SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, task.id),
            )
            conn.commit()
            conn.close()

        _log_sync("tasks", task.id, notion_page_id, "push", "success")
        _update_sync_state("tasks")
        return True

    except Exception as e:
        logger.exception("Failed to push task #%d to Notion", task.id)
        _log_sync("tasks", task.id, notion_page_id or "", "push", "failed", str(e))
        return False


def push_project(project) -> bool:
    """Push a project to Notion."""
    client = get_client()
    if not client:
        return False

    db_id = get_database_id("projects")
    if not db_id:
        return False

    properties = mappers.project_to_notion(project)
    notion_page_id = getattr(project, "notion_page_id", None)

    try:
        if notion_page_id:
            rate_limited_call(
                client.pages.update,
                page_id=notion_page_id,
                properties=properties,
            )
        else:
            result = rate_limited_call(
                client.pages.create,
                parent={"database_id": db_id},
                properties=properties,
            )
            notion_page_id = result["id"]
            conn = get_connection()
            conn.execute(
                "UPDATE projects SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, project.id),
            )
            conn.commit()
            conn.close()

        _log_sync("projects", project.id, notion_page_id, "push", "success")
        _update_sync_state("projects")
        return True

    except Exception as e:
        logger.exception("Failed to push project #%d to Notion", project.id)
        _log_sync("projects", project.id, notion_page_id or "", "push", "failed", str(e))
        return False


def push_note(note) -> bool:
    """Push a note to Notion."""
    client = get_client()
    if not client:
        return False

    db_id = get_database_id("notes")
    if not db_id:
        return False

    properties = mappers.note_to_notion(note)
    notion_page_id = getattr(note, "notion_page_id", None)

    try:
        if notion_page_id:
            rate_limited_call(
                client.pages.update,
                page_id=notion_page_id,
                properties=properties,
            )
        else:
            result = rate_limited_call(
                client.pages.create,
                parent={"database_id": db_id},
                properties=properties,
            )
            notion_page_id = result["id"]
            conn = get_connection()
            conn.execute(
                "UPDATE notes SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, note.id),
            )
            conn.commit()
            conn.close()

        _log_sync("notes", note.id, notion_page_id, "push", "success")
        _update_sync_state("notes")
        return True

    except Exception as e:
        logger.exception("Failed to push note #%d to Notion", note.id)
        _log_sync("notes", note.id, notion_page_id or "", "push", "failed", str(e))
        return False


# ── Curriculum push operations ───────────────────────────────────────

def push_curriculum(curriculum, module_count: int = 0) -> bool:
    """Push a curriculum/programme to Notion. Creates or updates."""
    client = get_client()
    if not client:
        return False

    db_id = get_database_id("programmes")
    if not db_id:
        return False

    properties = mappers.curriculum_to_notion(curriculum, module_count)
    notion_page_id = getattr(curriculum, "notion_page_id", None)

    try:
        if notion_page_id:
            rate_limited_call(
                client.pages.update,
                page_id=notion_page_id,
                properties=properties,
            )
        else:
            result = rate_limited_call(
                client.pages.create,
                parent={"database_id": db_id},
                properties=properties,
            )
            notion_page_id = result["id"]
            conn = get_connection()
            conn.execute(
                "UPDATE curricula SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, curriculum.id),
            )
            conn.commit()
            conn.close()

        _log_sync("curricula", curriculum.id, notion_page_id, "push", "success")
        _update_sync_state("programmes")
        return True

    except Exception as e:
        logger.exception("Failed to push curriculum #%d to Notion", curriculum.id)
        _log_sync("curricula", curriculum.id, notion_page_id or "", "push", "failed", str(e))
        return False


def push_curriculum_module(module, programme_notion_id: str | None = None,
                           phase_name: str = "") -> bool:
    """Push a curriculum module to Notion. Creates or updates."""
    client = get_client()
    if not client:
        return False

    db_id = get_database_id("programme_modules")
    if not db_id:
        return False

    properties = mappers.curriculum_module_to_notion(
        module, programme_notion_id, phase_name
    )
    notion_page_id = getattr(module, "notion_page_id", None)

    try:
        if notion_page_id:
            rate_limited_call(
                client.pages.update,
                page_id=notion_page_id,
                properties=properties,
            )
        else:
            result = rate_limited_call(
                client.pages.create,
                parent={"database_id": db_id},
                properties=properties,
            )
            notion_page_id = result["id"]
            conn = get_connection()
            conn.execute(
                "UPDATE curriculum_modules SET notion_page_id = ? WHERE id = ?",
                (notion_page_id, module.id),
            )
            conn.commit()
            conn.close()

        _log_sync("curriculum_modules", module.id, notion_page_id, "push", "success")
        _update_sync_state("programme_modules")
        return True

    except Exception as e:
        logger.exception("Failed to push module #%d to Notion", module.id)
        _log_sync("curriculum_modules", module.id, notion_page_id or "", "push", "failed", str(e))
        return False


def bulk_export_curricula_to_notion() -> dict:
    """Export all curricula and their modules to Notion.

    Pushes all 4 programmes first, then all 44 modules with programme relations.
    Returns stats dict with counts.
    """
    from roost import task_service

    stats = {"programmes": 0, "modules": 0}

    curricula = task_service.list_curricula()

    # Push programmes first
    for curriculum in curricula:
        modules = task_service.list_curriculum_modules(curriculum.id)
        if push_curriculum(curriculum, module_count=len(modules)):
            stats["programmes"] += 1

    # Reload curricula to get notion_page_ids
    curricula = task_service.list_curricula()

    # Build phase name lookup per curriculum
    conn = get_connection()
    phase_names = {}
    for c in curricula:
        rows = conn.execute(
            "SELECT phase_number, name FROM curriculum_phases WHERE curriculum_id = ?",
            (c.id,),
        ).fetchall()
        phase_names[c.id] = {r["phase_number"]: r["name"] for r in rows}
    conn.close()

    # Push modules with programme relation
    for curriculum in curricula:
        modules = task_service.list_curriculum_modules(curriculum.id)
        pn = phase_names.get(curriculum.id, {})
        for module in modules:
            phase_name = pn.get(module.phase, f"Phase {module.phase}")
            if push_curriculum_module(
                module,
                programme_notion_id=curriculum.notion_page_id,
                phase_name=phase_name,
            ):
                stats["modules"] += 1

    logger.info("Bulk curriculum export complete: %s", stats)
    return stats


# ── Pull operations ──────────────────────────────────────────────────

def pull_changes() -> int:
    """Pull changes from Notion databases, update local SQLite.

    Compares timestamps: if Notion's last_edited_time > local updated_at,
    apply Notion changes. Otherwise skip (local is newer).

    Returns number of records updated.
    """
    client = get_client()
    if not client:
        return 0

    updated = 0

    # Pull task changes
    updated += _pull_table_changes(
        client, "tasks",
        mappers.notion_to_task_updates,
        _apply_task_update,
    )

    # Pull project changes
    updated += _pull_table_changes(
        client, "projects",
        mappers.notion_to_project_updates,
        _apply_project_update,
    )

    # Pull note changes
    updated += _pull_table_changes(
        client, "notes",
        mappers.notion_to_note_updates,
        _apply_note_update,
    )

    return updated


def _pull_table_changes(client, table_name: str, mapper_fn, apply_fn) -> int:
    """Generic pull logic for a single table."""
    db_id = get_database_id(table_name)
    if not db_id:
        return 0

    # Get data_source_id (API v2025-09-03 queries via data_sources)
    ds_id = get_data_source_id(db_id)
    if not ds_id:
        logger.warning("No data_source_id for %s (db=%s), skipping pull", table_name, db_id)
        return 0

    # Get last sync time
    conn = get_connection()
    state = conn.execute(
        "SELECT last_synced_at FROM notion_sync_state WHERE table_name = ?",
        (table_name,),
    ).fetchone()
    conn.close()

    last_sync = state["last_synced_at"] if state else None

    try:
        # Query Notion for recently modified pages
        filter_params = {}
        if last_sync:
            filter_params = {
                "filter": {
                    "timestamp": "last_edited_time",
                    "last_edited_time": {"after": last_sync},
                }
            }

        result = rate_limited_call(
            client.data_sources.query,
            data_source_id=ds_id,
            **filter_params,
        )

        updated = 0
        for page in result.get("results", []):
            props = page.get("properties", {})
            local_id = mappers._get_number(props, "Local ID")
            if not local_id:
                continue

            notion_edited = page.get("last_edited_time", "")
            updates = mapper_fn(props)
            if updates:
                if apply_fn(int(local_id), updates, notion_edited, page["id"]):
                    updated += 1

        _update_sync_state(table_name)
        return updated

    except Exception:
        logger.exception("Failed to pull changes for %s", table_name)
        return 0


def _apply_task_update(local_id: int, updates: dict, notion_edited: str,
                       notion_page_id: str) -> bool:
    """Apply task updates from Notion, respecting conflict resolution."""
    conn = get_connection()
    row = conn.execute(
        "SELECT updated_at FROM tasks WHERE id = ?", (local_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    local_updated = row["updated_at"]
    # SQLite wins if local is newer
    if local_updated and notion_edited:
        if local_updated >= notion_edited[:19]:
            conn.close()
            _log_sync("tasks", local_id, notion_page_id, "pull", "conflict",
                      "Local is newer, skipping")
            return False

    set_parts = []
    values = []
    for k, v in updates.items():
        set_parts.append(f"{k} = ?")
        values.append(v)
    set_parts.append("updated_at = ?")
    values.append(datetime.now().isoformat(timespec="seconds"))

    values.append(local_id)
    conn.execute(
        f"UPDATE tasks SET {', '.join(set_parts)} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()

    _log_sync("tasks", local_id, notion_page_id, "pull", "success")
    return True


def _apply_project_update(local_id: int, updates: dict, notion_edited: str,
                          notion_page_id: str) -> bool:
    """Apply project updates from Notion."""
    conn = get_connection()
    row = conn.execute(
        "SELECT updated_at FROM projects WHERE id = ?", (local_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    local_updated = row["updated_at"]
    if local_updated and notion_edited:
        if local_updated >= notion_edited[:19]:
            conn.close()
            return False

    set_parts = []
    values = []
    for k, v in updates.items():
        set_parts.append(f"{k} = ?")
        values.append(v)
    set_parts.append("updated_at = ?")
    values.append(datetime.now().isoformat(timespec="seconds"))

    values.append(local_id)
    conn.execute(
        f"UPDATE projects SET {', '.join(set_parts)} WHERE id = ?",
        values,
    )
    conn.commit()
    conn.close()

    _log_sync("projects", local_id, notion_page_id, "pull", "success")
    return True


def _apply_note_update(local_id: int, updates: dict, notion_edited: str,
                       notion_page_id: str) -> bool:
    """Apply note updates from Notion."""
    conn = get_connection()
    row = conn.execute(
        "SELECT created_at FROM notes WHERE id = ?", (local_id,)
    ).fetchone()

    if not row:
        conn.close()
        return False

    # Notes don't have updated_at — use created_at for comparison
    # Be conservative: only update if we have changes
    if updates:
        set_parts = []
        values = []
        for k, v in updates.items():
            set_parts.append(f"{k} = ?")
            values.append(v)
        values.append(local_id)
        conn.execute(
            f"UPDATE notes SET {', '.join(set_parts)} WHERE id = ?",
            values,
        )
        conn.commit()

    conn.close()
    _log_sync("notes", local_id, notion_page_id, "pull", "success")
    return True


# ── Bulk export ──────────────────────────────────────────────────────

def bulk_export_to_notion() -> dict:
    """Export all local data to Notion. Used for initial setup."""
    from roost import task_service

    stats = {"tasks": 0, "projects": 0, "notes": 0}

    # Export projects first (tasks may reference them)
    for project in task_service.list_projects():
        if push_project(project):
            stats["projects"] += 1

    # Export tasks
    for task in task_service.list_tasks():
        if push_task(task):
            stats["tasks"] += 1

    # Export notes
    for note in task_service.list_notes(limit=500):
        if push_note(note):
            stats["notes"] += 1

    logger.info("Bulk export complete: %s", stats)
    return stats


# ── Retry queue ──────────────────────────────────────────────────────

def process_retry_queue() -> int:
    """Process failed sync operations from the log."""
    conn = get_connection()
    failed = conn.execute(
        """SELECT DISTINCT table_name, local_id
           FROM notion_sync_log
           WHERE status = 'failed' AND direction = 'push'
           ORDER BY created_at DESC LIMIT 50"""
    ).fetchall()
    conn.close()

    if not failed:
        return 0

    from roost import task_service

    retried = 0
    for row in failed:
        table = row["table_name"]
        local_id = row["local_id"]
        success = False

        try:
            if table == "tasks":
                task = task_service.get_task(local_id)
                if task:
                    success = push_task(task)
            elif table == "projects":
                project = task_service.get_project(local_id)
                if project:
                    success = push_project(project)
            elif table == "notes":
                note = task_service.get_note(local_id)
                if note:
                    success = push_note(note)
        except Exception:
            logger.exception("Retry failed for %s #%d", table, local_id)

        if success:
            retried += 1

    logger.info("Retry queue: processed %d/%d items", retried, len(failed))
    return retried
