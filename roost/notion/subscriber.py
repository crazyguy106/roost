"""Event bus subscribers: push to Notion on local writes.

Runs pushes in background threads so local operations never block.
Skips events with source="notion" to prevent echo loops.
"""

import logging
import threading

logger = logging.getLogger("roost.notion.subscriber")


def _push_in_background(fn, *args):
    """Run a push function in a background thread."""
    t = threading.Thread(target=fn, args=args, daemon=True)
    t.start()


def _on_task_created(data: dict) -> None:
    """Handle task.created event."""
    if data.get("source") == "notion":
        return
    task = data.get("task")
    if not task:
        return
    from roost.notion.sync import push_task
    _push_in_background(push_task, task)


def _on_task_updated(data: dict) -> None:
    """Handle task.updated event."""
    if data.get("source") == "notion":
        return
    task = data.get("task")
    if not task:
        return
    from roost.notion.sync import push_task
    _push_in_background(push_task, task)


def _on_task_completed(data: dict) -> None:
    """Handle task.completed event."""
    if data.get("source") == "notion":
        return
    task = data.get("task")
    if not task:
        return
    from roost.notion.sync import push_task
    _push_in_background(push_task, task)


def _on_project_created(data: dict) -> None:
    """Handle project.created event."""
    if data.get("source") == "notion":
        return
    project = data.get("project")
    if not project:
        return
    from roost.notion.sync import push_project
    _push_in_background(push_project, project)


def _on_project_updated(data: dict) -> None:
    """Handle project.updated event."""
    if data.get("source") == "notion":
        return
    project = data.get("project")
    if not project:
        return
    from roost.notion.sync import push_project
    _push_in_background(push_project, project)


def _on_note_created(data: dict) -> None:
    """Handle note.created event."""
    if data.get("source") == "notion":
        return
    note = data.get("note")
    if not note:
        return
    from roost.notion.sync import push_note
    _push_in_background(push_note, note)


def _on_curriculum_registered(data: dict) -> None:
    """Handle curriculum.registered event."""
    if data.get("source") == "notion":
        return
    curriculum = data.get("curriculum")
    module_count = data.get("module_count", 0)
    if not curriculum:
        return
    from roost.notion.sync import push_curriculum
    _push_in_background(push_curriculum, curriculum, module_count)


def _on_curriculum_updated(data: dict) -> None:
    """Handle curriculum.updated event."""
    if data.get("source") == "notion":
        return
    curriculum = data.get("curriculum")
    module_count = data.get("module_count", 0)
    if not curriculum:
        return
    from roost.notion.sync import push_curriculum
    _push_in_background(push_curriculum, curriculum, module_count)


def init_subscriber() -> None:
    """Register all Notion event subscribers.

    Call this on startup if NOTION_SYNC_ENABLED is true.
    """
    from roost.notion import is_notion_available

    if not is_notion_available():
        logger.info("Notion not available, subscriber not initialized")
        return

    from roost.events import subscribe, TASK_CREATED, TASK_UPDATED, TASK_COMPLETED
    from roost.events import PROJECT_CREATED, PROJECT_UPDATED, NOTE_CREATED
    from roost.events import CURRICULUM_REGISTERED, CURRICULUM_UPDATED

    subscribe(TASK_CREATED, _on_task_created)
    subscribe(TASK_UPDATED, _on_task_updated)
    subscribe(TASK_COMPLETED, _on_task_completed)
    subscribe(PROJECT_CREATED, _on_project_created)
    subscribe(PROJECT_UPDATED, _on_project_updated)
    subscribe(NOTE_CREATED, _on_note_created)
    subscribe(CURRICULUM_REGISTERED, _on_curriculum_registered)
    subscribe(CURRICULUM_UPDATED, _on_curriculum_updated)

    logger.info("Notion subscriber initialized — 8 event handlers registered")
