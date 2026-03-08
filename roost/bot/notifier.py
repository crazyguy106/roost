"""Telegram notification subscriber for task events.

Sends Telegram messages to authorized users when tasks change from
non-Telegram sources (web, CLI, API). Skips events where
source == "telegram" to avoid echo loops.
"""

import logging
from telegram.ext import Application
from roost.config import TELEGRAM_ALLOWED_USERS
from roost.events import subscribe, TASK_CREATED, TASK_UPDATED, TASK_COMPLETED

logger = logging.getLogger("roost.notifier")

_app: Application | None = None


def init_notifier(app: Application) -> None:
    """Initialize the notifier with the Telegram application instance.

    Call this during bot startup after the Application is built.
    """
    global _app
    _app = app

    subscribe(TASK_CREATED, _on_task_created)
    subscribe(TASK_UPDATED, _on_task_updated)
    subscribe(TASK_COMPLETED, _on_task_completed)
    logger.info("Notifier initialized — subscribed to task events")


async def _send_to_authorized(text: str) -> None:
    """Send a message to all authorized Telegram users."""
    if not _app or not _app.bot:
        return
    for user_id in TELEGRAM_ALLOWED_USERS:
        try:
            await _app.bot.send_message(chat_id=user_id, text=text)
        except Exception:
            logger.exception("Failed to notify user %s", user_id)


async def _on_task_created(data: dict) -> None:
    """Notify when a task is created from a non-Telegram source."""
    if data.get("source") == "telegram":
        return

    task = data.get("task")
    if not task:
        return

    source = data.get("source", "unknown")
    text = (
        f"📋 New task (from {source}):\n"
        f"#{task.id} {task.title}"
    )
    await _send_to_authorized(text)


async def _on_task_updated(data: dict) -> None:
    """Notify when a task is updated from a non-Telegram source."""
    if data.get("source") == "telegram":
        return

    task = data.get("task")
    if not task:
        return

    source = data.get("source", "unknown")
    text = (
        f"✏️ Task updated (from {source}):\n"
        f"#{task.id} {task.title} → {task.status.value}"
    )
    await _send_to_authorized(text)


async def _on_task_completed(data: dict) -> None:
    """Notify when a task is completed from a non-Telegram source."""
    if data.get("source") == "telegram":
        return

    task = data.get("task")
    if not task:
        return

    source = data.get("source", "unknown")
    text = (
        f"✅ Task completed (from {source}):\n"
        f"#{task.id} {task.title}"
    )
    await _send_to_authorized(text)
