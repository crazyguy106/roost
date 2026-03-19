"""Tests for roost.database — schema creation, migrations, connections."""

import sqlite3

import pytest


class TestGetConnection:
    """Connection factory returns a usable, configured connection."""

    def test_returns_connection(self, db_conn):
        assert isinstance(db_conn, sqlite3.Connection)

    def test_row_factory_is_row(self, db_conn):
        """Row factory should be sqlite3.Row for dict-like access."""
        assert db_conn.row_factory is sqlite3.Row

    def test_wal_mode(self, db_conn):
        mode = db_conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_foreign_keys_enabled(self, db_conn):
        fk = db_conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk == 1


class TestSchemaCreation:
    """init_db() creates all expected tables."""

    # Core tables that must always exist (not exhaustive — just the critical ones)
    EXPECTED_TABLES = [
        # Core (SCHEMA)
        "projects", "tasks", "notes", "command_log",
        # Auth & users
        "users", "oauth_tokens",
        # CRM
        "contacts", "entities", "contact_entities", "contact_identifiers",
        # CAGE framework (V19)
        "chat_history", "user_preferences",
    ]

    def test_all_tables_exist(self, db_conn):
        rows = db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        actual = {row["name"] for row in rows}

        for table in self.EXPECTED_TABLES:
            assert table in actual, f"Missing table: {table}"

    def test_chat_history_columns(self, db_conn):
        cols = [
            info[1]
            for info in db_conn.execute("PRAGMA table_info(chat_history)").fetchall()
        ]
        assert "session_id" in cols
        assert "user_id" in cols
        assert "role" in cols
        assert "content" in cols
        assert "created_at" in cols

    def test_user_preferences_columns(self, db_conn):
        cols = [
            info[1]
            for info in db_conn.execute("PRAGMA table_info(user_preferences)").fetchall()
        ]
        assert "user_id" in cols
        assert "key" in cols
        assert "value" in cols
        assert "updated_at" in cols

    def test_user_preferences_unique_constraint(self, db_conn):
        """user_id + key should be unique."""
        db_conn.execute(
            "INSERT INTO user_preferences (user_id, key, value) VALUES ('u1', 'tone', 'brief')"
        )
        db_conn.commit()

        with pytest.raises(sqlite3.IntegrityError):
            db_conn.execute(
                "INSERT INTO user_preferences (user_id, key, value) VALUES ('u1', 'tone', 'verbose')"
            )


class TestSchemaIdempotent:
    """Running init_db() twice should not fail."""

    def test_double_init(self, _patch_database_path):
        from roost.database import init_db
        # Should not raise
        init_db()
        init_db()
