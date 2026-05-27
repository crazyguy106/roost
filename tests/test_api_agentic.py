"""End-to-end tests for the Agentic Workflow SSE endpoint.

Spec: docs/agentic-workflow-phase1.md §4 (event protocol), §9 (acceptance).
"""

from __future__ import annotations

import importlib
import json
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

import roost.services.planner as planner_mod
from roost.services.planner import Plan, PlanStep


# ── Test fixtures ────────────────────────────────────────────────────

class _FakeAgent:
    """Stand-in for a provider agent.

    ``run()`` emits one tool_called + one tool_returned via on_tool_event
    so the SSE stream contains the full lifecycle the UI expects.
    """

    def __init__(self):
        self.tool_scope = "web"
        # Mimic a Claude-format tool list (name + description top-level).
        self.tools = [
            {"name": "search_emails", "description": "Search inbox", "input_schema": {}},
        ]

    async def run(self, prompt, user_id="", on_progress=None,
                  confirmation_callback=None, on_tool_event=None, **_):
        if on_tool_event is not None:
            await on_tool_event({
                "type": "tool_called",
                "tool": "search_emails",
                "args": {"query": "is:unread"},
                "call_id": "tc_abcd1234",
            })
            await on_tool_event({
                "type": "tool_returned",
                "tool": "search_emails",
                "call_id": "tc_abcd1234",
                "result": {"count": 3},
                "duration_ms": 42,
            })
        return "You have 3 unread emails."


def _plan_with_search_step() -> Plan:
    return Plan(
        plan_id="agt_aaaaaaaa",
        summary="Check unread mail.",
        steps=[
            PlanStep(
                step_id=1,
                description="Search the inbox for unread mail.",
                tools=["search_emails"],
                rationale="User asked about new mail.",
            )
        ],
        estimated_tool_calls=1,
        estimated_duration_s=3,
        model="test-model",
    )


def _build_app(monkeypatch, *, enabled=True, agent_enabled=True, mode="claude"):
    """Construct a tiny FastAPI app mounting just /api/agentic.

    Patches config + provider lookup + planner so no LLM is contacted.
    """
    import roost.config as cfg
    monkeypatch.setattr(cfg, "AGENTIC_WORKFLOW_ENABLED", enabled)
    monkeypatch.setattr(cfg, "AGENT_ENABLED", agent_enabled)

    # Re-import the API module so it picks up the patched config flag.
    import roost.web.api_agentic as api
    importlib.reload(api)
    # After reload, re-apply the config patch onto the module-local name.
    monkeypatch.setattr(api, "AGENTIC_WORKFLOW_ENABLED", enabled)
    monkeypatch.setattr(api, "AGENT_ENABLED", agent_enabled)
    monkeypatch.setattr(api, "get_agentic_mode", lambda *a, **kw: mode)
    monkeypatch.setattr(api, "create_agent", lambda *a, **kw: _FakeAgent())
    monkeypatch.setattr(api, "build_agent_context", lambda *a, **kw: "system")
    monkeypatch.setattr(api, "save_chat_history", lambda *a, **kw: None)

    async def fake_generate_plan(**_kw):
        return _plan_with_search_step()
    monkeypatch.setattr(api, "generate_plan", fake_generate_plan)

    # Disable the rate limiter so successive tests don't 429 each other.
    api._last_message.clear()
    api._RATE_LIMIT_SECONDS = 0  # type: ignore[attr-defined]

    app = FastAPI()

    @app.middleware("http")
    async def inject_user(request: Request, call_next):
        request.state.current_user = {"user_id": 99, "username": "test"}
        return await call_next(request)

    app.include_router(api.router)
    return app


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Parse the body of an SSE response into a list of decoded events."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            events.append({"type": "_done"})
            continue
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            continue
    return events


# ── Tests ────────────────────────────────────────────────────────────

def test_endpoint_returns_404_when_flag_off(monkeypatch):
    app = _build_app(monkeypatch, enabled=False)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "hi"})
    assert res.status_code == 404


def test_endpoint_returns_503_when_agent_disabled(monkeypatch):
    app = _build_app(monkeypatch, agent_enabled=False)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "hi"})
    assert res.status_code == 503


def test_endpoint_rejects_empty_message(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "   "})
    assert res.status_code == 400


def test_endpoint_rejects_oversize_message(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "x" * 10001})
    assert res.status_code == 400


def test_endpoint_streams_plan_then_tool_then_final(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "any unread mail?"})
    assert res.status_code == 200
    events = _parse_sse(res.text)

    types_in_order = [e.get("type") for e in events]

    # Spec §4.2 ordering: plan → tool_called → tool_returned → final → _done
    assert "plan" in types_in_order
    assert "tool_called" in types_in_order
    assert "tool_returned" in types_in_order
    assert "final" in types_in_order
    assert types_in_order[-1] == "_done"

    plan_idx = types_in_order.index("plan")
    called_idx = types_in_order.index("tool_called")
    returned_idx = types_in_order.index("tool_returned")
    final_idx = types_in_order.index("final")
    assert plan_idx < called_idx < returned_idx < final_idx


def test_plan_event_carries_required_fields(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "any unread mail?"})
    events = _parse_sse(res.text)
    plan_event = next(e for e in events if e.get("type") == "plan")
    assert plan_event["plan_id"] == "agt_aaaaaaaa"
    data = plan_event["data"]
    for key in (
        "plan_id", "summary", "steps", "estimated_tool_calls",
        "estimated_duration_s", "guardian_warnings", "fallback_used",
    ):
        assert key in data


def test_tool_events_carry_plan_and_step_correlation(monkeypatch):
    """`tool_called` should be enriched with plan_id and step_id."""
    app = _build_app(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "any unread mail?"})
    events = _parse_sse(res.text)
    tool_called = next(e for e in events if e.get("type") == "tool_called")
    assert tool_called["plan_id"] == "agt_aaaaaaaa"
    assert tool_called["step_id"] == 1
    assert tool_called["tool"] == "search_emails"


def test_final_event_carries_response_text(monkeypatch):
    app = _build_app(monkeypatch)
    client = TestClient(app)
    res = client.post("/api/agentic/message", json={"message": "any unread mail?"})
    events = _parse_sse(res.text)
    final = next(e for e in events if e.get("type") == "final")
    assert "3 unread" in final["text"]
    assert final["plan_id"] == "agt_aaaaaaaa"
