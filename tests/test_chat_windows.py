"""Tests for roost.services.chat_windows (Phase 1.6a foundation).

No mocks: uses the conftest-managed temp SQLite database. Each test
cleans the chat_windows table on entry so ordering assertions are
deterministic.
"""

from __future__ import annotations

import time

import pytest

from roost.database import db_connection
from roost.services import chat_windows as cw


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def clean_chat_windows():
    """Wipe the chat_windows table around each test."""
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows")
        conn.commit()
    yield
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows")
        conn.commit()


# ── Schema ───────────────────────────────────────────────────────────


def test_table_exists():
    with db_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='chat_windows'"
        ).fetchone()
    assert row is not None


def test_unique_per_user_window_name(clean_chat_windows):
    cw.create_window(1, tmux_window_name="w-1", title="One")
    with pytest.raises(cw.ChatWindowError):
        cw.create_window(1, tmux_window_name="w-1", title="Duplicate")
    # Different user, same window name → OK
    other = cw.create_window(2, tmux_window_name="w-1", title="Other user")
    assert other.user_id == 2


# ── Create / get / list ──────────────────────────────────────────────


def test_create_and_get(clean_chat_windows):
    w = cw.create_window(
        1,
        tmux_window_name="lead-42",
        title="Lead 42 — alice@example.com",
        linked_entity_type="lead",
        linked_entity_id=42,
        last_topic="ABSD timeline",
    )
    assert w.id > 0
    assert w.tmux_window_name == "lead-42"
    assert w.linked_entity_type == "lead"
    assert w.linked_entity_id == 42
    assert w.last_topic == "ABSD timeline"
    assert w.last_inbound_at is None
    assert w.created_at  # populated by default

    fetched = cw.get_window(w.id)
    assert fetched is not None
    assert fetched.id == w.id


def test_get_by_tmux_name(clean_chat_windows):
    cw.create_window(1, tmux_window_name="task-7", title="Task 7")
    cw.create_window(2, tmux_window_name="task-7", title="Other user task 7")

    own = cw.get_window_by_tmux_name(1, "task-7")
    assert own is not None and own.title == "Task 7"

    other = cw.get_window_by_tmux_name(2, "task-7")
    assert other is not None and other.title == "Other user task 7"

    miss = cw.get_window_by_tmux_name(1, "nope")
    assert miss is None


def test_create_requires_name_and_title(clean_chat_windows):
    with pytest.raises(cw.ChatWindowError):
        cw.create_window(1, tmux_window_name="", title="x")
    with pytest.raises(cw.ChatWindowError):
        cw.create_window(1, tmux_window_name="w", title="")


def test_list_orders_by_last_active(clean_chat_windows):
    a = cw.create_window(1, tmux_window_name="a", title="A")
    time.sleep(1.05)
    b = cw.create_window(1, tmux_window_name="b", title="B")
    time.sleep(1.05)
    c = cw.create_window(1, tmux_window_name="c", title="C")

    rows = cw.list_windows(1)
    assert [w.tmux_window_name for w in rows] == ["c", "b", "a"]

    # Touch A → moves to the front
    time.sleep(1.05)
    cw.touch_active(a.id)
    rows = cw.list_windows(1)
    assert rows[0].tmux_window_name == "a"


def test_list_is_scoped_per_user(clean_chat_windows):
    cw.create_window(1, tmux_window_name="w-1", title="Mine")
    cw.create_window(2, tmux_window_name="w-1", title="Theirs")
    assert {w.title for w in cw.list_windows(1)} == {"Mine"}
    assert {w.title for w in cw.list_windows(2)} == {"Theirs"}


def test_count_windows(clean_chat_windows):
    assert cw.count_windows(1) == 0
    cw.create_window(1, tmux_window_name="a", title="A")
    cw.create_window(1, tmux_window_name="b", title="B")
    cw.create_window(2, tmux_window_name="a", title="A")
    assert cw.count_windows(1) == 2
    assert cw.count_windows(2) == 1


# ── Mutations: touch / mark_inbound / link / topic / delete ───────────


def test_mark_inbound_sets_both_timestamps(clean_chat_windows):
    w = cw.create_window(1, tmux_window_name="x", title="X")
    assert cw.get_window(w.id).last_inbound_at is None

    cw.mark_inbound(w.id)
    updated = cw.get_window(w.id)
    assert updated.last_inbound_at is not None
    # last_active_at should also have been bumped
    assert updated.last_active_at >= w.last_active_at


def test_link_to_entity(clean_chat_windows):
    w = cw.create_window(1, tmux_window_name="x", title="X")
    cw.link_to_entity(w.id, "task", 7)
    updated = cw.get_window(w.id)
    assert updated.linked_entity_type == "task"
    assert updated.linked_entity_id == 7

    # Clearing
    cw.link_to_entity(w.id, "", None)
    updated = cw.get_window(w.id)
    assert updated.linked_entity_type == ""
    assert updated.linked_entity_id is None


def test_set_topic(clean_chat_windows):
    w = cw.create_window(1, tmux_window_name="x", title="X")
    cw.set_topic(w.id, "Negotiating ABSD remission")
    assert cw.get_window(w.id).last_topic == "Negotiating ABSD remission"


def test_delete_window(clean_chat_windows):
    w = cw.create_window(1, tmux_window_name="x", title="X")
    cw.delete_window(w.id)
    assert cw.get_window(w.id) is None


# ── Cap-and-evict recommendation ─────────────────────────────────────


def test_recommend_evictee_none_under_cap(clean_chat_windows):
    cw.create_window(1, tmux_window_name="a", title="A")
    cw.create_window(1, tmux_window_name="b", title="B")
    # Only 2 windows, cap 5 → no recommendation
    assert cw.recommend_evictee(1, cap=5) is None


def test_recommend_evictee_picks_null_inbound_first(clean_chat_windows):
    # 5 windows: one with inbound, four without. Coldest = no-inbound.
    hot = cw.create_window(1, tmux_window_name="hot", title="Hot")
    cw.mark_inbound(hot.id)

    cw.create_window(1, tmux_window_name="cold-1", title="Cold 1")
    time.sleep(1.05)
    cw.create_window(1, tmux_window_name="cold-2", title="Cold 2")
    time.sleep(1.05)
    cw.create_window(1, tmux_window_name="cold-3", title="Cold 3")
    time.sleep(1.05)
    oldest_cold = cw.create_window(1, tmux_window_name="cold-4", title="Cold 4")
    # cold-1 was created first → oldest last_active_at among no-inbound
    rec = cw.recommend_evictee(1, cap=5)
    assert rec is not None
    assert rec.tmux_window_name == "cold-1"
    assert oldest_cold.id != rec.id


def test_recommend_evictee_picks_oldest_inbound_when_all_have_inbound(clean_chat_windows):
    # 5 windows, all with inbound → oldest inbound wins.
    ids = []
    for name in ("a", "b", "c", "d", "e"):
        w = cw.create_window(1, tmux_window_name=name, title=name.upper())
        cw.mark_inbound(w.id)
        ids.append((name, w.id))
        time.sleep(1.05)

    rec = cw.recommend_evictee(1, cap=5)
    assert rec is not None
    # 'a' got inbound first → oldest last_inbound_at
    assert rec.tmux_window_name == "a"


def test_recommend_evictee_respects_user_scope(clean_chat_windows):
    # User 1 over cap, user 2 empty → recommendation only for user 1.
    for name in ("a", "b", "c", "d", "e", "f"):
        cw.create_window(1, tmux_window_name=name, title=name)
    assert cw.recommend_evictee(1, cap=5) is not None
    assert cw.recommend_evictee(2, cap=5) is None


def test_default_cap_constant():
    assert cw.DEFAULT_WINDOW_CAP == 5
