"""Pomodoro timer state management for the Telegram bot.

In-memory timer state (like capture.py pattern) with APScheduler integration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from roost.bot.tz import get_local_now

logger = logging.getLogger(__name__)


@dataclass
class TimerState:
    chat_id: int
    task_id: int | None = None
    task_title: str = ""
    duration: int = 25           # Work duration in minutes
    break_duration: int = 5      # Break duration in minutes
    started_at: datetime = field(default_factory=get_local_now)
    phase: str = "work"          # "work" or "break"
    job_name: str = ""           # APScheduler job reference


_active_timers: dict[int, TimerState] = {}


def get_timer(chat_id: int) -> TimerState | None:
    """Get active timer for a chat."""
    return _active_timers.get(chat_id)


def timer_keyboard(chat_id: int):
    """Build inline keyboard for timer notifications."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    timer = get_timer(chat_id)
    if not timer:
        return InlineKeyboardMarkup([])

    if timer.phase == "work":
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Start break", callback_data="timer:break"),
                InlineKeyboardButton("Done for now", callback_data="timer:stop"),
            ],
        ])
    else:
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Another round", callback_data="timer:another"),
                InlineKeyboardButton("Stop", callback_data="timer:stop"),
            ],
        ])


async def start_timer(
    chat_id: int,
    context,
    task_id: int | None = None,
    task_title: str = "",
    duration: int = 25,
    break_duration: int = 5,
) -> TimerState:
    """Start a pomodoro timer."""
    # Cancel existing timer if any
    await stop_timer(chat_id, context)

    job_name = f"timer_{chat_id}"
    state = TimerState(
        chat_id=chat_id,
        task_id=task_id,
        task_title=task_title,
        duration=duration,
        break_duration=break_duration,
        phase="work",
        job_name=job_name,
    )
    _active_timers[chat_id] = state

    # Schedule one-shot job
    context.job_queue.run_once(
        _timer_fired,
        when=duration * 60,
        name=job_name,
        chat_id=chat_id,
        data={"chat_id": chat_id},
    )

    return state


async def _timer_fired(context) -> None:
    """Called when a timer expires."""
    chat_id = context.job.chat_id
    state = _active_timers.get(chat_id)
    if not state:
        return

    if state.phase == "work":
        # Work phase done
        task_info = f' on "{state.task_title}"' if state.task_title else ""
        elapsed = state.duration

        msg = f"Timer done! {elapsed} minutes{task_info}."

        # Log activity if task linked
        if state.task_id:
            try:
                from roost.task_service import log_activity
                log_activity(state.task_id, "timer_done", f"{elapsed}min pomodoro")
            except Exception:
                logger.debug("Failed to log timer activity for task %s", state.task_id, exc_info=True)

        await context.bot.send_message(
            chat_id=chat_id,
            text=msg,
            reply_markup=timer_keyboard(chat_id),
        )

    elif state.phase == "break":
        # Break phase done
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"Break's over! ({state.break_duration}min)",
            reply_markup=timer_keyboard(chat_id),
        )


async def start_break(chat_id: int, context) -> TimerState | None:
    """Switch from work to break phase."""
    state = _active_timers.get(chat_id)
    if not state:
        return None

    # Cancel current job
    _cancel_job(context, state.job_name)

    state.phase = "break"
    state.started_at = get_local_now()

    # Schedule break timer
    context.job_queue.run_once(
        _timer_fired,
        when=state.break_duration * 60,
        name=state.job_name,
        chat_id=chat_id,
        data={"chat_id": chat_id},
    )

    return state


async def start_another_round(chat_id: int, context) -> TimerState | None:
    """Start another work round with the same settings."""
    state = _active_timers.get(chat_id)
    if not state:
        return None

    # Cancel current job
    _cancel_job(context, state.job_name)

    state.phase = "work"
    state.started_at = get_local_now()

    context.job_queue.run_once(
        _timer_fired,
        when=state.duration * 60,
        name=state.job_name,
        chat_id=chat_id,
        data={"chat_id": chat_id},
    )

    return state


async def stop_timer(chat_id: int, context) -> TimerState | None:
    """Stop and remove timer, returning elapsed info."""
    state = _active_timers.pop(chat_id, None)
    if state:
        _cancel_job(context, state.job_name)
    return state


def get_timer_status(chat_id: int) -> dict | None:
    """Get remaining time for an active timer."""
    state = _active_timers.get(chat_id)
    if not state:
        return None

    elapsed = (datetime.now() - state.started_at).total_seconds()
    total = (state.duration if state.phase == "work" else state.break_duration) * 60
    remaining = max(0, total - elapsed)

    return {
        "phase": state.phase,
        "task_id": state.task_id,
        "task_title": state.task_title,
        "elapsed_min": int(elapsed // 60),
        "remaining_min": int(remaining // 60),
        "remaining_sec": int(remaining % 60),
        "duration": state.duration,
        "break_duration": state.break_duration,
    }


def _cancel_job(context, job_name: str) -> None:
    """Cancel a scheduled job by name."""
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    for job in current_jobs:
        job.schedule_removal()
