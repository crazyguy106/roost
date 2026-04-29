"""MCP tools for Guardian AI, Cost Tracking, and Checkpoints."""

from roost.mcp.server import mcp


@mcp.tool()
def get_guardian_log(
    limit: int = 20,
    decision: str = "",
) -> list[dict]:
    """Get recent Guardian AI decisions (warn/block events).

    Args:
        limit: Max entries to return.
        decision: Filter by decision type: warn, block, or empty for all.
    """
    from roost.services.guardian import get_guardian_log as _get_log
    return _get_log(limit=limit, decision=decision or None)


@mcp.tool()
def get_guardian_stats() -> dict:
    """Get Guardian AI summary stats — total decisions, blocks, top blocked rules."""
    from roost.services.guardian import get_guardian_stats as _get_stats
    return _get_stats()


@mcp.tool()
def get_usage_today() -> dict:
    """Get today's AI token usage — input/output tokens, estimated cost, run count."""
    from roost.services.cost_tracking import get_today_tokens
    return get_today_tokens()


@mcp.tool()
def get_usage_history(days: int = 7) -> list[dict]:
    """Get daily AI token usage for the last N days."""
    from roost.services.cost_tracking import get_usage_history as _get_history
    return _get_history(days=days)


@mcp.tool()
def get_autonomy_level() -> dict:
    """Get the current autonomy level (supervised/assisted/autonomous)."""
    from roost.config import AUTONOMY_LEVEL
    descriptions = {
        "supervised": "Confirm every external action (email, SSH, uploads)",
        "assisted": "Confirm destructive actions in web chat only (default)",
        "autonomous": "No confirmation — maximum speed, maximum risk",
    }
    return {
        "level": AUTONOMY_LEVEL,
        "description": descriptions.get(AUTONOMY_LEVEL, "unknown"),
    }


@mcp.tool()
def list_checkpoints(
    run_id: str = "",
    limit: int = 20,
) -> dict:
    """List recent agent action checkpoints (for rollback).

    Each checkpoint records a write tool call with its arguments, result,
    and reverse action (if available).

    Args:
        run_id: Filter by agent run ID. Empty = all recent checkpoints.
        limit: Max checkpoints to return.
    """
    from roost.services.checkpoints import list_checkpoints as _list
    checkpoints = _list(run_id=run_id, limit=limit)
    return {"count": len(checkpoints), "checkpoints": checkpoints}


@mcp.tool()
def rollback_checkpoint(checkpoint_id: int) -> dict:
    """Rollback a checkpoint — undo a previous agent action.

    Executes the reverse tool call (e.g. delete_task to undo create_task).
    Not all actions are reversible (e.g. sent emails cannot be unsent).

    Args:
        checkpoint_id: The checkpoint ID to rollback.
    """
    from roost.services.checkpoints import rollback_checkpoint as _rollback
    return _rollback(checkpoint_id)


@mcp.tool()
def list_learned_skills(
    status: str = "",
    limit: int = 20,
) -> dict:
    """List learned skills (self-improving agent patterns).

    Skills are extracted from successful multi-tool agent runs. Pending skills
    need approval before the agent uses them.

    Args:
        status: Filter by status: pending, approved, rejected, or empty for all.
        limit: Max skills to return.
    """
    from roost.services.learned_skills import list_skills
    skills = list_skills(status=status, limit=limit)
    return {"count": len(skills), "skills": skills}


@mcp.tool()
def approve_learned_skill(skill_id: int) -> dict:
    """Approve a pending learned skill — it will be included in agent prompts.

    Args:
        skill_id: The skill ID to approve.
    """
    from roost.services.learned_skills import approve_skill
    return approve_skill(skill_id)


@mcp.tool()
def reject_learned_skill(skill_id: int) -> dict:
    """Reject a pending learned skill — it won't be used by the agent.

    Args:
        skill_id: The skill ID to reject.
    """
    from roost.services.learned_skills import reject_skill
    return reject_skill(skill_id)


@mcp.tool()
def spawn_background_agent(
    prompt: str,
    tool_scope: str = "TIER_READ_ONLY",
) -> dict:
    """Spawn a background agent to perform a task asynchronously.

    The agent runs in a background thread with enforced limits:
    max concurrent runs and max duration. Returns immediately with a run_id.

    Background agents default to read-only tool access for safety.

    Args:
        prompt: The task for the background agent to perform.
        tool_scope: Tool access tier (TIER_READ_ONLY, TIER_INTERNAL_WRITE, TIER_FULL). Default: TIER_READ_ONLY.
    """
    from roost.services.background_runs import spawn_background_agent as _spawn
    return _spawn(prompt=prompt, tool_scope=tool_scope)


@mcp.tool()
def list_background_runs(
    status: str = "",
    limit: int = 10,
) -> dict:
    """List background agent runs.

    Args:
        status: Filter by status: running, completed, failed, timeout, or empty for all.
        limit: Max runs to return.
    """
    from roost.services.background_runs import list_background_runs as _list
    runs = _list(status=status, limit=limit)
    return {"count": len(runs), "runs": runs}


@mcp.tool()
def get_background_run_result(run_id: str) -> dict:
    """Get the result of a background agent run.

    Args:
        run_id: The run ID returned by spawn_background_agent.
    """
    from roost.services.background_runs import get_background_run
    run = get_background_run(run_id)
    if not run:
        return {"error": f"Background run {run_id} not found"}
    return run
