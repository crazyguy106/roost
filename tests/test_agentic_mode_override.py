"""Unit tests for get_agentic_mode() — per-request provider override.

The override path lets /chat?provider=X and POST {"provider": "X"} pick a
provider without touching AGENT_PROVIDER. Anything outside the whitelist
falls back to the env-based logic.
"""

from __future__ import annotations

import pytest


def test_override_returns_whitelisted_cli_modes(monkeypatch):
    """A whitelisted override wins regardless of env-var state."""
    from roost import adapters
    # Force env to a non-matching state to prove override wins.
    monkeypatch.setattr(adapters, "AGENT_PROVIDER", "gemini", raising=False)
    monkeypatch.setattr(adapters, "GEMINI_API_KEY", "", raising=False)

    assert adapters.get_agentic_mode(override="claude_cli") == "claude_cli"
    assert adapters.get_agentic_mode(override="gemini_cli") == "gemini_cli"
    assert adapters.get_agentic_mode(override="codex_cli") == "codex_cli"


def test_override_returns_whitelisted_api_modes(monkeypatch):
    """API-key modes are also overridable (no env key required)."""
    from roost import adapters
    monkeypatch.setattr(adapters, "AGENT_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(adapters, "CLAUDE_API_KEY", "", raising=False)
    monkeypatch.setattr(adapters, "OPENAI_API_KEY", "", raising=False)

    assert adapters.get_agentic_mode(override="claude") == "claude"
    assert adapters.get_agentic_mode(override="openai") == "openai"
    assert adapters.get_agentic_mode(override="gemini") == "gemini"
    assert adapters.get_agentic_mode(override="ollama") == "ollama"


def test_unknown_override_falls_back_to_env(monkeypatch):
    """A bogus override is ignored, env-based resolution still runs."""
    from roost import adapters
    monkeypatch.setattr(adapters, "AGENT_PROVIDER", "claude_cli", raising=False)
    # Bogus value should be ignored, env_provider wins.
    assert adapters.get_agentic_mode(override="totally-fake") == "claude_cli"
    # Empty string is treated the same as None.
    assert adapters.get_agentic_mode(override="") == "claude_cli"
    # None is the default and still uses env.
    assert adapters.get_agentic_mode(override=None) == "claude_cli"


def test_no_override_no_env_returns_none(monkeypatch):
    """Belt-and-braces: when nothing is set, return None as before."""
    from roost import adapters
    monkeypatch.setattr(adapters, "AGENT_PROVIDER", "", raising=False)
    monkeypatch.setattr(adapters, "GEMINI_API_KEY", "", raising=False)
    monkeypatch.setattr(adapters, "CLAUDE_API_KEY", "", raising=False)
    monkeypatch.setattr(adapters, "OPENAI_API_KEY", "", raising=False)

    assert adapters.get_agentic_mode() is None
