"""Web tty idle + memory-pressure sweeper.

Two policies, both invoked from `roost.bot.scheduler` on a ~5-minute tick:

1. **Idle sweep** — any window whose `chat_windows.last_active_at` is older
   than `TTY_IDLE_TTL_MINUTES` (default 360 = 6h) gets its tmux window
   killed, the row marked `tmux_window_alive=0`, and a resume hint stored
   in `last_resume_cmd`. The row stays so the operator sees it in the
   "paused" drawer and can resume.

2. **Memory-pressure sweep** — when cgroup v2 reports
   `memory.current / memory.max >= TTY_MEMORY_PRESSURE_RATIO` (default
   0.85), we evict the coldest live windows globally (same ordering as
   the per-user picker recommendation) until back under the ratio or
   nothing more is left to evict.

Both policies are no-ops in environments without tmux / cgroup v2 (tests,
fresh dev boxes). The sweeper is built to swallow every exception
because a single bad row should not silence the whole tick.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from roost.services import chat_windows as cw

logger = logging.getLogger(__name__)

# How many coldest windows the memory sweep is allowed to kill per tick.
# Bounded so a transient spike can't drain every window at once.
_MEMORY_SWEEP_BUDGET = 5

# cgroup v2 paths (rootless containers usually expose these directly).
_CGROUP_CURRENT = Path("/sys/fs/cgroup/memory.current")
_CGROUP_MAX = Path("/sys/fs/cgroup/memory.max")


# A tmux killer is `(user_id, tmux_window_name) -> None`. Injected from
# api_tty so the sweeper doesn't have to know how tmux is invoked.
TmuxKiller = Callable[[int, str], None]


def _default_resume_cmd(window: cw.ChatWindow) -> str:
    """Hint stored on idle-kill so the resume drawer can show something
    actionable. Plain `bash` is a safe default — the operator can run
    `claude --resume` themselves once the tmux window is back."""
    return "bash"


def sweep_idle(
    idle_minutes: int,
    *,
    kill_tmux: Optional[TmuxKiller] = None,
    resume_cmd_for: Callable[[cw.ChatWindow], str] = _default_resume_cmd,
) -> int:
    """Kill tmux windows that have been idle longer than `idle_minutes`.

    Returns the number of rows marked paused.
    """
    swept = 0
    try:
        rows = cw.list_idle_for_sweep(idle_minutes)
    except Exception:  # noqa: BLE001
        logger.exception("tty_sweeper: list_idle_for_sweep failed")
        return 0
    for window in rows:
        try:
            if kill_tmux is not None:
                kill_tmux(window.user_id, window.tmux_window_name)
            cw.mark_window_killed(window.id, resume_cmd_for(window))
            swept += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "tty_sweeper: failed to kill window id=%s name=%s",
                window.id, window.tmux_window_name,
            )
    if swept:
        logger.info("tty_sweeper: idle-killed %d window(s)", swept)
    return swept


def read_memory_pressure() -> Optional[float]:
    """Return current/max as a float in [0, 1] or None if not measurable."""
    try:
        current_raw = _CGROUP_CURRENT.read_text().strip()
        max_raw = _CGROUP_MAX.read_text().strip()
    except OSError:
        return None
    if max_raw == "max":
        return 0.0
    try:
        current = float(current_raw)
        maximum = float(max_raw)
    except ValueError:
        return None
    if maximum <= 0:
        return None
    return current / maximum


def sweep_memory_pressure(
    ratio_threshold: float,
    *,
    kill_tmux: Optional[TmuxKiller] = None,
    pressure_reader: Callable[[], Optional[float]] = read_memory_pressure,
    budget: int = _MEMORY_SWEEP_BUDGET,
) -> int:
    """Evict coldest live windows globally while above the ratio.

    Returns the number of windows killed.
    """
    pressure = pressure_reader()
    if pressure is None or pressure < ratio_threshold:
        return 0
    try:
        candidates = cw.list_coldest_alive(budget)
    except Exception:  # noqa: BLE001
        logger.exception("tty_sweeper: list_coldest_alive failed")
        return 0
    killed = 0
    for window in candidates:
        if killed >= budget:
            break
        try:
            if kill_tmux is not None:
                kill_tmux(window.user_id, window.tmux_window_name)
            cw.mark_window_killed(window.id, _default_resume_cmd(window))
            killed += 1
        except Exception:  # noqa: BLE001
            logger.exception(
                "tty_sweeper: failed to memory-evict id=%s name=%s",
                window.id, window.tmux_window_name,
            )
        # Re-check pressure each iteration — if it drops, stop early.
        again = pressure_reader()
        if again is None or again < ratio_threshold:
            break
    if killed:
        logger.info(
            "tty_sweeper: memory-pressure killed %d window(s) (pressure=%.2f)",
            killed, pressure,
        )
    return killed


def tick(
    *,
    idle_minutes: int,
    ratio_threshold: float,
    kill_tmux: Optional[TmuxKiller] = None,
) -> dict:
    """One scheduler tick: idle sweep + memory-pressure sweep."""
    idle = sweep_idle(idle_minutes, kill_tmux=kill_tmux)
    pressure = sweep_memory_pressure(ratio_threshold, kill_tmux=kill_tmux)
    return {"idle_killed": idle, "pressure_killed": pressure}
