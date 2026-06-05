"""Automation gating for inbound messages.

Two controls, both read from `settings.yaml` (live, no restart):

* ``automations_paused`` — global kill switch. When on, nothing
  auto-runs; every inbound waits for a human.
* ``auto_engage_window_hours`` — recency gate. A *returning* contact whose
  prior activity is older than the window is treated as dormant and not
  auto-engaged (their message lands in the inbox for a human instead of
  auto-continuing the questionnaire or drafting a reply). A brand-new
  contact has no prior activity and is never dormant. 0 disables the gate.

Inbound handlers call :func:`automation_gate` right after the STOP/HELP
intercepts (which must always work) and skip auto-processing when it
returns ``proceed=False``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from roost.extras.lead_nurture.services import settings as _settings
from roost.extras.lead_nurture.services.cadences import store as _store

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Sentinel filename for the live pause switch. Lives in the (bind-mounted,
# persistent) data dir so an operator can pause/resume without a restart or
# rebuild: `touch data/automations_paused` to pause, remove it to resume.
_PAUSE_SENTINEL = "automations_paused"


def _paused_via_sentinel() -> bool:
    try:
        from roost.config import DATABASE_PATH
        return (Path(DATABASE_PATH).parent / _PAUSE_SENTINEL).exists()
    except Exception:
        return False


def is_paused() -> bool:
    """True when automation is globally paused — via the baked settings flag
    or the live data-dir sentinel file."""
    return bool(_settings.get("automations_paused", False)) or _paused_via_sentinel()


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def automation_gate(
    *,
    phone: str = "",
    email: str = "",
    telegram_chat_id: str = "",
    now: datetime | None = None,
) -> dict:
    """Decide whether auto-engagement should run for an inbound message.

    Returns ``{"proceed": bool, "reason": str, ...}`` where ``reason`` is:
      * ``"paused"``  — the global ``automations_paused`` switch is on,
      * ``"dormant"`` — the contact's prior activity is older than
        ``auto_engage_window_hours`` (extra key ``dormant_hours``),
      * ``"ok"``      — proceed normally.
    Never raises.
    """
    if is_paused():
        return {"proceed": False, "reason": "paused"}

    try:
        window_h = float(_settings.get("auto_engage_window_hours", 0) or 0)
    except (TypeError, ValueError):
        window_h = 0.0

    if window_h > 0:
        last = _parse_ts(
            _store.last_activity_at(
                phone=phone, email=email, telegram_chat_id=telegram_chat_id
            )
        )
        if last is not None:
            now = now or datetime.now(timezone.utc)
            age_h = (now - last).total_seconds() / 3600.0
            if age_h > window_h:
                return {
                    "proceed": False,
                    "reason": "dormant",
                    "dormant_hours": round(age_h, 1),
                }

    return {"proceed": True, "reason": "ok"}
