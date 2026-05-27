"""Agentic Workflow — SSE event utilities (Phase 1).

Helpers for the `/agentic` surface:
- ``EventEmitter`` — async queue producing SSE-encoded JSON lines.
- ``truncate_args`` / ``truncate_result`` — preview-safe shrinkers for tool
  args/results that hit the wire.
- ``redact_secrets`` — replaces sensitive arg values before truncation.
- ``new_call_id`` / ``new_plan_id`` — short, collision-resistant IDs.

See ``docs/agentic-workflow-phase1.md`` §4 for the wire format and §10 for
the open questions this module deliberately keeps simple.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from typing import Any

# ── Limits (match spec §4.3) ─────────────────────────────────────────

ARG_STRING_LIMIT = 200       # per-value truncation
RESULT_STRING_LIMIT = 500    # whole-result string cap

# Case-insensitive substring match on arg keys.
_SECRET_KEY_RE = re.compile(r"(secret|password|token|api[_-]?key|otp|auth)", re.IGNORECASE)

REDACTED = "***"


# ── ID helpers ────────────────────────────────────────────────────────

def new_plan_id() -> str:
    """Plan IDs travel with every event in a single run."""
    return f"agt_{secrets.token_hex(4)}"


def new_call_id() -> str:
    """Tool-call IDs are unique within a run.

    Uses 4 bytes (8 hex chars) per spec §3 Step 4 — collision probability
    over a single run is negligible.
    """
    return f"tc_{secrets.token_hex(4)}"


# ── Redaction + truncation ───────────────────────────────────────────

def redact_secrets(args: Any) -> Any:
    """Replace values for keys that look like credentials with ``"***"``.

    Recursive into nested dicts/lists. Applied *before* truncation so a
    truncated secret string never reaches the wire.
    """
    if isinstance(args, dict):
        out: dict[str, Any] = {}
        for k, v in args.items():
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                out[k] = REDACTED
            else:
                out[k] = redact_secrets(v)
        return out
    if isinstance(args, list):
        return [redact_secrets(v) for v in args]
    return args


def _truncate_string(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"…[truncated, {len(value)} chars]"


def truncate_args(args: Any) -> Any:
    """Shrink tool args for the wire: redact, then truncate string values.

    Dict keys are preserved; only string *values* are truncated. Non-string
    values pass through unchanged (counts, bools, None).
    """
    redacted = redact_secrets(args)

    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            return _truncate_string(v, ARG_STRING_LIMIT)
        if isinstance(v, dict):
            return {k: _walk(val) for k, val in v.items()}
        if isinstance(v, list):
            return [_walk(item) for item in v]
        return v

    return _walk(redacted)


def truncate_result(result: Any) -> str:
    """Coerce any tool result into a single preview string ≤500 chars.

    Bytes get a ``"<binary, N bytes>"`` summary instead of being decoded.
    Dict/list results are JSON-stringified first.
    """
    if isinstance(result, (bytes, bytearray)):
        return f"<binary, {len(result)} bytes>"
    if isinstance(result, str):
        return _truncate_string(result, RESULT_STRING_LIMIT)
    try:
        as_text = json.dumps(result, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        as_text = str(result)
    return _truncate_string(as_text, RESULT_STRING_LIMIT)


# ── EventEmitter ──────────────────────────────────────────────────────

class EventEmitter:
    """Async queue producing SSE-encoded ``data: {...}\\n\\n`` lines.

    Producers call ``emit(event_type, payload)`` (synchronous; never blocks).
    Consumers iterate ``stream()`` to yield wire-ready strings.
    Call ``close()`` once when the producer is done — ``stream()`` ends.
    """

    _SENTINEL = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._closed = False

    def emit(self, event_type: str, payload: dict | None = None) -> None:
        if self._closed:
            return
        event = {"type": event_type, **(payload or {})}
        self._queue.put_nowait(event)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._queue.put_nowait(self._SENTINEL)

    async def stream(self):
        """Yield SSE-formatted lines until ``close()`` is called."""
        while True:
            event = await self._queue.get()
            if event is self._SENTINEL:
                return
            yield f"data: {json.dumps(event, default=str)}\n\n"
