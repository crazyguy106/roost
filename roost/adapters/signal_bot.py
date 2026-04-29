"""Signal bot adapter for Roost.

Runs as a standalone process: python -m roost.adapters.signal_bot

Uses signal-cli-rest-api (runs as a Docker container) for sending/receiving.
See: https://github.com/bbernhard/signal-cli-rest-api

Requires: pip install httpx (already a Roost dependency)
Config: SIGNAL_API_URL (e.g. http://localhost:8080),
        SIGNAL_PHONE_NUMBER (bot's registered number, e.g. +1234567890),
        SIGNAL_ALLOWED_NUMBERS (comma-separated phone numbers)
"""

import asyncio
import logging

import httpx

from roost.config import (
    SIGNAL_API_URL, SIGNAL_PHONE_NUMBER, SIGNAL_ALLOWED_NUMBERS,
)
from roost.adapters import run_agent, truncate
from roost.events import subscribe, TASK_CREATED, TASK_UPDATED, TASK_COMPLETED

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("roost.signal")

# Polling interval for checking new messages (seconds)
_POLL_INTERVAL = 2


def _is_authorized(sender: str) -> bool:
    """Check if a phone number is in the allowlist."""
    if not SIGNAL_ALLOWED_NUMBERS:
        return True
    # Normalize: strip spaces, ensure + prefix
    normalized = sender.strip().replace(" ", "")
    return normalized in SIGNAL_ALLOWED_NUMBERS


async def _send_message(recipient: str, text: str) -> None:
    """Send a Signal message via signal-cli-rest-api."""
    url = f"{SIGNAL_API_URL}/v2/send"
    payload = {
        "message": text,
        "number": SIGNAL_PHONE_NUMBER,
        "recipients": [recipient],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code not in (200, 201):
            logger.warning("Signal send failed (%s): %s", resp.status_code, resp.text[:200])


async def _receive_messages() -> list[dict]:
    """Poll signal-cli-rest-api for new messages."""
    url = f"{SIGNAL_API_URL}/v1/receive/{SIGNAL_PHONE_NUMBER}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            return resp.json()
        except Exception:
            return []


async def _handle_message(envelope: dict) -> None:
    """Process a single Signal message envelope."""
    data_msg = envelope.get("dataMessage")
    if not data_msg:
        return

    text = data_msg.get("message", "").strip()
    if not text:
        return

    sender = envelope.get("source", "")
    if not sender:
        return

    if not _is_authorized(sender):
        await _send_message(
            sender,
            f"You're not authorized. Your number: {sender}\n"
            "Ask your admin to add you to SIGNAL_ALLOWED_NUMBERS."
        )
        return

    logger.info("Signal message from %s: %s", sender, text[:80])

    response = await run_agent(
        prompt=text,
        user_id=sender,
        platform="signal",
    )

    # Signal has no hard character limit, but keep chunks reasonable
    for chunk in _split_message(response, limit=2000):
        await _send_message(sender, chunk)


def _split_message(text: str, limit: int = 2000) -> list[str]:
    """Split message into chunks."""
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
    """Subscribe to task events for Signal notifications."""

    async def _on_task_event(data: dict) -> None:
        if data.get("source") == "signal":
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
        text = f"{label} (from {source}):\n#{task.id} {task.title}"

        for number in SIGNAL_ALLOWED_NUMBERS:
            try:
                await _send_message(number, text)
            except Exception:
                logger.debug("Failed to notify Signal user %s", number, exc_info=True)

    subscribe(TASK_CREATED, _on_task_event)
    subscribe(TASK_UPDATED, _on_task_event)
    subscribe(TASK_COMPLETED, _on_task_event)
    logger.info("Signal notifier subscribed to task events")


async def _poll_loop():
    """Main polling loop — check for new messages and process them."""
    logger.info("Signal bot polling started (number: %s)", SIGNAL_PHONE_NUMBER)

    while True:
        try:
            envelopes = await _receive_messages()
            for envelope in envelopes:
                try:
                    await _handle_message(envelope.get("envelope", envelope))
                except Exception:
                    logger.exception("Error handling Signal message")
        except Exception:
            logger.exception("Error in Signal poll loop")

        await asyncio.sleep(_POLL_INTERVAL)


def main():
    if not SIGNAL_API_URL:
        logger.error("SIGNAL_API_URL not set in .env")
        return
    if not SIGNAL_PHONE_NUMBER:
        logger.error("SIGNAL_PHONE_NUMBER not set in .env")
        return

    _init_notifier()

    logger.info("Signal bot starting (polling signal-cli-rest-api)...")
    asyncio.run(_poll_loop())


if __name__ == "__main__":
    main()
