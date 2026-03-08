"""Lightweight in-process event bus for cross-component notifications.

Supports sync and async subscribers. Events carry a data dict with a
'source' field to prevent echo loops (e.g. Telegram notifier skips
events originating from Telegram).
"""

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable

logger = logging.getLogger("roost.events")

# Event type constants
TASK_CREATED = "task.created"
TASK_UPDATED = "task.updated"
TASK_COMPLETED = "task.completed"
DOC_CREATED = "doc.created"
PROJECT_CREATED = "project.created"
PROJECT_UPDATED = "project.updated"
NOTE_CREATED = "note.created"
TASK_DEADLINE_APPROACHING = "task.deadline_approaching"
CURRICULUM_REGISTERED = "curriculum.registered"
CURRICULUM_UPDATED = "curriculum.updated"

# Internal subscriber registry
_subscribers: dict[str, list[Callable]] = defaultdict(list)


def subscribe(event_type: str, callback: Callable) -> None:
    """Register a callback for an event type.

    Callback signature: callback(data: dict) -> None
    Can be sync or async.
    """
    _subscribers[event_type].append(callback)
    logger.debug("Subscribed %s to %s", callback.__name__, event_type)


def emit(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Emit an event to all subscribers.

    Safe to call from sync code. Async subscribers are scheduled
    on the running event loop if one exists.
    """
    if data is None:
        data = {}

    callbacks = _subscribers.get(event_type, [])
    if not callbacks:
        return

    logger.debug("Emitting %s (%d subscribers)", event_type, len(callbacks))

    for cb in callbacks:
        try:
            if asyncio.iscoroutinefunction(cb):
                # Schedule async callback if there's a running loop
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(cb(data))
                except RuntimeError:
                    # No running loop — skip async subscribers
                    logger.debug("Skipping async subscriber %s (no event loop)", cb.__name__)
            else:
                cb(data)
        except Exception:
            logger.exception("Error in event subscriber %s for %s", cb.__name__, event_type)
