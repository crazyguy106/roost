"""Unit tests for ``roost.agents_claude_cli._StreamEventParser``.

Parser-only — does not spawn the `claude` CLI subprocess. The tests feed
synthetic stream-json events that mirror real CLI output and assert the
Roost-side on_tool_event payload shape and final-text accumulation.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from roost.agents_claude_cli import (
    _StreamEventParser,
    _load_sessions,
    _save_sessions,
    _get_claude_session_id,
    _set_claude_session_id,
)


# ── Parser: session init ─────────────────────────────────────────────

def test_parser_captures_session_id_from_init_event():
    parser = _StreamEventParser()
    asyncio.run(parser.handle({
        "type": "system",
        "subtype": "init",
        "session_id": "abc-123",
    }))
    assert parser.captured_session_id == "abc-123"


def test_parser_captures_session_id_from_result_event():
    parser = _StreamEventParser()
    asyncio.run(parser.handle({
        "type": "result",
        "result": "done",
        "session_id": "result-sid",
    }))
    assert parser.captured_session_id == "result-sid"
    assert parser.final_text == "done"


# ── Parser: assistant text ───────────────────────────────────────────

def test_parser_accumulates_assistant_text_and_calls_on_progress():
    parser = _StreamEventParser()
    seen: list[str] = []

    async def on_progress(text):
        seen.append(text)

    asyncio.run(parser.handle(
        {
            "type": "assistant",
            "message": {"content": [
                {"type": "text", "text": "hello world"},
            ]},
        },
        on_progress=on_progress,
    ))
    assert parser.final_text == "hello world"
    assert seen == ["hello world"]


def test_parser_skips_empty_assistant_text():
    parser = _StreamEventParser()
    asyncio.run(parser.handle({
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": ""}]},
    }))
    assert parser.final_text == ""


# ── Parser: tool_use → tool_called event ─────────────────────────────

def test_parser_emits_tool_called_for_tool_use_block():
    parser = _StreamEventParser()
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "tool_42",
                "name": "create_task",
                "input": {"title": "demo"},
            }]},
        },
        on_tool_event=on_tool_event,
    ))
    assert len(events) == 1
    assert events[0]["type"] == "tool_called"
    assert events[0]["call_id"] == "tool_42"
    assert events[0]["tool"] == "create_task"
    assert events[0]["args_preview"] == {"title": "demo"}
    # Tool start was recorded for duration calculation.
    assert "tool_42" in parser.tool_starts


def test_parser_truncates_long_tool_input_in_preview():
    parser = _StreamEventParser()
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    big_input = {"body": "X" * 5000}
    asyncio.run(parser.handle(
        {
            "type": "assistant",
            "message": {"content": [{
                "type": "tool_use",
                "id": "tool_big",
                "name": "send_email",
                "input": big_input,
            }]},
        },
        on_tool_event=on_tool_event,
    ))
    preview = events[0]["args_preview"]
    # Either trimmed to a string ending with ellipsis, or the raw dict
    # if under the limit. Big input must be the string form.
    assert isinstance(preview, str)
    assert preview.endswith("...")


# ── Parser: tool_result → tool_returned / tool_failed ────────────────

def test_parser_emits_tool_returned_for_success_result():
    parser = _StreamEventParser()
    # Seed a tool start so duration is computed.
    parser.tool_starts["tool_77"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tool_77",
                "is_error": False,
                "content": "ok: 3 rows",
            }]},
        },
        on_tool_event=on_tool_event,
    ))
    assert len(events) == 1
    assert events[0]["type"] == "tool_returned"
    assert events[0]["call_id"] == "tool_77"
    assert events[0]["result_preview"] == "ok: 3 rows"
    assert events[0]["duration_ms"] >= 0
    # Start was consumed.
    assert "tool_77" not in parser.tool_starts


def test_parser_emits_tool_failed_for_error_result():
    parser = _StreamEventParser()
    parser.tool_starts["tool_x"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tool_x",
                "is_error": True,
                "content": "ConnectionRefused: 9999",
            }]},
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_failed"
    assert events[0]["error"] == "ConnectionRefused: 9999"
    assert "result_preview" not in events[0]


def test_parser_handles_structured_tool_result_content_list():
    """Claude CLI sometimes returns content as a list of {type: text, text: ...}."""
    parser = _StreamEventParser()
    parser.tool_starts["tool_y"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tool_y",
                "is_error": False,
                "content": [
                    {"type": "text", "text": "line one"},
                    {"type": "text", "text": "line two"},
                ],
            }]},
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_returned"
    assert "line one" in events[0]["result_preview"]
    assert "line two" in events[0]["result_preview"]


def test_parser_truncates_long_tool_result_preview():
    parser = _StreamEventParser()
    parser.tool_starts["tool_long"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "user",
            "message": {"content": [{
                "type": "tool_result",
                "tool_use_id": "tool_long",
                "is_error": False,
                "content": "Y" * 10_000,
            }]},
        },
        on_tool_event=on_tool_event,
    ))
    preview = events[0]["result_preview"]
    assert len(preview) <= 600  # ~500-char cap + ellipsis
    assert preview.endswith("...")


# ── Parser: ignores unrelated event types gracefully ────────────────

def test_parser_ignores_unknown_event_types():
    parser = _StreamEventParser()
    # Should not raise.
    asyncio.run(parser.handle({"type": "ping"}))
    asyncio.run(parser.handle({"type": "system", "subtype": "other"}))
    assert parser.final_text == ""
    assert parser.captured_session_id is None


def test_parser_callback_failures_do_not_crash_handler():
    parser = _StreamEventParser()

    async def boom_progress(_):
        raise RuntimeError("boom-progress")

    async def boom_tool_event(_):
        raise RuntimeError("boom-tool")

    # Assistant text path swallows on_progress errors.
    asyncio.run(parser.handle(
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "hi"},
        ]}},
        on_progress=boom_progress,
    ))
    assert parser.final_text == "hi"

    # Tool path swallows on_tool_event errors.
    asyncio.run(parser.handle(
        {"type": "assistant", "message": {"content": [{
            "type": "tool_use", "id": "t1", "name": "x", "input": {},
        }]}},
        on_tool_event=boom_tool_event,
    ))
    parser.tool_starts["t1"] = 0.0
    asyncio.run(parser.handle(
        {"type": "user", "message": {"content": [{
            "type": "tool_result", "tool_use_id": "t1",
            "is_error": False, "content": "ok",
        }]}},
        on_tool_event=boom_tool_event,
    ))


# ── Session JSON-file map ────────────────────────────────────────────

def test_session_map_round_trip(monkeypatch, tmp_path):
    session_file = tmp_path / "sessions.json"
    monkeypatch.setenv("CLAUDE_CLI_SESSION_FILE", str(session_file))

    assert _load_sessions() == {}
    _set_claude_session_id("user:1:agent", "claude-uuid-1")
    _set_claude_session_id("user:2:agent", "claude-uuid-2")
    assert _get_claude_session_id("user:1:agent") == "claude-uuid-1"
    assert _get_claude_session_id("user:2:agent") == "claude-uuid-2"
    assert _get_claude_session_id("user:missing") is None


def test_session_map_ignores_empty_keys(monkeypatch, tmp_path):
    session_file = tmp_path / "sessions.json"
    monkeypatch.setenv("CLAUDE_CLI_SESSION_FILE", str(session_file))

    _set_claude_session_id("", "ignored")
    _set_claude_session_id(None, "ignored")
    _set_claude_session_id("real", "")
    assert _load_sessions() == {}


def test_session_map_handles_corrupt_file(monkeypatch, tmp_path):
    session_file = tmp_path / "sessions.json"
    session_file.write_text("not valid json{{{")
    monkeypatch.setenv("CLAUDE_CLI_SESSION_FILE", str(session_file))
    # Should fall back to empty rather than raise.
    assert _load_sessions() == {}
