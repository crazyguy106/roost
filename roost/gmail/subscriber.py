"""Event bus subscriber — send email notifications on task events.

Skips events from source="gmail" to prevent echo loops.
Runs sends in background threads so local operations never block.
"""

import logging
import threading
from roost.config import GMAIL_SEND_FROM

logger = logging.getLogger("roost.gmail.subscriber")


def _on_task_completed(data: dict) -> None:
    """Email notification when a task is completed."""
    if data.get("source") == "gmail":
        return
    task = data.get("task")
    if not task or not GMAIL_SEND_FROM:
        return

    def _send():
        try:
            from roost.gmail.service import send_task_notification
            send_task_notification(GMAIL_SEND_FROM, task, "completed")
        except Exception:
            logger.exception("Failed to send completion email")

    threading.Thread(target=_send, daemon=True).start()


def _on_deadline_approaching(data: dict) -> None:
    """Email notification for approaching deadlines."""
    task = data.get("task")
    if not task or not GMAIL_SEND_FROM:
        return

    def _send():
        try:
            from roost.gmail.service import send_task_notification
            send_task_notification(GMAIL_SEND_FROM, task, "deadline")
        except Exception:
            logger.exception("Failed to send deadline email")

    threading.Thread(target=_send, daemon=True).start()


def init_subscriber() -> None:
    """Wire up Gmail event bus subscribers. Call on startup if Gmail enabled."""
    from roost.gmail import is_gmail_available
    if not is_gmail_available():
        logger.info("Gmail not available — subscriber not initialized")
        return

    from roost.events import subscribe, TASK_COMPLETED, TASK_DEADLINE_APPROACHING

    subscribe(TASK_COMPLETED, _on_task_completed)
    subscribe(TASK_DEADLINE_APPROACHING, _on_deadline_approaching)

    logger.info("Gmail subscriber initialized (completion + deadline notifications)")
