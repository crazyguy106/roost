"""Durable, pausable RPA runs.

A run is a long-lived browser-automation flow (e.g. logging into an insurance
portal, downloading policy bundles). Unlike `background_runs` it can pause to
ask the user a question — typically "what is the OTP we just received?" — and
resume when the user answers via Telegram or web chat.

State machine:
    running   ──request_input──► awaiting_input ──submit_input──► running
    running                                     ──complete──► completed
    running                                     ──fail──────► failed
    *                                           ──cancel─────► cancelled

Caveats:
  - In-process `asyncio.Event`s do the resume signalling. The DB is the
    source of truth so a restart can list awaiting_input runs, but a run
    that is mid-flight when the process dies cannot be resumed in v1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from roost.database import get_connection

logger = logging.getLogger("roost.services.rpa_runs")

__all__ = [
    "create_run",
    "request_input",
    "submit_input",
    "complete",
    "fail",
    "cancel",
    "get_run",
    "list_runs",
    "find_awaiting_for_user",
    "set_state_field",
]


# In-process resume primitives, keyed by run_id.
# `_events[run_id]` is set by `submit_input`; `_inputs[run_id]` carries the value.
_events: dict[int, asyncio.Event] = {}
_inputs: dict[int, str] = {}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── CRUD ────────────────────────────────────────────────────────────


def create_run(
    user_id: str,
    portal_slug: str,
    recipe_id: int | None = None,
    state: dict[str, Any] | None = None,
) -> int:
    """Create a new run row, return its id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO rpa_runs
               (user_id, portal_slug, recipe_id, status, state_json, created_at, updated_at)
               VALUES (?, ?, ?, 'running', ?, ?, ?)""",
            (
                user_id,
                portal_slug,
                recipe_id,
                json.dumps(state or {}),
                _now(),
                _now(),
            ),
        )
        conn.commit()
        run_id = cur.lastrowid
    finally:
        conn.close()
    logger.info("RPA run #%d created (portal=%s user=%s)", run_id, portal_slug, user_id)
    return run_id


def get_run(run_id: int) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM rpa_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def list_runs(
    user_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM rpa_runs WHERE 1=1"
        params: list[Any] = []
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def set_state_field(run_id: int, key: str, value: Any) -> None:
    """Merge a single key/value into the run's state_json. Idempotent."""
    run = get_run(run_id)
    if not run:
        return
    state = run.get("state_json") if isinstance(run.get("state_json"), dict) else {}
    if value is None:
        state.pop(key, None)
    else:
        state[key] = value
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE rpa_runs SET state_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(state), _now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()


def find_awaiting_for_user(user_id: str) -> dict | None:
    """Return the most recent awaiting_input run for a user, or None."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM rpa_runs
               WHERE user_id = ? AND status = 'awaiting_input'
               ORDER BY id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


# ── Pause / resume ──────────────────────────────────────────────────


async def request_input(
    run_id: int,
    prompt_text: str,
    kind: str = "text",
    timeout: float = 300.0,
) -> str:
    """Pause the run, ask the user something, return their answer.

    Side effects:
      - Sets status='awaiting_input', stores prompt fields.
      - Sends a Telegram notification (best-effort).
      - Awaits an in-process `asyncio.Event`.

    Raises:
      asyncio.TimeoutError if the user does not respond in `timeout` seconds.
      RuntimeError if the run is cancelled while awaiting.
    """
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE rpa_runs
               SET status = 'awaiting_input', prompt_text = ?, prompt_kind = ?, updated_at = ?
               WHERE id = ?""",
            (prompt_text, kind, _now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()

    event = asyncio.Event()
    _events[run_id] = event
    _inputs.pop(run_id, None)

    await _notify_telegram(run_id, prompt_text, kind)

    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        _events.pop(run_id, None)
        fail(run_id, f"timeout waiting for user input ({kind})")
        raise

    value = _inputs.pop(run_id, "")
    _events.pop(run_id, None)

    # Status will already be 'running' or 'cancelled' from submit_input/cancel.
    current = get_run(run_id)
    if current and current["status"] == "cancelled":
        raise RuntimeError(f"Run {run_id} cancelled while awaiting input")
    return value


def submit_input(run_id: int, value: str) -> bool:
    """Resume a paused run with the user-supplied value. Returns True on success."""
    run = get_run(run_id)
    if not run or run["status"] != "awaiting_input":
        return False
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE rpa_runs
               SET status = 'running', last_input = ?, prompt_text = '', prompt_kind = '',
                   updated_at = ?
               WHERE id = ?""",
            (value, _now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()
    _inputs[run_id] = value
    event = _events.get(run_id)
    if event:
        event.set()
    logger.info("RPA run #%d input submitted (kind=%s)", run_id, run["prompt_kind"])
    return True


def complete(run_id: int, result: dict | None = None) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE rpa_runs
               SET status = 'completed', result_json = ?, updated_at = ?
               WHERE id = ?""",
            (json.dumps(result or {}), _now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("RPA run #%d completed", run_id)


def fail(run_id: int, error: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """UPDATE rpa_runs
               SET status = 'failed', error = ?, updated_at = ?
               WHERE id = ?""",
            (error[:2000], _now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()
    # Wake any awaiter so it can observe the failure
    event = _events.get(run_id)
    if event:
        event.set()
    logger.warning("RPA run #%d failed: %s", run_id, error[:200])


def cancel(run_id: int) -> bool:
    run = get_run(run_id)
    if not run or run["status"] in ("completed", "failed", "cancelled"):
        return False
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE rpa_runs SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (_now(), run_id),
        )
        conn.commit()
    finally:
        conn.close()
    event = _events.get(run_id)
    if event:
        event.set()
    logger.info("RPA run #%d cancelled", run_id)
    return True


# ── Helpers ─────────────────────────────────────────────────────────


def _row_to_dict(row) -> dict:
    d = dict(row)
    for k in ("state_json", "result_json"):
        if isinstance(d.get(k), str):
            try:
                d[k] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


async def _notify_telegram(
    run_id: int,
    prompt_text: str,
    kind: str,
    live_url: str | None = None,
) -> None:
    """Fire-and-forget Telegram notification of a pending prompt.

    `live_url` is an optional URL the user can open to drive the live
    browser session (used by `await_user_session` for Singpass / 2FA /
    captcha flows). When provided it's appended to the message body.
    """
    try:
        from roost.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_USERS:
            return
        import httpx

        if kind == "manual_intervention":
            tail = (
                f"Open the browser: {live_url}\nI'll resume automatically when you're done."
                if live_url
                else "I'll resume automatically when you've completed the action in the browser."
            )
            text = (
                f"RPA run #{run_id} needs you to take over the browser:\n"
                f"{prompt_text}\n\n{tail}"
            )
        else:
            text = (
                f"RPA run #{run_id} needs input ({kind}):\n"
                f"{prompt_text}\n\n"
                f"Reply with the value, or /rpa cancel {run_id} to abort."
            )
        chat_id = TELEGRAM_ALLOWED_USERS[0]
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url, json={"chat_id": chat_id, "text": text})
    except Exception:
        logger.exception("Failed to send Telegram prompt for run #%d", run_id)
