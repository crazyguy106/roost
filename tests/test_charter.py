"""Tests for roost.charter — charter loading and caching."""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def charter_dir(tmp_path):
    """Create a temporary charter directory with test files."""
    charter = tmp_path / "charter.md"
    charter.write_text("# Test Charter\n\nBe helpful and concise.")

    gemini = tmp_path / "gemini.md"
    gemini.write_text("# Gemini Notes\n\nUse tools efficiently.")

    claude = tmp_path / "claude.md"
    claude.write_text("# Claude Notes\n\nThink step-by-step.")

    # Patch the charter module to use our temp dir
    import roost.charter
    original_dir = roost.charter._CHARTER_DIR
    roost.charter._CHARTER_DIR = tmp_path
    roost.charter.reload_charter()

    yield tmp_path

    roost.charter._CHARTER_DIR = original_dir
    roost.charter.reload_charter()


class TestGetCharter:
    """Core charter loading."""

    def test_core_charter_only(self, charter_dir):
        from roost.charter import get_charter
        result = get_charter()
        assert "Test Charter" in result
        assert "Be helpful and concise" in result
        assert "Gemini" not in result

    def test_with_provider(self, charter_dir):
        from roost.charter import get_charter
        result = get_charter("gemini")
        assert "Test Charter" in result
        assert "Gemini Notes" in result
        assert "---" in result  # Separator between core and provider

    def test_unknown_provider_returns_core_only(self, charter_dir):
        from roost.charter import get_charter
        result = get_charter("ollama")
        assert "Test Charter" in result
        assert "Ollama" not in result

    def test_caching(self, charter_dir):
        from roost.charter import get_charter
        result1 = get_charter("claude")
        result2 = get_charter("claude")
        assert result1 is result2  # Same object = cached

    def test_reload_clears_cache(self, charter_dir):
        from roost.charter import get_charter, reload_charter
        result1 = get_charter("claude")
        reload_charter()
        result2 = get_charter("claude")
        assert result1 is not result2  # Different object after reload
        assert result1 == result2  # Same content though


class TestSaveCharter:
    """Charter save operations."""

    def test_save_core(self, charter_dir):
        from roost.charter import save_charter, get_charter_raw
        save_charter("# New Charter\n\nUpdated content.")
        assert "Updated content" in get_charter_raw()

    def test_save_clears_cache(self, charter_dir):
        from roost.charter import get_charter, save_charter
        old = get_charter()
        save_charter("# Changed Charter")
        new = get_charter()
        assert old != new
        assert "Changed Charter" in new

    def test_save_provider(self, charter_dir):
        from roost.charter import save_provider_charter, get_provider_charter_raw
        save_provider_charter("gemini", "# Updated Gemini\n\nNew guidance.")
        assert "Updated Gemini" in get_provider_charter_raw("gemini")

    def test_save_unknown_provider_raises(self, charter_dir):
        from roost.charter import save_provider_charter
        with pytest.raises(ValueError, match="Unknown provider"):
            save_provider_charter("gpt5", "content")


class TestListFiles:
    """Charter file listing."""

    def test_lists_all_files(self, charter_dir):
        from roost.charter import list_charter_files
        files = list_charter_files()
        names = [f["name"] for f in files]
        assert "charter" in names
        assert "gemini" in names
        assert "claude" in names

    def test_core_flag(self, charter_dir):
        from roost.charter import list_charter_files
        files = list_charter_files()
        core = [f for f in files if f["name"] == "charter"][0]
        assert core["is_core"] is True
        gemini = [f for f in files if f["name"] == "gemini"][0]
        assert gemini["is_core"] is False


class TestEmptyCharter:
    """Behavior when no charter files exist."""

    def test_missing_dir_returns_empty(self):
        import roost.charter
        original_dir = roost.charter._CHARTER_DIR
        roost.charter._CHARTER_DIR = Path("/tmp/nonexistent_charter_dir_12345")
        roost.charter.reload_charter()
        try:
            result = roost.charter.get_charter()
            assert result == ""
        finally:
            roost.charter._CHARTER_DIR = original_dir
            roost.charter.reload_charter()


class TestContextIntegration:
    """Charter integrates correctly with build_agent_context."""

    def test_charter_prepended_to_base_prompt(self, charter_dir, db_conn):
        from roost.context import build_agent_context
        result = build_agent_context("99999", "Base instructions here.", provider="gemini")
        # Charter should come before the base prompt
        charter_pos = result.find("Test Charter")
        base_pos = result.find("Base instructions here")
        assert charter_pos < base_pos
        assert "Gemini Notes" in result

    def test_no_provider_still_includes_core(self, charter_dir, db_conn):
        from roost.context import build_agent_context
        result = build_agent_context("99999", "Base prompt.")
        assert "Test Charter" in result
        assert "Gemini" not in result
