"""Unit tests for ``roost.agents_gemini_cli._GeminiStreamEventParser``.

Parser-only — does not spawn the `gemini` CLI subprocess. The tests feed
synthetic stream-json events that mirror @google/gemini-cli-core's
``stream-json-formatter`` output and assert the Roost-side on_tool_event
payload shape and final-text accumulation.
"""

from __future__ import annotations

import asyncio

from roost.agents_gemini_cli import _GeminiStreamEventParser


# ── Parser: session init ─────────────────────────────────────────────

def test_parser_captures_session_id_from_init_event():
    parser = _GeminiStreamEventParser()
    asyncio.run(parser.handle({
        "type": "init",
        "timestamp": "2026-05-24T00:00:00Z",
        "session_id": "sess-abc-123",
        "model": "gemini-3-flash-preview",
    }))
    assert parser.captured_session_id == "sess-abc-123"


# ── Parser: assistant message ────────────────────────────────────────

def test_parser_accumulates_final_message_and_calls_on_progress():
    parser = _GeminiStreamEventParser()
    seen: list[str] = []

    async def on_progress(text):
        seen.append(text)

    asyncio.run(parser.handle(
        {
            "type": "message",
            "role": "assistant",
            "content": "hello world",
        },
        on_progress=on_progress,
    ))
    assert parser.final_text == "hello world"
    assert seen == ["hello world"]


def test_parser_delta_message_streams_but_does_not_overwrite_final():
    parser = _GeminiStreamEventParser()
    seen: list[str] = []

    async def on_progress(text):
        seen.append(text)

    # First a final consolidated message.
    asyncio.run(parser.handle(
        {"type": "message", "role": "assistant", "content": "final"},
        on_progress=on_progress,
    ))
    # Then a stray delta — must not overwrite final_text.
    asyncio.run(parser.handle(
        {"type": "message", "role": "assistant",
         "content": "delta", "delta": True},
        on_progress=on_progress,
    ))
    assert parser.final_text == "final"
    assert seen == ["final", "delta"]


def test_parser_ignores_user_role_messages():
    parser = _GeminiStreamEventParser()
    asyncio.run(parser.handle({
        "type": "message",
        "role": "user",
        "content": "should be ignored",
    }))
    assert parser.final_text == ""


# ── Parser: tool_use → tool_called ───────────────────────────────────

def test_parser_emits_tool_called_for_tool_use_event():
    parser = _GeminiStreamEventParser()
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "tool_use",
            "tool_id": "tool_42",
            "tool_name": "create_task",
            "parameters": {"title": "demo"},
        },
        on_tool_event=on_tool_event,
    ))
    assert len(events) == 1
    assert events[0]["type"] == "tool_called"
    assert events[0]["call_id"] == "tool_42"
    assert events[0]["tool"] == "create_task"
    assert events[0]["args_preview"] == {"title": "demo"}
    assert "tool_42" in parser.tool_starts


def test_parser_truncates_long_tool_input_in_preview():
    parser = _GeminiStreamEventParser()
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    big_input = {"body": "X" * 5000}
    asyncio.run(parser.handle(
        {
            "type": "tool_use",
            "tool_id": "tool_big",
            "tool_name": "send_email",
            "parameters": big_input,
        },
        on_tool_event=on_tool_event,
    ))
    preview = events[0]["args_preview"]
    assert isinstance(preview, str)
    assert preview.endswith("...")


# ── Parser: tool_result → tool_returned / tool_failed ────────────────

def test_parser_emits_tool_returned_for_success_result():
    parser = _GeminiStreamEventParser()
    parser.tool_starts["tool_77"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "tool_result",
            "tool_id": "tool_77",
            "status": "success",
            "output": "ok: 3 rows",
        },
        on_tool_event=on_tool_event,
    ))
    assert len(events) == 1
    assert events[0]["type"] == "tool_returned"
    assert events[0]["call_id"] == "tool_77"
    assert events[0]["result_preview"] == "ok: 3 rows"
    assert events[0]["duration_ms"] >= 0
    assert "tool_77" not in parser.tool_starts


def test_parser_emits_tool_failed_for_error_result():
    parser = _GeminiStreamEventParser()
    parser.tool_starts["tool_x"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "tool_result",
            "tool_id": "tool_x",
            "status": "error",
            "error": {"type": "ConnectionError", "message": "refused on :9999"},
        },
        on_tool_event=on_tool_event,
    ))
    assert events[0]["type"] == "tool_failed"
    assert "refused on :9999" in events[0]["error"]
    assert "result_preview" not in events[0]


def test_parser_truncates_long_tool_result_preview():
    parser = _GeminiStreamEventParser()
    parser.tool_starts["tool_long"] = 0.0
    events: list[dict] = []

    async def on_tool_event(payload):
        events.append(payload)

    asyncio.run(parser.handle(
        {
            "type": "tool_result",
            "tool_id": "tool_long",
            "status": "success",
            "output": "Y" * 10_000,
        },
        on_tool_event=on_tool_event,
    ))
    preview = events[0]["result_preview"]
    assert len(preview) <= 600
    assert preview.endswith("...")


# ── Parser: error events ─────────────────────────────────────────────

def test_parser_promotes_error_event_to_final_text():
    parser = _GeminiStreamEventParser()
    asyncio.run(parser.handle({
        "type": "error",
        "severity": "error",
        "message": "model overloaded — retry",
    }))
    assert "model overloaded" in parser.final_text
    assert parser.final_text.startswith("Gemini error")


def test_parser_error_does_not_clobber_existing_final_text():
    parser = _GeminiStreamEventParser()
    asyncio.run(parser.handle({
        "type": "message", "role": "assistant", "content": "answer",
    }))
    asyncio.run(parser.handle({
        "type": "error", "severity": "warning", "message": "rate limited",
    }))
    assert parser.final_text == "answer"


def test_parser_result_error_carries_message_when_final_text_empty():
    parser = _GeminiStreamEventParser()
    asyncio.run(parser.handle({
        "type": "result",
        "status": "error",
        "error": {"message": "auth required"},
    }))
    assert "auth required" in parser.final_text


# ── Parser: graceful no-ops ──────────────────────────────────────────

def test_parser_ignores_unknown_event_types():
    parser = _GeminiStreamEventParser()
    asyncio.run(parser.handle({"type": "ping"}))
    asyncio.run(parser.handle({"type": "init"}))  # init without session_id
    assert parser.final_text == ""
    assert parser.captured_session_id is None


def test_parser_callback_failures_do_not_crash_handler():
    parser = _GeminiStreamEventParser()

    async def boom_progress(_):
        raise RuntimeError("boom-progress")

    async def boom_tool_event(_):
        raise RuntimeError("boom-tool")

    asyncio.run(parser.handle(
        {"type": "message", "role": "assistant", "content": "hi"},
        on_progress=boom_progress,
    ))
    assert parser.final_text == "hi"

    asyncio.run(parser.handle(
        {"type": "tool_use", "tool_id": "t1",
         "tool_name": "x", "parameters": {}},
        on_tool_event=boom_tool_event,
    ))
    parser.tool_starts["t1"] = 0.0
    asyncio.run(parser.handle(
        {"type": "tool_result", "tool_id": "t1",
         "status": "success", "output": "ok"},
        on_tool_event=boom_tool_event,
    ))
