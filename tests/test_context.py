"""Tests for roost.context — CAGE framework: preferences, chat history, context assembly."""

import pytest


# ── Preferences (Align layer) ──────────────────────────────────────


class TestPreferences:
    """User preference CRUD via context.py functions."""

    def test_set_and_get(self, db_conn, user_id):
        from roost.context import set_preference, get_preferences

        set_preference(user_id, "tone", "concise")
        prefs = get_preferences(user_id)
        assert prefs["tone"] == "concise"

    def test_upsert_overwrites(self, db_conn, user_id):
        from roost.context import set_preference, get_preferences

        set_preference(user_id, "tone", "verbose")
        set_preference(user_id, "tone", "brief")
        prefs = get_preferences(user_id)
        assert prefs["tone"] == "brief"

    def test_multiple_keys(self, db_conn, user_id):
        from roost.context import set_preference, get_preferences

        set_preference(user_id, "tone", "concise")
        set_preference(user_id, "language", "en")
        set_preference(user_id, "timezone", "Asia/Singapore")
        prefs = get_preferences(user_id)
        assert len(prefs) == 3
        assert prefs["language"] == "en"

    def test_empty_for_unknown_user(self, db_conn):
        from roost.context import get_preferences

        prefs = get_preferences("nonexistent")
        assert prefs == {}

    def test_delete_existing(self, db_conn, user_id):
        from roost.context import set_preference, delete_preference, get_preferences

        set_preference(user_id, "tone", "concise")
        result = delete_preference(user_id, "tone")
        assert result is True
        assert get_preferences(user_id) == {}

    def test_delete_nonexistent(self, db_conn, user_id):
        from roost.context import delete_preference

        result = delete_preference(user_id, "nonexistent_key")
        assert result is False

    def test_user_isolation(self, db_conn):
        """Preferences for user A should not leak to user B."""
        from roost.context import set_preference, get_preferences

        set_preference("user_a", "tone", "formal")
        set_preference("user_b", "tone", "casual")

        assert get_preferences("user_a")["tone"] == "formal"
        assert get_preferences("user_b")["tone"] == "casual"


# ── Chat History ───────────────────────────────────────────────────


class TestChatHistory:
    """Chat history persistence and retrieval."""

    def test_save_and_load(self, db_conn, user_id):
        from roost.context import save_chat_history, load_chat_history

        save_chat_history("sess1", "user", "hello", user_id)
        save_chat_history("sess1", "assistant", "hi there", user_id)

        history = load_chat_history("sess1")
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "hello"
        assert history[1]["role"] == "assistant"
        assert history[1]["content"] == "hi there"

    def test_chronological_order(self, db_conn, user_id):
        from roost.context import save_chat_history, load_chat_history

        for i in range(5):
            save_chat_history("sess2", "user", f"msg-{i}", user_id)

        history = load_chat_history("sess2")
        contents = [h["content"] for h in history]
        assert contents == ["msg-0", "msg-1", "msg-2", "msg-3", "msg-4"]

    def test_limit(self, db_conn, user_id):
        from roost.context import save_chat_history, load_chat_history

        for i in range(20):
            save_chat_history("sess3", "user", f"msg-{i}", user_id)

        history = load_chat_history("sess3", limit=5)
        assert len(history) == 5
        # Should be the 5 most recent, in chronological order
        contents = [h["content"] for h in history]
        assert contents == ["msg-15", "msg-16", "msg-17", "msg-18", "msg-19"]

    def test_session_isolation(self, db_conn, user_id):
        from roost.context import save_chat_history, load_chat_history

        save_chat_history("sess_a", "user", "for session A", user_id)
        save_chat_history("sess_b", "user", "for session B", user_id)

        hist_a = load_chat_history("sess_a")
        hist_b = load_chat_history("sess_b")
        assert len(hist_a) == 1
        assert len(hist_b) == 1
        assert hist_a[0]["content"] == "for session A"

    def test_content_truncation(self, db_conn, user_id):
        """Content longer than 10000 chars should be truncated on save."""
        from roost.context import save_chat_history, load_chat_history

        long_msg = "x" * 15000
        save_chat_history("sess_trunc", "user", long_msg, user_id)

        history = load_chat_history("sess_trunc")
        assert len(history[0]["content"]) == 10000

    def test_prune(self, db_conn, user_id):
        from roost.context import save_chat_history, prune_chat_history, load_chat_history

        # Insert a message, then backdate it
        save_chat_history("sess_old", "user", "ancient message", user_id)
        db_conn.execute(
            "UPDATE chat_history SET created_at = datetime('now', '-30 days') "
            "WHERE session_id = 'sess_old'"
        )
        db_conn.commit()

        # Insert a recent one
        save_chat_history("sess_new", "user", "recent message", user_id)

        deleted = prune_chat_history(days=7)
        assert deleted >= 1

        assert load_chat_history("sess_old") == []
        assert len(load_chat_history("sess_new")) == 1

    def test_empty_session(self, db_conn):
        from roost.context import load_chat_history

        history = load_chat_history("nonexistent_session")
        assert history == []


# ── Context Assembly (build_agent_context) ──────────────────────────


class TestBuildAgentContext:
    """The CAGE context builder injects dynamic context into the system prompt."""

    BASE_PROMPT = "You are a helpful assistant."

    def test_no_context_returns_base(self, db_conn, user_id):
        """With no data in DB, base prompt should be present in result."""
        from roost.context import build_agent_context

        result = build_agent_context(user_id, self.BASE_PROMPT)
        # Base prompt should be present (charter may be prepended)
        assert self.BASE_PROMPT in result

    def test_preferences_injected(self, db_conn, user_id):
        from roost.context import set_preference, build_agent_context

        set_preference(user_id, "tone", "concise")
        set_preference(user_id, "language", "en")

        result = build_agent_context(user_id, self.BASE_PROMPT)
        assert "User Preferences" in result
        assert "tone: concise" in result
        assert "language: en" in result
        # Base prompt still present
        assert self.BASE_PROMPT in result

    def test_notes_injected(self, db_conn, user_id):
        """Recent notes should appear in the context."""
        from roost.context import build_agent_context

        # notes.user_id is FK to users(id) — insert a user first
        db_conn.execute(
            "INSERT OR IGNORE INTO users (id, name, telegram_id) VALUES (?, ?, ?)",
            (int(user_id), "Test User", int(user_id)),
        )
        db_conn.execute(
            "INSERT INTO notes (content, user_id) VALUES (?, ?)",
            ("Buy milk and eggs", int(user_id)),
        )
        db_conn.commit()

        result = build_agent_context(user_id, self.BASE_PROMPT)
        assert "Buy milk" in result

    def test_token_budget_truncation(self, db_conn, user_id):
        """Context should be truncated if it exceeds MAX_CONTEXT_CHARS."""
        from roost.context import set_preference, build_agent_context, MAX_CONTEXT_CHARS

        # Create enough preferences to exceed the budget
        for i in range(100):
            set_preference(user_id, f"pref_{i:03d}", "x" * 50)

        result = build_agent_context(user_id, self.BASE_PROMPT)
        # The injected context portion (after the separator) should be bounded
        parts = result.split("---\n\n")
        if len(parts) > 1:
            context_part = parts[1]
            assert len(context_part) <= MAX_CONTEXT_CHARS + 30  # +30 for truncation marker

    def test_separator_format(self, db_conn, user_id):
        """Context should be separated from base prompt by ---."""
        from roost.context import set_preference, build_agent_context

        set_preference(user_id, "tone", "concise")
        result = build_agent_context(user_id, self.BASE_PROMPT)
        assert "\n\n---\n\n" in result


# ── Rate Limiting (from executor.py) ───────────────────────────────


class TestRateLimiting:
    """Rate limiter for AI commands."""

    def test_first_call_passes(self):
        from roost.bot.executor import check_rate_limit, _rate_limits
        _rate_limits.clear()
        # Should not raise
        check_rate_limit(11111)

    def test_rapid_second_call_blocked(self):
        from roost.bot.executor import check_rate_limit, RateLimitError, _rate_limits
        _rate_limits.clear()
        check_rate_limit(22222)
        with pytest.raises(RateLimitError):
            check_rate_limit(22222)

    def test_different_users_independent(self):
        from roost.bot.executor import check_rate_limit, _rate_limits
        _rate_limits.clear()
        check_rate_limit(33333)
        # Different user should pass immediately
        check_rate_limit(44444)


class TestTruncateOutput:
    """Output truncation utility."""

    def test_short_text_unchanged(self):
        from roost.bot.executor import _truncate_output
        assert _truncate_output("hello", limit=100) == "hello"

    def test_long_text_truncated(self):
        from roost.bot.executor import _truncate_output
        long = "x" * 5000
        result = _truncate_output(long, limit=100)
        assert len(result) <= 100
        assert "[truncated]" in result

    def test_keeps_tail(self):
        """Truncation should keep the end of the output (most recent)."""
        from roost.bot.executor import _truncate_output
        text = "START" + "x" * 5000 + "END"
        result = _truncate_output(text, limit=200)
        assert result.endswith("END")
        assert "START" not in result
