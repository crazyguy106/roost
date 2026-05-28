"""Fragmented-message debouncer for inbound channels.

WhatsApp / WeChat / Telegram users often type one thought across
multiple sends. Classifying each fragment as a standalone message
mis-routes ("hi" → cold/general) and spams the operator with
duplicate qualification questions.

Design:
  * In-memory buffer keyed by ``(channel, sender)``.
  * On each fragment, append to the buffer and (re)schedule a debounce
    task. A newer fragment cancels the pending task.
  * When the debounce task fires, the buffer is *atomically* drained
    and the concatenated text is handed to the per-channel
    ``processor`` coroutine.
  * Three release rules — see ``settings.yaml`` for full prose:
        1. Silence ≥ ``debounce_seconds``                       (primary)
        2. Last fragment ends in . ? ! AND silence ≥ ``early`` (fast-path)
        3. ``max_wait_seconds`` since first fragment            (safety cap)
  * If ``settings.fragmented_messages.enabled`` is false, the buffer
    is a passthrough — the processor runs synchronously per fragment.

The buffer is process-local and not durable. A container restart
loses any in-flight buffers; survivors are negligible compared to
the legibility win and we accept the trade.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

logger = logging.getLogger("roost.inbound_buffer")

# Type alias for the per-channel processor. Receives the concatenated
# message dict (text fields merged) and runs the existing pipeline.
Processor = Callable[[dict], Awaitable[None]]


@dataclass
class _Entry:
    msgs: list[dict] = field(default_factory=list)
    first_at: float = 0.0
    last_at: float = 0.0
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_buffers: dict[tuple[str, str], _Entry] = {}


def _merge(messages: list[dict]) -> dict:
    """Combine fragments into one logical message. Joins text with
    spaces; preserves other fields from the *first* fragment so
    sender_name / message_id stay sensible. Tracks original count as
    ``_fragment_count`` for telemetry."""
    if not messages:
        return {}
    head = dict(messages[0])
    combined_text = " ".join(
        (m.get("text") or "").strip() for m in messages
        if (m.get("text") or "").strip()
    ).strip()
    head["text"] = combined_text
    head["_fragment_count"] = len(messages)
    head["_fragment_ids"] = [m.get("message_id", "") for m in messages]
    return head


def _is_terminal(text: str, terminals: list[str]) -> bool:
    if not text:
        return False
    last = text.rstrip()
    return bool(last) and any(last.endswith(t) for t in terminals)


async def _flush(channel: str, sender: str, processor: Processor) -> None:
    """Drain the buffer for (channel, sender) and hand off to processor.
    Called exclusively from the debounce task."""
    key = (channel, sender)
    entry = _buffers.get(key)
    if entry is None:
        return
    async with entry.lock:
        messages = list(entry.msgs)
        entry.msgs.clear()
        entry.task = None
    if not messages:
        return
    merged = _merge(messages)
    fragments = merged.get("_fragment_count", 1)
    if fragments > 1:
        logger.info(
            "inbound_buffer: flushing %d fragments from %s/%s (text=%r)",
            fragments, channel, sender, merged["text"][:80],
        )
    try:
        await processor(merged)
    except Exception:  # noqa: BLE001 — never crash the buffer task
        logger.exception(
            "inbound_buffer: processor raised on %s/%s — dropping",
            channel, sender,
        )
    finally:
        # Don't leak entries forever — drop the buffer if nothing else
        # arrived during the flush.
        if entry.msgs == [] and entry.task is None:
            _buffers.pop(key, None)


async def _debounce_wait(
    channel: str, sender: str, processor: Processor,
    debounce: float, early: float, max_wait: float, terminals: list[str],
) -> None:
    """Wait until one of the release rules fires, then flush."""
    key = (channel, sender)
    while True:
        entry = _buffers.get(key)
        if entry is None or not entry.msgs:
            return
        now = time.monotonic()
        silent_for = now - entry.last_at
        wall_for = now - entry.first_at
        last_text = (entry.msgs[-1].get("text") or "")
        terminal = _is_terminal(last_text, terminals)

        # Rule 3: hard cap.
        if wall_for >= max_wait:
            break
        # Rule 2: terminal punctuation + short silence.
        if terminal and silent_for >= early:
            break
        # Rule 1: full debounce window.
        if silent_for >= debounce:
            break

        # Sleep until the *closest* deadline triggers re-check.
        candidates = [debounce - silent_for, max_wait - wall_for]
        if terminal:
            candidates.append(early - silent_for)
        sleep_for = max(0.05, min(c for c in candidates if c > 0))
        try:
            await asyncio.sleep(sleep_for)
        except asyncio.CancelledError:
            # A new fragment cancelled us — let the new task take over.
            return

    await _flush(channel, sender, processor)


async def submit(
    channel: str, sender: str, message: dict, processor: Processor,
) -> None:
    """Append a fragment and (re)arm the debounce task.

    ``message`` is the per-channel inbound dict (must contain ``text``;
    typically also ``sender``, ``sender_name``, ``message_id``).
    ``processor`` is the coroutine that runs once the buffer flushes —
    pass a coroutine factory that takes one ``dict`` arg.

    If the debouncer is disabled in settings, the processor runs
    immediately on the single message.
    """
    from roost.extras.lead_nurture.services import settings as svc_settings
    cfg = svc_settings.get_fragmented_messages()
    if not cfg.get("enabled", True):
        await processor(message)
        return

    debounce = float(cfg.get("debounce_seconds", 20))
    early = float(cfg.get("early_release_seconds", 4))
    max_wait = float(cfg.get("max_wait_seconds", 90))
    terminals = list(cfg.get("terminal_punctuation") or [".", "?", "!"])

    key = (channel, sender)
    entry = _buffers.get(key)
    if entry is None:
        entry = _Entry()
        _buffers[key] = entry

    now = time.monotonic()
    async with entry.lock:
        if not entry.msgs:
            entry.first_at = now
        entry.msgs.append(message)
        entry.last_at = now
        # Cancel any pending debounce task — we'll start a fresh one
        # with the updated last_at.
        if entry.task and not entry.task.done():
            entry.task.cancel()
        entry.task = asyncio.create_task(
            _debounce_wait(
                channel, sender, processor,
                debounce, early, max_wait, terminals,
            )
        )


def reset_for_tests() -> None:
    """Drop all buffers — test helper."""
    for entry in list(_buffers.values()):
        if entry.task and not entry.task.done():
            entry.task.cancel()
    _buffers.clear()
