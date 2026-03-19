"""Tests for roost.cli.onboard — the setup wizard."""

import os
import tempfile

import pytest

from roost.cli.onboard import (
    AI_PROVIDERS,
    _build_docker_compose_args,
    _generate_env,
    _validate_telegram_token,
)


class TestTelegramTokenValidation:
    """Validate bot token format checking."""

    def test_valid_token(self):
        assert _validate_telegram_token("123456789:ABCdefGHIjklMNOpqrSTUvwxyz")

    def test_missing_colon(self):
        assert not _validate_telegram_token("123456789ABCdefGHIjklMNOpqrSTUvwxyz")

    def test_non_numeric_prefix(self):
        assert not _validate_telegram_token("abc:ABCdefGHIjklMNOpqrSTUvwxyz")

    def test_too_short_suffix(self):
        assert not _validate_telegram_token("123:short")

    def test_empty_string(self):
        assert not _validate_telegram_token("")


class TestGenerateEnv:
    """Env file generation from wizard answers."""

    BASE_ANSWERS = {
        "session_secret": "test-secret-hex",
        "web_username": "admin",
        "web_password": "secure-pass-123",
        "ssh_password": "secure-pass-123",
    }

    def test_gemini_provider(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "gemini",
            "ai_key": "AIzaSy-test-key",
        }
        env = _generate_env(answers)
        assert "AGENT_PROVIDER=gemini" in env
        assert "GEMINI_API_KEY=AIzaSy-test-key" in env
        assert "AI_ENABLED=true" in env

    def test_claude_provider(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "claude",
            "ai_key": "sk-ant-test-key",
        }
        env = _generate_env(answers)
        assert "AGENT_PROVIDER=claude" in env
        assert "CLAUDE_API_KEY=sk-ant-test-key" in env

    def test_openai_provider(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "openai",
            "ai_key": "sk-proj-test-key",
        }
        env = _generate_env(answers)
        assert "AGENT_PROVIDER=openai" in env
        assert "OPENAI_API_KEY=sk-proj-test-key" in env

    def test_ollama_provider(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "ollama",
            "ollama_url": "http://10.0.0.5:11434/v1",
        }
        env = _generate_env(answers)
        assert "AGENT_PROVIDER=ollama" in env
        assert "OLLAMA_URL=http://10.0.0.5:11434/v1" in env

    def test_telegram_enabled(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "gemini",
            "ai_key": "test",
            "telegram_token": "123:ABCdef",
            "telegram_user_id": "99999",
        }
        env = _generate_env(answers)
        assert "TELEGRAM_ENABLED=true" in env
        assert "TELEGRAM_BOT_TOKEN=123:ABCdef" in env
        assert "TELEGRAM_ALLOWED_USERS=99999" in env

    def test_telegram_skipped(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "gemini",
            "ai_key": "test",
        }
        env = _generate_env(answers)
        assert "TELEGRAM_ENABLED=false" in env

    def test_session_secret_included(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "gemini",
            "ai_key": "test",
        }
        env = _generate_env(answers)
        assert "SESSION_SECRET=test-secret-hex" in env

    def test_google_ms_default_disabled(self):
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "gemini",
            "ai_key": "test",
        }
        env = _generate_env(answers)
        assert "GOOGLE_ENABLED=false" in env
        assert "MS_ENABLED=false" in env

    def test_env_file_is_writable(self):
        """Generated env can be written to a file and is valid."""
        answers = {
            **self.BASE_ANSWERS,
            "ai_provider": "gemini",
            "ai_key": "test-key",
            "telegram_token": "123:tokenvalue_long_enough_here",
            "telegram_user_id": "12345",
        }
        env = _generate_env(answers)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
            f.write(env)
            f.flush()
            # Verify it's parseable (key=value lines)
            for line in env.split("\n"):
                line = line.strip()
                if line and not line.startswith("#"):
                    assert "=" in line, f"Invalid line: {line}"
        os.unlink(f.name)


class TestBuildDockerComposeArgs:
    """Docker compose build arg generation."""

    def test_ai_and_telegram(self):
        answers = {"ai_provider": "gemini", "telegram_token": "123:abc"}
        args = _build_docker_compose_args(answers)
        assert "ENABLE_AI=true" in args
        assert "ENABLE_TELEGRAM=true" in args
        assert "ENABLE_GOOGLE=false" in args

    def test_ai_only(self):
        answers = {"ai_provider": "claude"}
        args = _build_docker_compose_args(answers)
        assert "ENABLE_AI=true" in args
        assert "ENABLE_TELEGRAM=false" in args

    def test_command_starts_with_docker_compose_build(self):
        answers = {"ai_provider": "gemini"}
        args = _build_docker_compose_args(answers)
        assert args[:3] == ["docker", "compose", "build"]


class TestAIProviders:
    """Provider config is complete and consistent."""

    def test_four_providers(self):
        assert len(AI_PROVIDERS) == 4

    def test_all_have_required_keys(self):
        for key, provider in AI_PROVIDERS.items():
            assert "name" in provider
            assert "provider" in provider
            assert "hint" in provider

    def test_ollama_has_no_key_var(self):
        ollama = AI_PROVIDERS["4"]
        assert ollama["key_var"] is None
        assert ollama["provider"] == "ollama"
