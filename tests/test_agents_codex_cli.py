"""Scaffold tests for ``roost.agents_codex_cli._CodexStreamEventParser``.

The Codex CLI provider is SCAFFOLD (see roost/agents_codex_cli.py
docstring) — the JSON event schema was inferred from @openai/codex
docs and the issue tracker, not validated against a live `codex exec`
session. These tests pin the *current* parser assumptions so a future
update against real event traces will surface schema drift as test
failures rather than silent breakage.

When you eventually run the CLI for real, capture a sample event stream
with ``codex exec --json -`` and replace the synthetic dicts below with
the real shapes before relying on this provider in production.
"""

from __future__ import annotations

import asyncio

from roost.agents_codex_cli import _CodexStreamEventParser, _coerce_text


# ── _coerce_text helper ──────────────────────────────────────────────

def test_coerce_text_passes_through_strings():
    assert _coerce_text("hello") == "hello"


def test_coerce_text_joins_output_text_blocks():
    blocks = [
        {"type": "output_text", "text": "line one"},
        {"type": "output_text", "text": "line two"},
    ]
    assert _coerce_text(blocks) == "line one\nline two"


def test_coerce_text_returns_empty_for_unknown_shapes():
    assert _coerce_text(None) == ""
    assert _coerce_text(42) == ""


# ── Parser: session capture ──────────────────────────────────────────

def test_parser_captures_session_id():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "session.created",
        "session_id": "cdx-sess-1",
    }))
    assert parser.captured_session_id == "cdx-sess-1"


def test_parser_captures_session_id_from_alt_field():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({"type": "session_started", "id": "cdx-2"}))
    assert parser.captured_session_id == "cdx-2"


# ── Parser: assistant message ────────────────────────────────────────

def test_parser_captures_assistant_message_string():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "agent_message",
        "role": "assistant",
        "content": "hello from codex",
    }))
    assert parser.final_text == "hello from codex"


def test_parser_captures_assistant_message_block_list():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "output_text", "text": "alpha"},
            {"type": "output_text", "text": "beta"},
        ],
    }))
    assert "alpha" in parser.final_text and "beta" in parser.final_text


# ── Parser: tool_call → tool_called ──────────────────────────────────

def test_parser_emits_tool_called():
    parser = _CodexStreamEventParser()
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "tool_call",
            "call_id": "tc_1",
            "name": "create_task",
            "arguments": {"title": "x"},
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_called"
    assert events[0]["call_id"] == "tc_1"
    assert events[0]["tool"] == "create_task"
    assert "tc_1" in parser.tool_starts


# ── Parser: tool_call_output → tool_returned / tool_failed ───────────

def test_parser_emits_tool_returned_on_success():
    parser = _CodexStreamEventParser()
    parser.tool_starts["tc_1"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "tool_call_output",
            "call_id": "tc_1",
            "status": "success",
            "output": "ok",
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_returned"
    assert events[0]["result_preview"] == "ok"


def test_parser_emits_tool_failed_on_error_status():
    parser = _CodexStreamEventParser()
    parser.tool_starts["tc_err"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "function_call_output",
            "call_id": "tc_err",
            "status": "error",
            "output": "boom",
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_failed"
    assert events[0]["error"] == "boom"


# ── Parser: error / final events ─────────────────────────────────────

def test_parser_error_event_becomes_final_text_when_empty():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "error",
        "message": "rate limited",
    }))
    assert "rate limited" in parser.final_text
    assert parser.final_text.startswith("Codex error")


def test_parser_final_event_provides_output_when_no_message_seen():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "task_complete",
        "final_output": "done",
    }))
    assert parser.final_text == "done"


def test_parser_ignores_unknown_event_types():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({"type": "ping"}))
    asyncio.run(parser.handle({"type": "thinking"}))
    assert parser.final_text == ""
    assert parser.captured_session_id is None
