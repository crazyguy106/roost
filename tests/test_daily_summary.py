"""Tests for the end-of-day summary builder + bot handlers.

Builder tests seed minimal rows in the relevant tables and assert the
counts. Handler tests use the same SimpleNamespace + AsyncMock pattern as
test_bot_nurture_approval.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.database import get_connection
from roost.services.daily_summary import build_summary, format_summary


@pytest.fixture(autouse=True)
def _disable_ai_narrative(monkeypatch):
    """Force the AI narrative off for builder/formatter tests so output is
    deterministic. Tests that exercise AI behaviour patch it explicitly."""
    monkeypatch.setattr(
        "roost.services.daily_summary._ai_narrative", lambda s: None,
    )


# ── Fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def clean_summary_tables():
    """Wipe rows that build_summary reads, restore on teardown."""
    tables = (
        "nurture_enrollments",
        "tasks",
        "rpa_runs",
        "automation_runs",
    )
    conn = get_connection()
    try:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    yield
    conn = get_connection()
    try:
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _yesterday_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
        "%Y-%m-%d %H:%M:%S",
    )


# ── Builder ────────────────────────────────────────────────────────────


def test_build_summary_empty(clean_summary_tables):
    s = build_summary(tz_name="Asia/Singapore")
    assert s["nurture"]["advanced_by_status"] == {}
    assert s["nurture"]["pending_approvals"] == []
    assert s["tasks"]["completed"] == 0
    assert s["inbound_leads"]["total_new"] == 0
    assert s["recipes"]["by_status"] == {}
    assert s["rpa"]["by_status"] == {}


def test_build_summary_counts_today_only(clean_summary_tables):
    """Rows older than the window must be excluded."""
    conn = get_connection()
    today = _now_str()
    long_ago = _yesterday_str()

    # Two tasks done today, one done two days ago.
    conn.execute(
        "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'done', ?)",
        ("today A", today),
    )
    conn.execute(
        "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'done', ?)",
        ("today B", today),
    )
    conn.execute(
        "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'done', ?)",
        ("ancient", long_ago),
    )
    # One open task still, with a past deadline (overdue).
    conn.execute(
        "INSERT INTO tasks (title, status, deadline) "
        "VALUES (?, 'todo', '2020-01-01')",
        ("stale",),
    )
    conn.commit()
    conn.close()

    s = build_summary(tz_name="Asia/Singapore")
    assert s["tasks"]["completed"] == 2
    assert s["tasks"]["open"] == 1
    assert s["tasks"]["overdue"] == 1


def test_build_summary_nurture_pending_approvals(clean_summary_tables):
    conn = get_connection()
    conn.execute(
        "INSERT INTO nurture_cadences "
        "(slug, name, vertical, steps_json, source, user_id) "
        "VALUES (?, ?, 'generic', '[]', 'library', '')",
        ("test_cad", "Test Cadence"),
    )
    cad_id = conn.execute(
        "SELECT id FROM nurture_cadences WHERE slug = ?", ("test_cad",)
    ).fetchone()["id"]
    conn.execute(
        "INSERT INTO nurture_enrollments "
        "(cadence_id, cadence_slug, contact_name, status, "
        " current_step, pause_reason, last_step_at, created_at, source) "
        "VALUES (?, ?, ?, 'paused', 0, ?, ?, ?, 'whatsapp')",
        (cad_id, "test_cad", "Alice", "awaiting_approval:0",
         _now_str(), _now_str()),
    )
    conn.commit()
    conn.close()

    s = build_summary(tz_name="Asia/Singapore")
    assert len(s["nurture"]["pending_approvals"]) == 1
    p = s["nurture"]["pending_approvals"][0]
    assert p["contact"] == "Alice"
    assert p["cadence"] == "test_cad"
    assert p["step"] == 1
    # New today, source=whatsapp
    assert s["inbound_leads"]["total_new"] == 1
    assert s["inbound_leads"]["by_source"] == {"whatsapp": 1}


def test_build_summary_rpa_failures(clean_summary_tables):
    conn = get_connection()
    conn.execute(
        "INSERT INTO rpa_runs (portal_slug, status, error, updated_at) "
        "VALUES (?, 'failed', ?, ?)",
        ("aia", "Singpass timeout after 90s", _now_str()),
    )
    conn.execute(
        "INSERT INTO rpa_runs (portal_slug, status, updated_at) "
        "VALUES (?, 'completed', ?)",
        ("hdb", _now_str()),
    )
    conn.commit()
    conn.close()

    s = build_summary(tz_name="Asia/Singapore")
    assert s["rpa"]["by_status"]["failed"] == 1
    assert s["rpa"]["by_status"]["completed"] == 1
    assert s["rpa"]["succeeded_by_portal"] == {"hdb": 1}
    assert len(s["rpa"]["failures"]) == 1
    assert s["rpa"]["failures"][0]["portal"] == "aia"
    assert "Singpass" in s["rpa"]["failures"][0]["error"]


def test_format_summary_no_activity(clean_summary_tables):
    s = build_summary(tz_name="UTC")
    text = format_summary(s)
    assert "Daily summary" in text
    assert "No activity" in text


def test_format_summary_renders_sections(clean_summary_tables):
    conn = get_connection()
    today = _now_str()
    conn.execute(
        "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'done', ?)",
        ("did a thing", today),
    )
    conn.execute(
        "INSERT INTO rpa_runs (portal_slug, status, updated_at) "
        "VALUES (?, 'completed', ?)", ("hdb", today),
    )
    conn.commit()
    conn.close()
    text = format_summary(build_summary(tz_name="Asia/Singapore"))
    assert "*Tasks*" in text
    assert "1 completed today" in text
    assert "*RPA*" in text


# ── Chatwoot section (FA-H) ───────────────────────────────────────────


def test_build_summary_includes_chatwoot_section_when_enabled(
    clean_summary_tables, monkeypatch,
):
    """When CHATWOOT_ENABLED is true, the morning brief picks up open/pending
    counts and the top-of-inbox preview, and the formatter renders them."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.conversation_meta",
        lambda assignee_type="me": {
            "ok": True, "open": 3, "resolved": 5, "pending": 1, "all_count": 9,
        },
    )
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.list_open_conversations",
        lambda limit=5: {
            "ok": True,
            "conversations": [
                {"id": 101, "contact": "Tan Mei", "preview": "Thursday 2pm works"},
                {"id": 102, "contact": "+6598887777", "preview": ""},
            ],
        },
    )

    s = build_summary(tz_name="Asia/Singapore")
    cw = s["chatwoot"]
    assert cw["enabled"] is True
    assert cw["open"] == 3
    assert cw["pending"] == 1
    assert len(cw["top_open"]) == 2
    assert cw["top_open"][0]["contact"] == "Tan Mei"

    text = format_summary(s)
    assert "*Chatwoot*" in text
    assert "3 open" in text
    assert "1 pending" in text
    assert "#101 Tan Mei" in text
    assert "Thursday 2pm works" in text
    # No-preview row still renders with the contact.
    assert "#102 +6598887777" in text


def test_build_summary_chatwoot_disabled_omits_section(clean_summary_tables, monkeypatch):
    """Flag off → the section reports enabled=False and the formatter skips it."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", False)
    s = build_summary(tz_name="UTC")
    assert s["chatwoot"] == {"enabled": False}
    text = format_summary(s)
    assert "*Chatwoot*" not in text


def test_build_summary_chatwoot_unreachable_renders_warning(
    clean_summary_tables, monkeypatch,
):
    """When Chatwoot is configured but unreachable, the brief degrades to a
    one-line warning instead of crashing the morning send."""
    monkeypatch.setattr("roost.config.CHATWOOT_ENABLED", True)
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.chatwoot.conversation_meta",
        lambda assignee_type="me": {"error": "Chatwoot API 502"},
    )
    s = build_summary(tz_name="UTC")
    assert s["chatwoot"] == {"enabled": True, "error": "Chatwoot API 502"}
    text = format_summary(s)
    assert "*Chatwoot*" in text
    assert "inbox unreachable" in text
    assert "502" in text


# ── AI narrative ──────────────────────────────────────────────────────


def test_ai_narrative_skips_empty_day(clean_summary_tables, monkeypatch):
    """No activity → no Gemini call, narrative is None."""
    monkeypatch.setattr("roost.config.AI_SUMMARY_ENABLED", True)
    monkeypatch.setattr("roost.config.GEMINI_API_KEY", "test-key")
    from roost.services.daily_summary import _ai_narrative
    s = build_summary(tz_name="UTC")
    assert _ai_narrative(s) is None


def test_ai_narrative_disabled_when_no_key(clean_summary_tables, monkeypatch):
    monkeypatch.setattr("roost.config.AI_SUMMARY_ENABLED", True)
    monkeypatch.setattr("roost.config.GEMINI_API_KEY", "")
    conn = get_connection()
    conn.execute(
        "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'done', ?)",
        ("did one", _now_str()),
    )
    conn.commit()
    conn.close()
    from roost.services.daily_summary import _ai_narrative
    s = build_summary(tz_name="UTC")
    assert _ai_narrative(s) is None


def test_format_summary_prepends_narrative(clean_summary_tables, monkeypatch):
    """When _ai_narrative returns text, format_summary prepends it."""
    conn = get_connection()
    conn.execute(
        "INSERT INTO tasks (title, status, updated_at) VALUES (?, 'done', ?)",
        ("did one", _now_str()),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(
        "roost.services.daily_summary._ai_narrative",
        lambda s: "• Shipped 1 task\n• No items need attention",
    )
    text = format_summary(build_summary(tz_name="Asia/Singapore"))
    assert "• Shipped 1 task" in text
    # Narrative appears before the deterministic Tasks section.
    assert text.index("• Shipped 1 task") < text.index("*Tasks*")


# ── Handlers ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def allow_user(monkeypatch):
    monkeypatch.setattr(
        "roost.bot.security.TELEGRAM_ALLOWED_USERS", {42},
    )


def _make_update():
    msg = SimpleNamespace(reply_text=AsyncMock(), text="/cmd")
    user = SimpleNamespace(id=42, first_name="Test")
    return SimpleNamespace(
        message=msg, effective_user=user, callback_query=None,
    )


@pytest.mark.asyncio
async def test_cmd_summary_renders(clean_summary_tables):
    from roost.bot.handlers.daily_summary import cmd_summary
    upd = _make_update()
    await cmd_summary(upd, SimpleNamespace(args=[]))
    out = upd.message.reply_text.await_args.args[0]
    assert "Daily summary" in out


@pytest.mark.asyncio
async def test_cmd_summarytime_validates_format():
    from roost.bot.handlers.daily_summary import cmd_summarytime
    upd = _make_update()
    await cmd_summarytime(upd, SimpleNamespace(args=["25:99"]))
    out = upd.message.reply_text.await_args.args[0]
    assert "Bad time" in out


@pytest.mark.asyncio
async def test_cmd_summarytime_validates_tz():
    from roost.bot.handlers.daily_summary import cmd_summarytime
    upd = _make_update()
    await cmd_summarytime(upd, SimpleNamespace(args=["18:00", "Atlantis/Lost"]))
    out = upd.message.reply_text.await_args.args[0]
    assert "Unknown timezone" in out


@pytest.mark.asyncio
async def test_cmd_summarytime_persists(monkeypatch):
    """Use an in-memory dict instead of the real DB to avoid lock contention
    with a concurrently-running bot."""
    store: dict = {}

    def fake_set(key, value, user_id=None):
        store[(user_id, key)] = value

    def fake_get(key, user_id=None):
        return store.get((user_id, key))

    def fake_del(key, user_id=None):
        store.pop((user_id, key), None)

    monkeypatch.setattr("roost.services.settings.set_setting", fake_set)
    monkeypatch.setattr("roost.services.settings.get_setting", fake_get)
    monkeypatch.setattr("roost.services.settings.delete_setting", fake_del)

    from roost.bot.handlers.daily_summary import cmd_summarytime
    upd = _make_update()
    await cmd_summarytime(
        upd, SimpleNamespace(args=["18:30", "Asia/Singapore"]),
    )
    assert store[(42, "daily_summary_time")] == "18:30"
    assert store[(42, "daily_summary_tz")] == "Asia/Singapore"


@pytest.mark.asyncio
async def test_cmd_summaryoff_clears_setting(monkeypatch):
    store: dict = {(42, "daily_summary_time"): "09:00"}

    def fake_get(key, user_id=None):
        return store.get((user_id, key))

    def fake_del(key, user_id=None):
        store.pop((user_id, key), None)

    monkeypatch.setattr("roost.services.settings.delete_setting", fake_del)
    from roost.bot.handlers.daily_summary import cmd_summaryoff
    upd = _make_update()
    await cmd_summaryoff(upd, SimpleNamespace(args=[]))
    assert (42, "daily_summary_time") not in store


@pytest.mark.asyncio
async def test_cmd_summarystatus_disabled_default(monkeypatch):
    monkeypatch.setattr(
        "roost.services.settings.get_setting",
        lambda key, user_id=None: None,
    )
    from roost.bot.handlers.daily_summary import cmd_summarystatus
    upd = _make_update()
    await cmd_summarystatus(upd, SimpleNamespace(args=[]))
    out = upd.message.reply_text.await_args.args[0]
    assert "disabled" in out
