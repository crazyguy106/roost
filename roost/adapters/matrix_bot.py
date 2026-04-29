"""Matrix bot adapter for Roost.

Runs as a standalone process: python -m roost.adapters.matrix_bot

Uses matrix-nio for Matrix protocol communication.

Requires: pip install matrix-nio
Config: MATRIX_HOMESERVER (e.g. https://matrix.org),
        MATRIX_USER_ID (e.g. @roost:matrix.org),
        MATRIX_ACCESS_TOKEN or MATRIX_PASSWORD,
        MATRIX_ALLOWED_USERS (comma-separated Matrix user IDs)
"""

import asyncio
import logging

try:
    from nio import (
        AsyncClient,
        MatrixRoom,
        RoomMessageText,
        InviteMemberEvent,
        LoginResponse,
    )
except ImportError:
    raise ImportError("matrix-nio is required: pip install matrix-nio")

from roost.config import (
    MATRIX_HOMESERVER, MATRIX_USER_ID, MATRIX_ACCESS_TOKEN,
    MATRIX_PASSWORD, MATRIX_ALLOWED_USERS,
)
from roost.adapters import run_agent, truncate
from roost.events import subscribe, TASK_CREATED, TASK_UPDATED, TASK_COMPLETED

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("roost.matrix")

# Track rooms for notifications
_client: AsyncClient | None = None


def _is_authorized(sender: str) -> bool:
    """Check if a Matrix user is authorized."""
    if not MATRIX_ALLOWED_USERS:
        return True
    return sender in MATRIX_ALLOWED_USERS


async def _message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
    """Handle incoming Matrix text messages."""
    # Ignore own messages
    if event.sender == MATRIX_USER_ID:
        return

    # Only respond in DMs (rooms with exactly 2 members) or when mentioned
    is_dm = room.member_count == 2
    is_mention = MATRIX_USER_ID in event.body or (
        hasattr(room, "display_name") and room.display_name in event.body
    )

    if not is_dm and not is_mention:
        return

    if not _is_authorized(event.sender):
        await _client.room_send(
            room.room_id,
            "m.room.message",
            {
                "msgtype": "m.text",
                "body": (
                    f"You're not authorized. Your Matrix ID: {event.sender}\n"
                    "Ask your admin to add you to MATRIX_ALLOWED_USERS."
                ),
            },
        )
        return

    # Strip mention from text
    text = event.body
    if is_mention and not is_dm:
        text = text.replace(MATRIX_USER_ID, "").strip()

    if not text:
        return

    logger.info("Matrix message from %s in %s: %s", event.sender, room.display_name, text[:80])

    # Send typing indicator
    await _client.room_typing(room.room_id, typing_state=True)

    response = await run_agent(
        prompt=text,
        user_id=event.sender,
        platform="matrix",
    )

    await _client.room_typing(room.room_id, typing_state=False)

    # Send response (split if needed)
    for chunk in _split_message(response, limit=4000):
        await _client.room_send(
            room.room_id,
            "m.room.message",
            {
                "msgtype": "m.text",
                "body": chunk,
                "format": "org.matrix.custom.html",
                "formatted_body": _markdown_to_html(chunk),
            },
        )


async def _invite_callback(room: MatrixRoom, event: InviteMemberEvent) -> None:
    """Auto-accept invites from authorized users."""
    if event.membership != "invite":
        return
    if event.state_key != MATRIX_USER_ID:
        return

    # Accept all invites (auth check happens on message)
    logger.info("Accepting Matrix invite to %s from %s", room.room_id, event.sender)
    await _client.join(room.room_id)


def _markdown_to_html(text: str) -> str:
    """Basic markdown to HTML conversion for Matrix formatted messages."""
    import re
    # Bold
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    # Italic
    text = re.sub(r"\*(.+?)\*", r"<em>\1</em>", text)
    # Code blocks
    text = re.sub(r"```(\w*)\n(.*?)```", r"<pre><code>\2</code></pre>", text, flags=re.DOTALL)
    # Inline code
    text = re.sub(r"`(.+?)`", r"<code>\1</code>", text)
    # Newlines
    text = text.replace("\n", "<br>")
    return text


def _split_message(text: str, limit: int = 4000) -> list[str]:
    """Split message into Matrix-friendly chunks."""
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
    """Subscribe to task events for Matrix notifications."""

    async def _on_task_event(data: dict) -> None:
        if data.get("source") == "matrix" or not _client:
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
        text = f"**{label}** (from {source}):\n#{task.id} {task.title}"

        # Send to all DM rooms with authorized users
        for room_id, room in _client.rooms.items():
            if room.member_count == 2:
                members = [m for m in room.users if m != MATRIX_USER_ID]
                if members and members[0] in MATRIX_ALLOWED_USERS:
                    try:
                        await _client.room_send(
                            room_id,
                            "m.room.message",
                            {"msgtype": "m.text", "body": text},
                        )
                    except Exception:
                        logger.debug("Failed to notify Matrix room %s", room_id, exc_info=True)

    subscribe(TASK_CREATED, _on_task_event)
    subscribe(TASK_UPDATED, _on_task_event)
    subscribe(TASK_COMPLETED, _on_task_event)
    logger.info("Matrix notifier subscribed to task events")


async def _run():
    """Main Matrix client loop."""
    global _client

    _client = AsyncClient(MATRIX_HOMESERVER, MATRIX_USER_ID)

    # Authenticate
    if MATRIX_ACCESS_TOKEN:
        _client.access_token = MATRIX_ACCESS_TOKEN
        _client.user_id = MATRIX_USER_ID
    elif MATRIX_PASSWORD:
        resp = await _client.login(MATRIX_PASSWORD)
        if not isinstance(resp, LoginResponse):
            logger.error("Matrix login failed: %s", resp)
            return
        logger.info("Matrix login successful, access_token: %s...", resp.access_token[:20])
    else:
        logger.error("Set MATRIX_ACCESS_TOKEN or MATRIX_PASSWORD in .env")
        return

    # Register callbacks
    _client.add_event_callback(_message_callback, RoomMessageText)
    _client.add_event_callback(_invite_callback, InviteMemberEvent)

    _init_notifier()

    # Initial sync to avoid processing old messages
    logger.info("Matrix bot starting initial sync...")
    await _client.sync(timeout=10000, full_state=True)
    logger.info("Matrix bot ready — listening for messages")

    # Continuous sync
    await _client.sync_forever(timeout=30000, full_state=True)


def main():
    if not MATRIX_HOMESERVER:
        logger.error("MATRIX_HOMESERVER not set in .env")
        return
    if not MATRIX_USER_ID:
        logger.error("MATRIX_USER_ID not set in .env")
        return

    logger.info("Matrix bot starting...")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
