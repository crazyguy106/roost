"""Background agent runs — spawn sub-agents with concurrency limits.

Allows spawning background agent runs for long-running tasks while the
user continues interacting. Enforces MAX_BACKGROUND_RUNS and
MAX_BACKGROUND_DURATION to prevent runaway agents.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timezone

from roost.database import db_connection

logger = logging.getLogger(__name__)

# Import limits from config (with fallback defaults)
try:
    from roost.config import MAX_BACKGROUND_RUNS, MAX_BACKGROUND_DURATION
except ImportError:
    MAX_BACKGROUND_RUNS = 3
    MAX_BACKGROUND_DURATION = 300

# Active threads tracker (in-memory, cleared on restart)
_active_threads: dict[str, threading.Thread] = {}


def spawn_background_agent(
    prompt: str,
    user_id: str = "",
    system_prompt: str = "",
    tool_scope: str = "TIER_READ_ONLY",
) -> dict:
    """Spawn a background agent run.

    Returns immediately with a run_id. The agent runs in a background
    thread with enforced duration limit.

    Args:
        prompt: The task for the agent to perform.
        user_id: User who initiated the run.
        system_prompt: Optional system prompt override.
        tool_scope: Tool access tier (default read-only for safety).
    """
    # Check concurrency limit
    active = _count_active()
    if active >= MAX_BACKGROUND_RUNS:
        return {
            "error": f"Background run limit reached ({MAX_BACKGROUND_RUNS}). "
                     "Wait for existing runs to complete.",
            "active_count": active,
        }

    run_id = str(uuid.uuid4())[:8]

    # Record in database
    try:
        with db_connection() as conn:
            conn.execute(
                """INSERT INTO background_runs
                   (run_id, prompt, user_id, status)
                   VALUES (?, ?, ?, 'running')""",
                (run_id, prompt[:2000], user_id),
            )
            conn.commit()
    except Exception:
        logger.debug("Failed to record background run", exc_info=True)

    # Start background thread
    thread = threading.Thread(
        target=_run_agent_thread,
        args=(run_id, prompt, user_id, system_prompt, tool_scope),
        name=f"bg-agent-{run_id}",
        daemon=True,
    )
    _active_threads[run_id] = thread
    thread.start()

    return {
        "run_id": run_id,
        "status": "running",
        "message": f"Background agent spawned (ID: {run_id}). "
                   f"Max duration: {MAX_BACKGROUND_DURATION}s. "
                   f"Active: {active + 1}/{MAX_BACKGROUND_RUNS}.",
    }


def list_background_runs(
    status: str = "",
    limit: int = 20,
    user_id: str = "",
) -> list[dict]:
    """List background agent runs."""
    try:
        with db_connection() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM background_runs
                       WHERE status = ? ORDER BY created_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM background_runs
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        logger.debug("Failed to list background runs", exc_info=True)
        return []


def get_background_run(run_id: str) -> dict | None:
    """Get a specific background run."""
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT * FROM background_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


# ── Internal ───────────────────────────────────────────────────────

def _count_active() -> int:
    """Count currently running background agents."""
    # Clean up finished threads
    finished = [rid for rid, t in _active_threads.items() if not t.is_alive()]
    for rid in finished:
        del _active_threads[rid]
    return len(_active_threads)


def _run_agent_thread(
    run_id: str,
    prompt: str,
    user_id: str,
    system_prompt: str,
    tool_scope: str,
) -> None:
    """Run agent in background thread with timeout enforcement."""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        result = loop.run_until_complete(
            asyncio.wait_for(
                _run_agent(run_id, prompt, user_id, system_prompt, tool_scope),
                timeout=MAX_BACKGROUND_DURATION,
            )
        )

        _update_run(run_id, status="completed", result=result[:5000])

    except asyncio.TimeoutError:
        logger.warning("Background run %s timed out after %ds", run_id, MAX_BACKGROUND_DURATION)
        _update_run(
            run_id, status="timeout",
            result=f"Timed out after {MAX_BACKGROUND_DURATION}s.",
        )

    except Exception as e:
        logger.exception("Background run %s failed", run_id)
        _update_run(run_id, status="failed", result=str(e)[:2000])

    finally:
        _active_threads.pop(run_id, None)


async def _run_agent(
    run_id: str,
    prompt: str,
    user_id: str,
    system_prompt: str,
    tool_scope: str,
) -> str:
    """Run the actual agent."""
    from roost.gemini_agent import GeminiAgent

    agent = GeminiAgent(
        system_prompt=system_prompt or "You are a background agent. Complete the task efficiently.",
        tool_scope=tool_scope,
    )
    return await agent.run(prompt, user_id=user_id)


def _update_run(run_id: str, status: str, result: str = "") -> None:
    """Update a background run record."""
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with db_connection() as conn:
            conn.execute(
                """UPDATE background_runs
                   SET status = ?, result = ?, completed_at = ?
                   WHERE run_id = ?""",
                (status, result, now, run_id),
            )
            conn.commit()
    except Exception:
        logger.debug("Failed to update background run", exc_info=True)
