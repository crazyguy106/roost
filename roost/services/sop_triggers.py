"""SOP event triggers — fire automation recipes on system events.

Extends the recipe system's `event` trigger type with concrete event names:
  - email_received: New email arrives (after polling)
  - task_completed: A task is marked done
  - task_created: A new task is created
  - calendar_event_starting: A calendar event is about to start
  - contact_created: A new contact is added
  - webhook: External webhook received

Recipes with trigger_type='event' and trigger_config matching the event
name are executed automatically, subject to their risk_tier rules.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Valid event types
EVENT_TYPES = {
    "email_received",
    "task_completed",
    "task_created",
    "calendar_event_starting",
    "contact_created",
    "webhook",
}


async def fire_event(
    event_type: str,
    event_data: dict | None = None,
    user_id: str = "",
) -> list[dict]:
    """Fire an event and execute matching recipes.

    Args:
        event_type: One of the EVENT_TYPES constants.
        event_data: Dict with event-specific data (passed as trigger_data).
        user_id: User context for the event.

    Returns list of execution results (one per matching recipe).
    """
    if event_type not in EVENT_TYPES:
        logger.warning("Unknown event type: %s", event_type)
        return []

    from roost.services.recipes import list_recipes, execute_recipe

    # Find recipes triggered by this event
    recipes = list_recipes(trigger_type="event", enabled_only=True)
    matching = [
        r for r in recipes
        if r.get("trigger_config", "").strip() == event_type
    ]

    if not matching:
        return []

    results = []
    for recipe in matching:
        logger.info(
            "SOP trigger: event '%s' firing recipe #%d '%s'",
            event_type, recipe["id"], recipe["name"],
        )
        try:
            result = await execute_recipe(
                recipe_id=recipe["id"],
                message=_build_event_message(event_type, event_data or {}),
                trigger_data={"event_type": event_type, **(event_data or {})},
            )
            results.append(result)
        except Exception:
            logger.exception("SOP recipe #%d failed on event '%s'", recipe["id"], event_type)
            results.append({"recipe_id": recipe["id"], "status": "failed"})

    return results


def fire_event_sync(
    event_type: str,
    event_data: dict | None = None,
    user_id: str = "",
) -> None:
    """Fire-and-forget event trigger (non-blocking, for use in sync code).

    Spawns in a background thread to avoid blocking the caller.
    """
    import asyncio
    import threading

    def _run():
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(fire_event(event_type, event_data, user_id))
        except Exception:
            logger.debug("Sync event fire failed for '%s'", event_type, exc_info=True)

    thread = threading.Thread(target=_run, name=f"sop-{event_type}", daemon=True)
    thread.start()


def _build_event_message(event_type: str, data: dict) -> str:
    """Build a message string from event data for recipe classification."""
    parts = [f"Event: {event_type}"]

    if event_type == "email_received":
        parts.append(f"From: {data.get('from', '?')}")
        parts.append(f"Subject: {data.get('subject', '?')}")
        if data.get("snippet"):
            parts.append(f"Preview: {data['snippet'][:200]}")

    elif event_type == "task_completed":
        parts.append(f"Task: {data.get('title', '?')}")
        parts.append(f"Project: {data.get('project', '?')}")

    elif event_type == "task_created":
        parts.append(f"Task: {data.get('title', '?')}")
        parts.append(f"Priority: {data.get('priority', '?')}")

    elif event_type == "calendar_event_starting":
        parts.append(f"Event: {data.get('summary', '?')}")
        parts.append(f"Start: {data.get('start', '?')}")

    elif event_type == "contact_created":
        parts.append(f"Name: {data.get('name', '?')}")

    elif event_type == "webhook":
        parts.append(f"Source: {data.get('source', '?')}")
        if data.get("body"):
            parts.append(f"Body: {str(data['body'])[:200]}")

    return "\n".join(parts)
