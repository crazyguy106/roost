"""Unit tests for the web tty sweeper (idle + memory pressure).

The sweeper is built so the tmux killer + cgroup pressure reader are
injectable, which is the whole point: no docker / no /sys access needed
in CI.
"""

from __future__ import annotations

import pytest

from roost.database import db_connection
from roost.services import chat_windows as cw
from roost.services import tty_sweeper


@pytest.fixture(autouse=True)
def clean_chat_windows():
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows")
        conn.commit()
    yield
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows")
        conn.commit()


# ── Idle sweep ───────────────────────────────────────────────────────


def _make_idle(window_id: int, minutes_ago: int) -> None:
    """Backdate last_active_at to make a window look idle."""
    with db_connection() as conn:
        conn.execute(
            "UPDATE chat_windows SET last_active_at = datetime('now', ?) WHERE id = ?",
            (f"-{minutes_ago} minutes", window_id),
        )
        conn.commit()


def test_idle_sweep_kills_old_windows():
    fresh = cw.auto_create(1, title="fresh")
    stale = cw.auto_create(1, title="stale")
    _make_idle(stale.id, minutes_ago=400)

    killed = []
    def fake_kill(uid, name):
        killed.append((uid, name))

    swept = tty_sweeper.sweep_idle(idle_minutes=360, kill_tmux=fake_kill)
    assert swept == 1
    assert killed == [(1, stale.tmux_window_name)]

    refreshed = cw.get_window(stale.id)
    assert refreshed.tmux_window_alive == 0
    assert refreshed.last_resume_cmd == "bash"

    fresh_after = cw.get_window(fresh.id)
    assert fresh_after.tmux_window_alive == 1


def test_idle_sweep_skips_already_dead_rows():
    w = cw.auto_create(1, title="zombie")
    cw.mark_window_killed(w.id, resume_cmd="bash")
    _make_idle(w.id, minutes_ago=999)

    killed = []
    swept = tty_sweeper.sweep_idle(idle_minutes=360, kill_tmux=killed.append)
    assert swept == 0
    assert killed == []


def test_idle_sweep_continues_on_killer_error():
    """A single tmux failure must not skip the rest of the rows."""
    a = cw.auto_create(1, title="A")
    b = cw.auto_create(2, title="B")
    _make_idle(a.id, minutes_ago=400)
    _make_idle(b.id, minutes_ago=400)

    calls = []
    def kill_one_bad(uid, name):
        calls.append((uid, name))
        if uid == 1:
            raise RuntimeError("tmux unavailable")

    swept = tty_sweeper.sweep_idle(idle_minutes=360, kill_tmux=kill_one_bad)
    # B still got swept; A's mark-killed was skipped because the killer
    # threw before the UPDATE. The contract is: best-effort, log the
    # failure, keep going.
    assert swept == 1
    assert len(calls) == 2


# ── Memory pressure sweep ────────────────────────────────────────────


def test_memory_sweep_noop_below_threshold():
    cw.auto_create(1, title="A")
    swept = tty_sweeper.sweep_memory_pressure(
        ratio_threshold=0.85,
        pressure_reader=lambda: 0.5,
    )
    assert swept == 0


def test_memory_sweep_evicts_coldest_first():
    # Three live rows, none with inbound; oldest active is the coldest.
    a = cw.auto_create(1, title="A")
    b = cw.auto_create(1, title="B")
    c = cw.auto_create(1, title="C")
    _make_idle(a.id, minutes_ago=120)  # oldest
    _make_idle(b.id, minutes_ago=60)
    _make_idle(c.id, minutes_ago=30)

    # Pressure stays above threshold the entire time → up to budget kills.
    killed = []
    swept = tty_sweeper.sweep_memory_pressure(
        ratio_threshold=0.85,
        pressure_reader=lambda: 0.95,
        budget=2,
        kill_tmux=lambda u, n: killed.append(n),
    )
    assert swept == 2
    assert killed[0] == a.tmux_window_name
    assert killed[1] == b.tmux_window_name


def test_memory_sweep_stops_when_pressure_drops():
    a = cw.auto_create(1, title="A")
    b = cw.auto_create(1, title="B")
    _make_idle(a.id, minutes_ago=120)
    _make_idle(b.id, minutes_ago=60)

    readings = iter([0.95, 0.50, 0.50])
    killed = []
    swept = tty_sweeper.sweep_memory_pressure(
        ratio_threshold=0.85,
        pressure_reader=lambda: next(readings),
        budget=5,
        kill_tmux=lambda u, n: killed.append(n),
    )
    assert swept == 1
    assert killed == [a.tmux_window_name]


# ── tick() integration ──────────────────────────────────────────────


def test_tick_returns_counts():
    a = cw.auto_create(1, title="idle")
    _make_idle(a.id, minutes_ago=400)
    result = tty_sweeper.tick(
        idle_minutes=360,
        ratio_threshold=0.85,
        kill_tmux=lambda u, n: None,
    )
    assert result["idle_killed"] == 1
    assert result["pressure_killed"] == 0
