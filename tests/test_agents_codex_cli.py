"""Tests for ``roost.agents_codex_cli._CodexStreamEventParser``.

Validated against a live ``codex exec --json --skip-git-repo-check`` run
on 2026-05-27 (codex 2025-12 schema). All meaningful payloads arrive
nested inside ``item.completed`` events; the parser unwraps them.
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

def test_parser_captures_session_id_from_thread_started():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "thread.started",
        "thread_id": "019e697d-c067-7113-ae23-79da7f0c1f2c",
    }))
    assert parser.captured_session_id == "019e697d-c067-7113-ae23-79da7f0c1f2c"


# ── Parser: assistant message via item.completed ─────────────────────

def test_parser_captures_assistant_message_from_item_completed():
    """Live observed shape: type=item.completed wraps item.type=agent_message."""
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "agent_message",
            "text": "Hi. What would you like to work on?",
        },
    }))
    assert parser.final_text == "Hi. What would you like to work on?"


def test_parser_captures_assistant_message_with_content_blocks():
    """Older Codex variant: item.content as a block list."""
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "item.completed",
        "item": {
            "id": "item_0",
            "type": "message",
            "content": [
                {"type": "output_text", "text": "alpha"},
                {"type": "output_text", "text": "beta"},
            ],
        },
    }))
    assert "alpha" in parser.final_text and "beta" in parser.final_text


def test_parser_fires_on_progress_for_assistant_message():
    parser = _CodexStreamEventParser()
    progress: list[str] = []

    async def on_progress(text):
        progress.append(text)

    asyncio.run(parser.handle(
        {
            "type": "item.completed",
            "item": {"id": "i", "type": "agent_message", "text": "yo"},
        },
        on_progress=on_progress,
    ))
    assert progress == ["yo"]


# ── Parser: tool calls via item.completed ────────────────────────────

def test_parser_emits_tool_called_from_function_call_item():
    parser = _CodexStreamEventParser()
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "item.completed",
            "item": {
                "id": "fc_1",
                "type": "function_call",
                "call_id": "fc_1",
                "name": "create_task",
                "arguments": {"title": "x"},
            },
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_called"
    assert events[0]["call_id"] == "fc_1"
    assert events[0]["tool"] == "create_task"
    assert "fc_1" in parser.tool_starts


def test_parser_emits_tool_returned_on_success():
    parser = _CodexStreamEventParser()
    parser.tool_starts["fc_1"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "item.completed",
            "item": {
                "id": "fco_1",
                "type": "function_call_output",
                "call_id": "fc_1",
                "status": "success",
                "output": "ok",
            },
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_returned"
    assert events[0]["result_preview"] == "ok"


def test_parser_emits_tool_failed_on_error_status():
    parser = _CodexStreamEventParser()
    parser.tool_starts["fc_err"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "item.completed",
            "item": {
                "id": "fco_err",
                "type": "function_call_output",
                "call_id": "fc_err",
                "status": "error",
                "output": "boom",
            },
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_failed"
    assert events[0]["error"] == "boom"


# ── Parser: boundary + error events ──────────────────────────────────

def test_parser_turn_started_is_noop():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({"type": "turn.started"}))
    assert parser.final_text == ""


def test_parser_turn_completed_is_noop():
    """turn.completed carries usage stats but no final text."""
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "turn.completed",
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }))
    assert parser.final_text == ""


def test_parser_error_event_becomes_final_text_when_empty():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({
        "type": "error",
        "message": "rate limited",
    }))
    assert "rate limited" in parser.final_text
    assert parser.final_text.startswith("Codex error")


def test_parser_ignores_unknown_event_types():
    parser = _CodexStreamEventParser()
    asyncio.run(parser.handle({"type": "ping"}))
    asyncio.run(parser.handle({"type": "thinking"}))
    assert parser.final_text == ""
    assert parser.captured_session_id is None
