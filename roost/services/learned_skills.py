"""Self-improving skills — learn from successful agent runs.

After a successful multi-tool agent run, the system extracts a "skill"
(a reusable pattern: trigger phrase → tool sequence + parameters).
Skills start as "pending" and must be approved before they're included
in the agent's system prompt.

This lets the agent get better over time at recurring tasks without
the user having to re-explain every time.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from roost.database import db_connection

logger = logging.getLogger(__name__)


# ── Skill CRUD ─────────────────────────────────────────────────────

def save_skill(
    name: str,
    trigger_phrases: list[str],
    tool_sequence: list[dict],
    description: str = "",
    user_id: str = "",
    source_run_id: str = "",
) -> int | None:
    """Save a learned skill candidate (status=pending).

    Args:
        name: Short skill name (e.g. "email_summary")
        trigger_phrases: List of phrases that should activate this skill
        tool_sequence: List of {tool_name, args_template} dicts
        description: Human-readable description of what the skill does
        source_run_id: The agent run that generated this skill
    """
    try:
        with db_connection() as conn:
            cur = conn.execute(
                """INSERT INTO learned_skills
                   (name, trigger_phrases, tool_sequence, description,
                    user_id, source_run_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (name,
                 json.dumps(trigger_phrases),
                 json.dumps(tool_sequence, default=str)[:10000],
                 description,
                 user_id,
                 source_run_id),
            )
            conn.commit()
            return cur.lastrowid
    except Exception:
        logger.debug("Failed to save learned skill", exc_info=True)
        return None


def list_skills(
    status: str = "",
    limit: int = 50,
    user_id: str = "",
) -> list[dict]:
    """List learned skills with optional status filter."""
    try:
        with db_connection() as conn:
            if status:
                rows = conn.execute(
                    """SELECT * FROM learned_skills
                       WHERE status = ? ORDER BY created_at DESC LIMIT ?""",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM learned_skills
                       ORDER BY created_at DESC LIMIT ?""",
                    (limit,),
                ).fetchall()
            return [_skill_to_dict(r) for r in rows]
    except Exception:
        logger.debug("Failed to list skills", exc_info=True)
        return []


def get_skill(skill_id: int) -> dict | None:
    """Get a skill by ID."""
    try:
        with db_connection() as conn:
            row = conn.execute(
                "SELECT * FROM learned_skills WHERE id = ?",
                (skill_id,),
            ).fetchone()
            return _skill_to_dict(row) if row else None
    except Exception:
        return None


def approve_skill(skill_id: int) -> dict:
    """Approve a pending skill — it will be included in agent prompts."""
    try:
        with db_connection() as conn:
            conn.execute(
                "UPDATE learned_skills SET status = 'approved' WHERE id = ? AND status = 'pending'",
                (skill_id,),
            )
            conn.commit()
            skill = get_skill(skill_id)
            if skill:
                return {"ok": True, "skill": skill}
            return {"error": f"Skill {skill_id} not found"}
    except Exception as e:
        return {"error": str(e)}


def reject_skill(skill_id: int) -> dict:
    """Reject a pending skill — it won't be used."""
    try:
        with db_connection() as conn:
            conn.execute(
                "UPDATE learned_skills SET status = 'rejected' WHERE id = ? AND status = 'pending'",
                (skill_id,),
            )
            conn.commit()
            return {"ok": True, "skill_id": skill_id}
    except Exception as e:
        return {"error": str(e)}


def increment_usage(skill_id: int) -> None:
    """Increment the usage counter for an approved skill."""
    try:
        with db_connection() as conn:
            conn.execute(
                "UPDATE learned_skills SET use_count = use_count + 1 WHERE id = ?",
                (skill_id,),
            )
            conn.commit()
    except Exception:
        pass


def get_approved_skills_prompt() -> str:
    """Build a prompt section listing all approved skills for the agent.

    Returns empty string if no approved skills exist.
    """
    skills = list_skills(status="approved")
    if not skills:
        return ""

    lines = [
        "\n## Learned Skills (from previous successful runs)\n",
        "When the user's request matches one of these patterns, "
        "you can use the recorded tool sequence as a starting point:\n",
    ]

    for s in skills[:20]:  # Cap at 20 to avoid prompt bloat
        triggers = ", ".join(s.get("trigger_phrases", [])[:3])
        lines.append(f"- **{s['name']}** (triggers: {triggers})")
        lines.append(f"  {s.get('description', '')}")
        tool_seq = s.get("tool_sequence", [])
        if tool_seq:
            tools = [t.get("tool_name", "?") for t in tool_seq[:5]]
            lines.append(f"  Tools: {' → '.join(tools)}")
        lines.append("")

    return "\n".join(lines)


# ── Skill extraction from agent runs ──────────────────────────────

def extract_skill_from_run(
    run_id: str,
    user_prompt: str,
    tool_calls: list[dict],
    user_id: str = "",
) -> int | None:
    """Analyse a completed agent run and extract a skill candidate.

    Only extracts if:
    - At least 2 tool calls were made (single-tool runs aren't patterns)
    - The run completed successfully (no errors)
    - A similar skill doesn't already exist

    Args:
        run_id: Agent run ID
        user_prompt: The original user request
        tool_calls: List of {tool_name, args} dicts from the run
        user_id: User who initiated the run

    Returns skill ID if extracted, None otherwise.
    """
    if len(tool_calls) < 2:
        return None

    # Check for errors in tool results
    error_count = sum(
        1 for tc in tool_calls
        if isinstance(tc.get("result"), dict) and "error" in tc.get("result", {})
    )
    if error_count > len(tool_calls) // 2:
        return None  # Too many errors

    # Check if a similar skill already exists
    existing = list_skills(status="approved")
    tool_names = [tc.get("tool_name", "") for tc in tool_calls]
    tool_sig = ",".join(tool_names)
    for s in existing:
        existing_sig = ",".join(
            t.get("tool_name", "") for t in s.get("tool_sequence", [])
        )
        if existing_sig == tool_sig:
            return None  # Duplicate

    # Build skill candidate
    name = _generate_skill_name(user_prompt, tool_names)
    trigger_phrases = [user_prompt[:200]]

    # Strip sensitive values from args (keep structure)
    sanitized_sequence = []
    for tc in tool_calls:
        sanitized_args = {}
        for k, v in (tc.get("args") or {}).items():
            if any(s in k.lower() for s in ("password", "secret", "token", "key")):
                sanitized_args[k] = "***"
            elif isinstance(v, str) and len(v) > 200:
                sanitized_args[k] = v[:200] + "..."
            else:
                sanitized_args[k] = v
        sanitized_sequence.append({
            "tool_name": tc.get("tool_name", ""),
            "args_template": sanitized_args,
        })

    description = f"Learned from: {user_prompt[:100]}"

    return save_skill(
        name=name,
        trigger_phrases=trigger_phrases,
        tool_sequence=sanitized_sequence,
        description=description,
        user_id=user_id,
        source_run_id=run_id,
    )


def _generate_skill_name(prompt: str, tool_names: list[str]) -> str:
    """Generate a short skill name from the prompt and tools used."""
    # Use first few words of the prompt
    words = prompt.lower().split()[:4]
    name = "_".join(w for w in words if w.isalnum())[:30]
    if not name:
        name = "_".join(tool_names[:2])[:30]
    return name or "unnamed_skill"


# ── Helpers ────────────────────────────────────────────────────────

def _skill_to_dict(row) -> dict:
    d = dict(row)
    for json_field in ("trigger_phrases", "tool_sequence"):
        if json_field in d and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
