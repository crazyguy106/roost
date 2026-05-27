"""Generate a weekly status report from completed tasks.

Pulls the tasks you completed in the last 7 days, groups them by
project, and asks the AI to draft a short narrative summary suitable
for sharing with a manager or team.

This is `external_write` because the intended use is to share the
output. The skill doesn't send anything itself — it produces a draft
that you review and then forward manually. The risk tier is a hint to
Roost that this output is human-visible and worth governance.
"""

import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

SKILL_META = {
    "name": "weekly_report",
    "description": "Draft a weekly status report from your completed tasks.",
    "trigger": "weekly",
    "version": "1.0.0",
    "risk_tier": "external_write",
}


async def run(args: dict) -> str:
    """
    Optional args:
        days: int     — how many days back to scan (default 7)
        tone: str     — "formal", "casual", or "bullet" (default "bullet")
    """
    days = int(args.get("days", 7))
    tone = (args.get("tone") or "bullet").lower()
    if tone not in {"formal", "casual", "bullet"}:
        tone = "bullet"

    try:
        from roost.services.tasks import list_tasks
    except ImportError:
        return "Task service is unavailable."

    cutoff = datetime.now() - timedelta(days=days)
    try:
        # roost.services.tasks.list_tasks returns a list of Task pydantic models.
        # Use .model_dump() to get dicts and then filter.
        all_done = list_tasks(status="done")
        done = [t.model_dump() if hasattr(t, "model_dump") else dict(t) for t in all_done]
    except Exception as e:
        logger.exception("task fetch failed")
        return f"Could not fetch tasks: {e}"

    # Task model exposes updated_at (ISO string) — that's when it transitioned to done.
    recent = [
        t for t in done
        if _parse_date(str(t.get("updated_at", ""))) >= cutoff
    ]

    if not recent:
        return f"No tasks completed in the last {days} days. Nothing to report."

    # Group by project (or "Unsorted")
    by_project: dict[str, list[dict]] = {}
    for t in recent:
        project = t.get("project_name") or "Unsorted"
        by_project.setdefault(project, []).append(t)

    # Build the raw input for the AI
    project_blocks: list[str] = []
    for project, tasks in sorted(by_project.items()):
        lines = [f"## {project}"]
        for t in tasks:
            lines.append(f"- {t.get('title', '(untitled)')}")
        project_blocks.append("\n".join(lines))
    raw_summary = "\n\n".join(project_blocks)

    # Ask the AI to turn it into a report. gemini_generate is @mcp.tool()-wrapped;
    # access the raw callable via .fn
    try:
        from roost.mcp.tools_gemini import gemini_generate as _tool
        gemini_generate = getattr(_tool, "fn", _tool)
    except ImportError:
        return f"## Weekly report (raw)\n\n{raw_summary}"

    tone_instructions = {
        "formal": "Write in a professional, third-person voice suitable for a board update.",
        "casual": "Write in a friendly, first-person voice suitable for a team stand-up.",
        "bullet": "Keep the output as tight bullet points, one line each.",
    }

    prompt = (
        f"Draft a weekly status report from the completed work below. "
        f"{tone_instructions[tone]}\n\n"
        f"Group by project. Highlight the 2-3 most impactful items. "
        f"Do NOT invent work that isn't in the list.\n\n"
        f"---\n{raw_summary}\n---"
    )

    try:
        result = gemini_generate(prompt=prompt, temperature=0.5, max_output_tokens=2048)
        draft = result.get("text", "") if isinstance(result, dict) else str(result)
    except Exception as e:
        logger.exception("report generation failed")
        return f"Report generation failed: {e}\n\nRaw list:\n{raw_summary}"

    return (
        f"## Weekly report ({days}-day window, {tone} tone)\n\n"
        f"**Draft — review before sharing.**\n\n{draft}"
    )


def _parse_date(s: str) -> datetime:
    """Parse a task's completed_at. Return epoch on failure."""
    if not s:
        return datetime.fromtimestamp(0)
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    return datetime.fromtimestamp(0)
