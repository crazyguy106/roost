"""Agentic Workflow planner (Phase 1).

``generate_plan()`` is one LLM round-trip that returns a structured
``Plan`` describing what the executor is about to do. The plan is
*advisory* — the executor is not bound to follow it. See
``docs/agentic-workflow-phase1.md`` §5 for the full contract.

The planner reuses Roost's existing provider abstraction
(``roost.adapters.create_agent``) with an empty tool registry, so it
inherits API keys and provider routing automatically.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from roost.config import AGENTIC_PLANNER_MODEL, AGENT_PROVIDER, PROJECT_ROOT
from roost.gemini_agent import MAX_TOOL_CALLS_PER_RUN
from roost.services.agentic_events import new_plan_id
from roost.services.planner_prompts import (
    PLAN_SYSTEM_PROMPT,
    PLAN_USER_TEMPLATE,
    format_tool_inventory,
)

logger = logging.getLogger("roost.services.planner")

MAX_STEPS = 8
SUMMARY_MAX = 120
DESCRIPTION_MAX = 200
RATIONALE_MAX = 200

_FAILURE_LOG = Path(PROJECT_ROOT) / "data" / "planner_failures.log"


# ── Dataclasses ──────────────────────────────────────────────────────

@dataclass
class PlanStep:
    step_id: int
    description: str
    tools: list[str]
    rationale: str

    def to_event_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Plan:
    plan_id: str
    summary: str
    steps: list[PlanStep]
    estimated_tool_calls: int
    estimated_duration_s: int
    guardian_warnings: list[str] = field(default_factory=list)
    raw_text: str = ""
    model: str = ""
    fallback_used: bool = False

    def to_event_dict(self) -> dict[str, Any]:
        """Shape the plan as the `data` of a `plan` SSE event (spec §4.2)."""
        return {
            "plan_id": self.plan_id,
            "summary": self.summary,
            "steps": [s.to_event_dict() for s in self.steps],
            "estimated_tool_calls": self.estimated_tool_calls,
            "estimated_duration_s": self.estimated_duration_s,
            "guardian_warnings": self.guardian_warnings,
            "fallback_used": self.fallback_used,
        }


# ── Parsing ──────────────────────────────────────────────────────────

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_blob(text: str) -> str | None:
    """Pull the JSON object out of a fenced ```json``` block, else best-effort.

    Falls back to grabbing the outermost ``{...}`` substring if the model
    skipped fences. Returns None if nothing plausible is present.
    """
    if not text:
        return None
    m = _JSON_FENCE_RE.search(text)
    if m:
        return m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start:end + 1]
    return None


def _coerce_step(raw: Any, index: int) -> PlanStep | None:
    if not isinstance(raw, dict):
        return None
    try:
        step_id = int(raw.get("step_id", index + 1))
    except (TypeError, ValueError):
        step_id = index + 1
    description = str(raw.get("description") or "")[:DESCRIPTION_MAX]
    rationale = str(raw.get("rationale") or "")[:RATIONALE_MAX]
    tools_raw = raw.get("tools") or []
    if not isinstance(tools_raw, list):
        return None
    tools = [str(t) for t in tools_raw if isinstance(t, (str, int))]
    if not description:
        return None
    return PlanStep(
        step_id=step_id,
        description=description,
        tools=tools,
        rationale=rationale,
    )


def parse_plan_json(
    text: str,
    tool_names: set[str],
) -> tuple[Plan | None, str]:
    """Parse + validate a planner response. Returns ``(plan, reason)``.

    On success the second element is ``""``. On failure, it's a short
    human-readable reason that gets logged + can label the fallback.
    """
    blob = _extract_json_blob(text)
    if blob is None:
        return None, "no JSON block found"
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e.msg}"

    if not isinstance(data, dict):
        return None, "top-level is not an object"

    summary = str(data.get("summary") or "")[:SUMMARY_MAX]
    if not summary:
        return None, "missing summary"

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None, "steps missing or empty"
    if len(raw_steps) > MAX_STEPS:
        raw_steps = raw_steps[:MAX_STEPS]

    steps: list[PlanStep] = []
    for i, raw in enumerate(raw_steps):
        step = _coerce_step(raw, i)
        if step is None:
            return None, f"step {i + 1} malformed"
        # Tool names must exist in inventory (empty inventory + empty tools list is fine).
        unknown = [t for t in step.tools if t not in tool_names]
        if unknown:
            return None, f"step {i + 1} references unknown tool(s): {unknown}"
        steps.append(step)

    try:
        est_calls = int(data.get("estimated_tool_calls", 0))
    except (TypeError, ValueError):
        est_calls = sum(len(s.tools) for s in steps)
    est_calls = max(0, min(est_calls, MAX_TOOL_CALLS_PER_RUN))

    try:
        est_duration = int(data.get("estimated_duration_s", 0))
    except (TypeError, ValueError):
        est_duration = 0
    est_duration = max(0, est_duration)

    plan = Plan(
        plan_id="",  # filled by caller
        summary=summary,
        steps=steps,
        estimated_tool_calls=est_calls,
        estimated_duration_s=est_duration,
    )
    return plan, ""


def _build_fallback(user_message: str, reason: str) -> Plan:
    desc = (user_message or "(empty request)").strip()[:DESCRIPTION_MAX]
    return Plan(
        plan_id="",
        summary="Plan generation failed — proceeding with direct execution.",
        steps=[
            PlanStep(
                step_id=1,
                description=desc,
                tools=[],
                rationale="Fallback plan; tool selection deferred to executor.",
            )
        ],
        estimated_tool_calls=0,
        estimated_duration_s=0,
        fallback_used=True,
        raw_text=reason,
    )


def _log_failure(user_message: str, raw_output: str, reason: str) -> None:
    """Append a failure record to ``data/planner_failures.log``.

    Best-effort: never raises. The user message is hashed (first 8 chars
    of its hex) to make local cross-references possible without storing
    full content.
    """
    try:
        _FAILURE_LOG.parent.mkdir(parents=True, exist_ok=True)
        import hashlib
        msg_hash = hashlib.sha256(user_message.encode("utf-8", "ignore")).hexdigest()[:8]
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "msg_hash": msg_hash,
            "reason": reason,
            "raw_output_preview": (raw_output or "")[:500],
        }
        with _FAILURE_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:  # pragma: no cover - logging must not crash callers
        logger.exception("Failed to write planner failure log")


# ── Provider call ────────────────────────────────────────────────────

def _resolve_planner_model() -> str:
    """Pick the model name to record on the Plan.

    The actual provider routing happens inside ``create_agent`` based on
    ``AGENT_PROVIDER``; ``model`` here is metadata for audit/UI display.
    """
    if AGENTIC_PLANNER_MODEL:
        return AGENTIC_PLANNER_MODEL
    # Best-effort: read the executor's own model from config without
    # importing every provider's config block.
    return os.getenv(
        {
            "gemini": "GEMINI_MODEL",
            "claude": "CLAUDE_MODEL",
            "openai": "OPENAI_MODEL",
            "ollama": "OLLAMA_MODEL",
        }.get(AGENT_PROVIDER, "AGENT_PROVIDER"),
        AGENT_PROVIDER,
    )


async def _call_planner_llm(prompt: str, system: str, session_id: str) -> str:
    """One LLM round-trip with no tool registry.

    Reuses ``create_agent`` to inherit provider routing. We pass an empty
    user_id and disable agent tools so the model only emits text.
    """
    from roost.adapters import create_agent, get_agentic_mode

    mode = get_agentic_mode()
    if mode is None:
        raise RuntimeError("No AI provider configured for planner")

    # Construct an agent but force tools off where the API allows it.
    agent = create_agent(mode, session_id, system_prompt=system)
    # All three agent classes accept include_agent_tools at construction;
    # but the factory has already set it True. We rely on the system prompt
    # discouraging tool calls and on parsing whatever text comes back.
    # The fallback path covers the case where the model still calls tools.
    try:
        if hasattr(agent, "tools"):
            agent.tools = []
        if hasattr(agent, "_tool_schemas"):
            agent._tool_schemas = []
    except Exception:  # pragma: no cover - defensive
        pass

    output = await agent.run(prompt, user_id="planner", on_progress=None)
    return output or ""


# ── Public entry point ──────────────────────────────────────────────

async def generate_plan(
    user_message: str,
    tool_inventory: list[dict],
    system_prompt: str = "",
    user_id: str = "",
    session_id: str = "",
    model: str | None = None,
) -> Plan:
    """Produce a structured plan for ``user_message``.

    Always returns a ``Plan`` — never raises. On any failure the returned
    plan has ``fallback_used = True`` and a single placeholder step.
    """
    tool_names = {t.get("name") for t in tool_inventory if isinstance(t, dict)}
    tool_names.discard(None)

    inventory_text = format_tool_inventory(tool_inventory)
    user_prompt = PLAN_USER_TEMPLATE.format(
        tool_inventory=inventory_text,
        user_message=user_message,
    )

    planner_session = session_id or f"planner:{user_id or 'anon'}"
    chosen_model = model or _resolve_planner_model()

    raw_text = ""
    try:
        raw_text = await _call_planner_llm(
            prompt=user_prompt,
            system=PLAN_SYSTEM_PROMPT,
            session_id=planner_session,
        )
    except Exception as e:
        logger.warning("Planner LLM call failed: %s", e)
        fallback = _build_fallback(user_message, f"llm_error: {e}")
        fallback.plan_id = new_plan_id()
        fallback.model = chosen_model
        _log_failure(user_message, "", f"llm_error: {e}")
        return fallback

    plan, reason = parse_plan_json(raw_text, tool_names)
    if plan is None:
        _log_failure(user_message, raw_text, reason)
        fallback = _build_fallback(user_message, reason)
        fallback.plan_id = new_plan_id()
        fallback.model = chosen_model
        fallback.raw_text = raw_text
        return fallback

    plan.plan_id = new_plan_id()
    plan.model = chosen_model
    plan.raw_text = raw_text
    return plan
