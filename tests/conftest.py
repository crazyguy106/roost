"""Shared test fixtures for Roost.

Key challenge: roost.database calls init_db() on import, which uses
DATABASE_PATH from roost.config.  We must patch the path BEFORE any
roost module is imported.

Strategy: use a session-scoped autouse fixture that patches DATABASE_PATH
to a temp file, then triggers a fresh init_db().  Per-test fixtures get
a clean connection to the same DB.
"""

import os
import sqlite3
import tempfile

import pytest


# ── Session-level: create a temp DB file used by all tests ──────────

@pytest.fixture(scope="session", autouse=True)
def _patch_database_path():
    """Redirect Roost's database to a temp file for the test session.

    Must run before any roost imports happen.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_path = tmp.name
    tmp.close()

    # Patch the env var so roost.config picks it up
    os.environ["DATABASE_PATH"] = tmp_path

    # Also suppress Telegram token warnings etc.
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:fake")
    os.environ.setdefault("TELEGRAM_ALLOWED_USERS", "12345")
    os.environ.setdefault("AGENT_ENABLED", "false")

    # Now it's safe to import roost — init_db() will use the temp file
    import roost.database
    # Force re-init in case the module was already imported somehow
    roost.database.DATABASE_PATH = tmp_path
    roost.database.init_db()

    # Seed a test user (FK target for user_settings, notes, etc.)
    conn = roost.database.get_connection()
    conn.execute(
        "INSERT OR IGNORE INTO users (id, name, telegram_id, role) VALUES (1, 'Test Admin', 1, 'owner')"
    )
    conn.execute(
        "INSERT OR IGNORE INTO users (id, name, telegram_id, role) VALUES (2, 'Test User 2', 2, 'member')"
    )
    conn.commit()
    conn.close()

    yield tmp_path

    # Cleanup
    try:
        os.unlink(tmp_path)
    except OSError:
        pass


# ── Per-test: fresh connection + cleanup ────────────────────────────

@pytest.fixture
def db_conn(_patch_database_path):
    """Yield a fresh SQLite connection to the test database.

    Uses autocommit (isolation_level=None) so reads don't hold implicit
    transactions that block writes from service-layer connections.

    Cleans up CAGE-related tables between tests so they don't leak state.
    """
    conn = sqlite3.connect(_patch_database_path, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    yield conn

    # Clean up test data (CAGE tables + notes used in context tests)
    for table in ("chat_history", "user_preferences", "user_settings"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    conn.close()


@pytest.fixture
def db_cleanup(_patch_database_path):
    """Cleanup fixture for tests that use service-layer functions directly.

    Unlike db_conn, this does NOT hold a connection open during the test,
    avoiding 'database is locked' errors when services open their own connections.
    """
    yield

    # Clean up after test
    from roost.database import get_connection
    conn = get_connection()
    for table in ("user_settings", "user_preferences"):
        try:
            conn.execute(f"DELETE FROM {table}")
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()


@pytest.fixture
def user_id():
    """A consistent fake user ID for tests."""
    return "99999"
