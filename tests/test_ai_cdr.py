"""Regression tests for ``roost.extras.messaging_external.services.ai_cdr``.

Exposed by a live prompt-injection drill on 2026-05-27:
  - ``_parse_json_response`` crashed on ``raw_text is None`` (Gemini
    SDK can return ``response.text == None`` with content in
    ``candidates[0].content.parts`` instead).
  - ``max_output_tokens=512`` truncated real buyer classifications mid-
    JSON; raised here only by the token bump in this commit.
"""

from __future__ import annotations

from roost.extras.messaging_external.services import ai_cdr
from roost.extras.messaging_external.services.ai_cdr import _parse_json_response


def test_parse_json_response_handles_none():
    """Gemini sometimes returns response.text == None — must not crash."""
    assert _parse_json_response(None) is None


def test_parse_json_response_handles_non_string():
    assert _parse_json_response(42) is None
    assert _parse_json_response({"already": "parsed"}) is None


def test_parse_json_response_strips_markdown_fences():
    text = "```json\n{\"intent\": \"general\", \"urgency\": \"cold\"}\n```"
    parsed = _parse_json_response(text)
    assert parsed == {"intent": "general", "urgency": "cold"}


def test_parse_json_response_extracts_from_trailing_prose():
    text = 'Here is the classification: {"intent": "general", "urgency": "cold"} thanks.'
    parsed = _parse_json_response(text)
    assert parsed == {"intent": "general", "urgency": "cold"}


def test_classify_returns_safe_default_with_no_api_key(monkeypatch):
    """When GEMINI_API_KEY is unset the classifier must short-circuit
    rather than attempt a network call."""
    monkeypatch.setattr(ai_cdr, "SAFE_DEFAULT", dict(ai_cdr.SAFE_DEFAULT))
    monkeypatch.setattr("roost.config.GEMINI_API_KEY", "", raising=False)

    result = ai_cdr.classify_message_sync("hello world")
    assert result["intent"] == "unknown"
    assert result["urgency"] == "cold"
    # Reason flag varies by which fail-safe branch fires; just confirm
    # the safe-default shape is preserved end-to-end.
    assert result["confidence"] == 0.0
