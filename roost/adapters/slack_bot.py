"""Slack bot adapter for Roost.

Runs as a standalone process: python -m roost.adapters.slack_bot

Uses Socket Mode (no public URL needed) for real-time messaging.

Requires: pip install slack-bolt
Config: SLACK_BOT_TOKEN, SLACK_APP_TOKEN (xapp-... for Socket Mode),
        SLACK_ALLOWED_USERS (comma-separated Slack user IDs, optional)
"""

import asyncio
import logging
import threading

try:
    from slack_bolt import App
    from slack_bolt.adapter.socket_mode import SocketModeHandler
except ImportError:
    raise ImportError("slack-bolt is required: pip install slack-bolt")

from roost.config import SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_ALLOWED_USERS
from roost.adapters import run_agent, truncate
from roost.events import subscribe, TASK_CREATED, TASK_UPDATED, TASK_COMPLETED

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("roost.slack")

# Slack Bolt app (sync SDK — async adapter wraps agent calls)
app = App(token=SLACK_BOT_TOKEN)

# Keep reference for notifications
_slack_client = None


def _is_authorized(user_id: str) -> bool:
    """Check if a Slack user is authorized. Empty allowlist = open access."""
    if not SLACK_ALLOWED_USERS:
        return True
    return user_id in SLACK_ALLOWED_USERS


@app.event("app_mention")
def handle_mention(event, say):
    """Handle @bot mentions in channels."""
    user_id = event.get("user", "")
    if not _is_authorized(user_id):
        say(f"You're not authorized. Your Slack user ID: {user_id}")
        return

    # Strip the bot mention from text
    text = event.get("text", "")
    # Remove <@BOT_ID> mention pattern
    import re
    text = re.sub(r"<@[A-Z0-9]+>", "", text).strip()

    if not text:
        return

    thread_ts = event.get("thread_ts") or event.get("ts")

    # Run agent in async context
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(
            run_agent(prompt=text, user_id=user_id, platform="slack")
        )
    finally:
        loop.close()

    # Reply in thread
    for chunk in _split_message(response, limit=3000):
        say(text=chunk, thread_ts=thread_ts)


@app.event("message")
def handle_dm(event, say):
    """Handle direct messages to the bot."""
    # Only handle DMs (channel type "im")
    if event.get("channel_type") != "im":
        return

    # Ignore bot messages and message_changed events
    if event.get("bot_id") or event.get("subtype"):
        return

    user_id = event.get("user", "")
    if not _is_authorized(user_id):
        say(f"You're not authorized. Your Slack user ID: {user_id}")
        return

    text = event.get("text", "").strip()
    if not text:
        return

    # Run agent in async context
    loop = asyncio.new_event_loop()
    try:
        response = loop.run_until_complete(
            run_agent(prompt=text, user_id=user_id, platform="slack")
        )
    finally:
        loop.close()

    for chunk in _split_message(response, limit=3000):
        say(text=chunk)


def _split_message(text: str, limit: int = 3000) -> list[str]:
    """Split message into Slack-friendly chunks."""
    if len(text) <= limit:
        return [text]

    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


def _init_notifier():
    """Subscribe to task events for Slack notifications."""
    global _slack_client

    from slack_sdk import WebClient
    _slack_client = WebClient(token=SLACK_BOT_TOKEN)

    async def _on_task_event(data: dict) -> None:
        if data.get("source") == "slack" or not _slack_client:
            return

        task = data.get("task")
        if not task:
            return

        source = data.get("source", "unknown")
        event_type = data.get("event_type", "updated")
        labels = {
            "task.created": "New task",
            "task.completed": "Task completed",
            "task.updated": "Task updated",
        }
        label = labels.get(event_type, "Task event")
        text = f"*{label}* (from {source}):\n#{task.id} {task.title}"

        for user_id in SLACK_ALLOWED_USERS:
            try:
                _slack_client.chat_postMessage(channel=user_id, text=text)
            except Exception:
                logger.debug("Failed to DM Slack user %s", user_id, exc_info=True)

    subscribe(TASK_CREATED, _on_task_event)
    subscribe(TASK_UPDATED, _on_task_event)
    subscribe(TASK_COMPLETED, _on_task_event)
    logger.info("Slack notifier subscribed to task events")


def main():
    if not SLACK_BOT_TOKEN:
        logger.error("SLACK_BOT_TOKEN not set in .env")
        return
    if not SLACK_APP_TOKEN:
        logger.error("SLACK_APP_TOKEN not set in .env (required for Socket Mode)")
        return

    _init_notifier()

    logger.info("Slack bot starting (Socket Mode)...")
    handler = SocketModeHandler(app, SLACK_APP_TOKEN)
    handler.start()


if __name__ == "__main__":
    main()
