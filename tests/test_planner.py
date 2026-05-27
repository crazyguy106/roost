"""Tests for ``roost.services.planner`` — parse, validate, fallback."""

import asyncio
import json

import pytest

from roost.services import planner as planner_mod
from roost.services.planner import (
    MAX_STEPS,
    Plan,
    PlanStep,
    _build_fallback,
    generate_plan,
    parse_plan_json,
)


# ── parse_plan_json ──────────────────────────────────────────────────

def _ok_plan_text(tools_in_step_1: list[str] | None = None) -> str:
    tools = tools_in_step_1 or ["search_emails"]
    return (
        "```json\n"
        + json.dumps(
            {
                "summary": "Check unread mail.",
                "steps": [
                    {
                        "step_id": 1,
                        "description": "Search for unread emails in inbox.",
                        "tools": tools,
                        "rationale": "User asked about new mail.",
                    }
                ],
                "estimated_tool_calls": len(tools),
                "estimated_duration_s": 5,
            }
        )
        + "\n```"
    )


def test_parse_valid_plan():
    plan, reason = parse_plan_json(_ok_plan_text(), {"search_emails"})
    assert plan is not None
    assert reason == ""
    assert plan.summary == "Check unread mail."
    assert len(plan.steps) == 1
    assert plan.steps[0].tools == ["search_emails"]
    assert plan.estimated_tool_calls == 1


def test_parse_hallucinated_tool_fails():
    plan, reason = parse_plan_json(_ok_plan_text(["not_a_real_tool"]), {"search_emails"})
    assert plan is None
    assert "unknown tool" in reason


def test_parse_no_tools_plan_is_valid():
    text = (
        "```json\n"
        + json.dumps(
            {
                "summary": "Reply to greeting.",
                "steps": [
                    {
                        "step_id": 1,
                        "description": "Respond conversationally.",
                        "tools": [],
                        "rationale": "Greeting only.",
                    }
                ],
                "estimated_tool_calls": 0,
                "estimated_duration_s": 1,
            }
        )
        + "\n```"
    )
    plan, reason = parse_plan_json(text, set())
    assert plan is not None
    assert reason == ""
    assert plan.steps[0].tools == []


def test_parse_missing_summary_fails():
    text = "```json\n" + json.dumps({"steps": [{"description": "x", "tools": []}]}) + "\n```"
    plan, reason = parse_plan_json(text, set())
    assert plan is None
    assert "summary" in reason


def test_parse_empty_steps_fails():
    text = "```json\n" + json.dumps({"summary": "ok", "steps": []}) + "\n```"
    plan, reason = parse_plan_json(text, set())
    assert plan is None
    assert "steps" in reason


def test_parse_no_json_block_fails():
    plan, reason = parse_plan_json("Sorry, I cannot help with this.", set())
    assert plan is None
    assert "no JSON block found" in reason


def test_parse_caps_steps_at_max():
    too_many = {
        "summary": "Many steps.",
        "steps": [
            {"step_id": i, "description": f"step {i}", "tools": [], "rationale": "x"}
            for i in range(1, MAX_STEPS + 5)
        ],
        "estimated_tool_calls": 0,
        "estimated_duration_s": 0,
    }
    text = "```json\n" + json.dumps(too_many) + "\n```"
    plan, _ = parse_plan_json(text, set())
    assert plan is not None
    assert len(plan.steps) == MAX_STEPS


def test_parse_accepts_unfenced_json():
    raw = json.dumps(
        {
            "summary": "Plain JSON output.",
            "steps": [
                {
                    "step_id": 1,
                    "description": "Do the thing.",
                    "tools": [],
                    "rationale": "Why not.",
                }
            ],
            "estimated_tool_calls": 0,
            "estimated_duration_s": 0,
        }
    )
    plan, reason = parse_plan_json(raw, set())
    assert plan is not None, reason


# ── Fallback shape ───────────────────────────────────────────────────

def test_fallback_plan_shape():
    fb = _build_fallback("send mail to alice", "no JSON block found")
    assert fb.fallback_used is True
    assert len(fb.steps) == 1
    assert fb.steps[0].tools == []
    assert "send mail to alice" in fb.steps[0].description


# ── generate_plan with mocked LLM ────────────────────────────────────

class _FakeAgent:
    def __init__(self, output: str):
        self._output = output

    async def run(self, prompt, user_id="", on_progress=None, **kwargs):
        return self._output


def _install_fake_agent(monkeypatch, output: str):
    """Patch ``create_agent`` + ``get_agentic_mode`` for offline testing."""
    monkeypatch.setattr(planner_mod, "_call_planner_llm", lambda *_a, **_kw: _async_return(output))


async def _async_return(value):
    return value


def test_generate_plan_happy_path(monkeypatch):
    _install_fake_agent(monkeypatch, _ok_plan_text())
    plan = asyncio.run(
        generate_plan(
            user_message="check my inbox",
            tool_inventory=[{"name": "search_emails", "description": "search inbox"}],
        )
    )
    assert isinstance(plan, Plan)
    assert plan.fallback_used is False
    assert plan.plan_id.startswith("agt_")
    assert plan.steps[0].tools == ["search_emails"]


def test_generate_plan_falls_back_on_bad_output(monkeypatch):
    _install_fake_agent(monkeypatch, "I'm not going to plan this.")
    plan = asyncio.run(
        generate_plan(
            user_message="something",
            tool_inventory=[{"name": "search_emails", "description": "x"}],
        )
    )
    assert plan.fallback_used is True
    assert plan.plan_id.startswith("agt_")
    assert "something" in plan.steps[0].description


def test_generate_plan_falls_back_on_llm_exception(monkeypatch):
    async def boom(*a, **kw):
        raise RuntimeError("provider down")
    monkeypatch.setattr(planner_mod, "_call_planner_llm", boom)
    plan = asyncio.run(
        generate_plan(
            user_message="ping",
            tool_inventory=[],
        )
    )
    assert plan.fallback_used is True
    assert "ping" in plan.steps[0].description


def test_generate_plan_event_dict_matches_spec(monkeypatch):
    _install_fake_agent(monkeypatch, _ok_plan_text())
    plan = asyncio.run(
        generate_plan(
            user_message="x",
            tool_inventory=[{"name": "search_emails", "description": "x"}],
        )
    )
    event = plan.to_event_dict()
    # Spec §4.2 required keys
    for key in (
        "plan_id",
        "summary",
        "steps",
        "estimated_tool_calls",
        "estimated_duration_s",
        "guardian_warnings",
        "fallback_used",
    ):
        assert key in event
    # raw_text + model are NOT in the event payload (they're metadata).
    assert "raw_text" not in event
    assert "model" not in event
