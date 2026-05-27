"""Tests for ``roost.services.agentic_events``."""

import asyncio
import json

import pytest

from roost.services.agentic_events import (
    ARG_STRING_LIMIT,
    REDACTED,
    RESULT_STRING_LIMIT,
    EventEmitter,
    new_call_id,
    new_plan_id,
    redact_secrets,
    truncate_args,
    truncate_result,
)


# ── Redaction ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "key",
    [
        "password", "PASSWORD", "user_password",
        "api_key", "apiKey", "api-key", "openai_api_key",
        "token", "access_token", "Bearer_Token",
        "secret", "client_secret", "MY_SECRET",
        "otp", "OTP_CODE",
        "auth", "auth_header",
    ],
)
def test_redact_replaces_known_secret_keys(key):
    result = redact_secrets({key: "supersensitive"})
    assert result[key] == REDACTED


def test_redact_leaves_safe_keys_untouched():
    result = redact_secrets({"to": "a@b.com", "subject": "hi", "count": 3})
    assert result == {"to": "a@b.com", "subject": "hi", "count": 3}


def test_redact_recurses_into_nested_structures():
    src = {
        "user": "alice",
        "creds": {"password": "p", "session_token": "t"},
        "history": [{"api_key": "k"}, {"safe": "ok"}],
    }
    out = redact_secrets(src)
    assert out["creds"]["password"] == REDACTED
    assert out["creds"]["session_token"] == REDACTED
    assert out["history"][0]["api_key"] == REDACTED
    assert out["history"][1] == {"safe": "ok"}
    assert out["user"] == "alice"


# ── Truncation ───────────────────────────────────────────────────────

def test_truncate_args_caps_long_strings():
    long_val = "x" * (ARG_STRING_LIMIT + 50)
    out = truncate_args({"query": long_val, "limit": 5})
    assert len(out["query"]) <= ARG_STRING_LIMIT + 50  # incl. suffix
    assert "truncated" in out["query"]
    assert out["limit"] == 5  # non-string passes through


def test_truncate_args_redacts_before_truncating():
    long_secret = "s" * (ARG_STRING_LIMIT + 100)
    out = truncate_args({"password": long_secret, "to": "alice@example.com"})
    assert out["password"] == REDACTED
    assert "alice@example.com" in out["to"]


def test_truncate_result_handles_dict():
    out = truncate_result({"hits": 3, "items": [1, 2, 3]})
    assert isinstance(out, str)
    assert "hits" in out


def test_truncate_result_handles_long_string():
    big = "z" * (RESULT_STRING_LIMIT + 100)
    out = truncate_result(big)
    assert len(out) <= RESULT_STRING_LIMIT + 50
    assert "truncated" in out


def test_truncate_result_handles_bytes():
    out = truncate_result(b"\x00\x01\x02" * 100)
    assert out == "<binary, 300 bytes>"


def test_truncate_result_handles_unserialisable():
    class Weird:
        def __str__(self):
            return "weird-thing"

    out = truncate_result(Weird())
    assert "weird-thing" in out


# ── ID helpers ───────────────────────────────────────────────────────

def test_plan_id_has_expected_shape():
    pid = new_plan_id()
    assert pid.startswith("agt_") and len(pid) == 12


def test_call_id_has_expected_shape():
    cid = new_call_id()
    assert cid.startswith("tc_") and len(cid) == 11


def test_ids_are_unique_in_a_burst():
    ids = {new_call_id() for _ in range(500)}
    # 500 random 4-byte tokens (32-bit space) — collisions astronomically unlikely.
    assert len(ids) == 500


# ── EventEmitter ─────────────────────────────────────────────────────

def test_emitter_streams_in_order():
    async def go():
        emitter = EventEmitter()
        emitter.emit("plan", {"plan_id": "agt_x"})
        emitter.emit("tool_called", {"tool": "search_emails"})
        emitter.emit("final", {"text": "done"})
        emitter.close()
        chunks = []
        async for chunk in emitter.stream():
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(go())
    assert len(chunks) == 3
    decoded = [json.loads(c.removeprefix("data: ").strip()) for c in chunks]
    assert [d["type"] for d in decoded] == ["plan", "tool_called", "final"]
    assert decoded[0]["plan_id"] == "agt_x"


def test_emitter_drops_events_after_close():
    async def go():
        emitter = EventEmitter()
        emitter.emit("a")
        emitter.close()
        emitter.emit("b")          # dropped
        chunks = []
        async for chunk in emitter.stream():
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(go())
    assert len(chunks) == 1
    assert json.loads(chunks[0].removeprefix("data: ").strip())["type"] == "a"


def test_emitter_close_is_idempotent():
    async def go():
        emitter = EventEmitter()
        emitter.emit("only")
        emitter.close()
        emitter.close()
        chunks = []
        async for chunk in emitter.stream():
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(go())
    assert len(chunks) == 1
