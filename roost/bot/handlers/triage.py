"""Smart triage + calendar + neurodivergent feature command handlers."""

import re
import logging
from datetime import datetime, timedelta, time, date

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost import task_service
from roost.bot.handlers.common import _format_task_line, _format_task_obj
from roost.bot.tz import get_local_now, to_local_dt

logger = logging.getLogger(__name__)


# ── Date Parsing for /cal ───────────────────────────────────────────

# Day-of-week names → weekday int (Monday=0)
_DAY_NAMES = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _parse_date_arg(args: list[str]) -> tuple[date, date, str, list[str]]:
    """Parse natural date arguments from command args.

    Returns (start_date, end_date, display_label, remaining_args).

    Supported inputs:
      (empty)           → today
      tomorrow / tmr    → tomorrow
      monday / mon etc  → next occurrence of that weekday
      25/2 or 25-2      → specific day/month (current year)
      week / 7d         → today through today+6
    """
    today = get_local_now().date()
    if not args:
        label = today.strftime("Today (%a %d %b)")
        return today, today, label, []

    first = args[0].lower()
    rest = args[1:]

    # "tomorrow" / "tmr"
    if first in ("tomorrow", "tmr"):
        d = today + timedelta(days=1)
        label = d.strftime("Tomorrow (%a %d %b)")
        return d, d, label, rest

    # "week" / "7d"
    if first in ("week", "7d"):
        end = today + timedelta(days=6)
        label = f"{today.strftime('%d %b')}–{end.strftime('%d %b')}"
        return today, end, label, rest

    # Day-of-week name (e.g. "monday", "fri")
    if first in _DAY_NAMES:
        target_wd = _DAY_NAMES[first]
        days_ahead = (target_wd - today.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7  # next week if today is that day
        d = today + timedelta(days=days_ahead)
        label = d.strftime("%A %d %b")
        return d, d, label, rest

    # Date format: "25/2" or "25-2"
    m = re.match(r"^(\d{1,2})[/-](\d{1,2})$", first)
    if m:
        day_num, month_num = int(m.group(1)), int(m.group(2))
        try:
            d = date(today.year, month_num, day_num)
            # If the date is in the past, assume next year
            if d < today:
                d = date(today.year + 1, month_num, day_num)
            label = d.strftime("%a %d %b")
            return d, d, label, rest
        except ValueError:
            pass  # Invalid date, fall through

    # Unrecognised — default to today, pass all args through
    label = today.strftime("Today (%a %d %b)")
    return today, today, label, args


# ── Smart Triage ─────────────────────────────────────────────────────

@authorized
async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Morning briefing: overdue + due today + in progress + top urgent."""
    from roost.triage import get_today_tasks

    triage = get_today_tasks()
    lines = ["*Today's Focus*\n"]

    overdue = triage.get("overdue", [])
    if overdue:
        lines.append(f"*OVERDUE ({len(overdue)}):*")
        for t in overdue[:5]:
            lines.append(_format_task_line(t))

    due = triage.get("due_today", [])
    if due:
        lines.append(f"\n*Due today ({len(due)}):*")
        for t in due[:5]:
            lines.append(_format_task_line(t))

    wip = triage.get("in_progress", [])
    if wip:
        lines.append(f"\n*In progress ({len(wip)}):*")
        for t in wip[:5]:
            lines.append(_format_task_line(t, show_context=True))

    top = triage.get("top_urgent", [])
    if top:
        lines.append(f"\n*Suggested next:*")
        for t in top[:5]:
            lines.append(_format_task_line(t))

    if len(lines) == 1:
        lines.append("Nothing urgent! Enjoy your day.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_urgent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top N tasks by urgency score."""
    n = 10
    if context.args and context.args[0].isdigit():
        n = min(int(context.args[0]), 30)

    tasks = task_service.list_tasks(
        order_by="urgency",
        limit=n,
        exclude_paused_projects=True,
    )
    active = [t for t in tasks if t.status.value != "done"]

    if not active:
        await update.message.reply_text("No active tasks.")
        return

    lines = [f"*Top {len(active)} urgent tasks:*\n"]
    for i, t in enumerate(active, 1):
        score = f" ({t.urgency_score:.0f})" if t.urgency_score else ""
        lines.append(f"{i}. {_format_task_obj(t)}{score}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show in-progress tasks with context notes."""
    tasks = task_service.list_tasks(status="in_progress", exclude_paused_projects=True)

    if not tasks:
        await update.message.reply_text("No tasks in progress. Use /wip ID to start working on something.")
        return

    lines = ["*What you were working on:*\n"]
    for t in tasks:
        lines.append(_format_task_obj(t, show_context=True))
        if t.last_worked_at:
            lines.append(f"    ⏰ Last touched: {t.last_worked_at}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_wip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark task in_progress + set context breadcrumb."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /wip ID [where I left off]")
        return

    task_id = int(context.args[0])
    ctx_note = " ".join(context.args[1:]) if len(context.args) > 1 else ""

    task = task_service.mark_wip(task_id, context=ctx_note)
    if not task:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    msg = f"🔵 Working on #{task.id}: {task.title}"
    if ctx_note:
        msg += f"\n💬 {ctx_note}"
    await update.message.reply_text(msg)


@authorized
async def cmd_lowenergy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Low-energy tasks for bad days."""
    tasks = task_service.list_low_energy_tasks(limit=10)

    if not tasks:
        await update.message.reply_text(
            "No low-energy tasks found.\nTag tasks with energy=low when creating them."
        )
        return

    lines = ["*Low-energy tasks (easy wins):*\n"]
    for t in tasks:
        lines.append(_format_task_obj(t))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Daily Focus ──────────────────────────────────────────────────────

@authorized
async def cmd_daily(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/daily — show today's focus tasks.
    /daily add ID — pin a task to today's focus (max 3).
    /daily remove ID — unpin a task.
    /daily clear — clear all focus tasks.
    """
    if context.args:
        action = context.args[0].lower()

        if action == "add" and len(context.args) > 1 and context.args[1].isdigit():
            tid = int(context.args[1])
            result = task_service.set_focus(tid)
            if result["ok"]:
                await update.message.reply_text(f"Focused: #{tid}")
            else:
                await update.message.reply_text(result["message"])
            return

        if action == "remove" and len(context.args) > 1 and context.args[1].isdigit():
            tid = int(context.args[1])
            count = task_service.clear_focus(tid)
            if count:
                await update.message.reply_text(f"Removed #{tid} from focus.")
            else:
                await update.message.reply_text(f"#{tid} was not focused today.")
            return

        if action == "clear":
            count = task_service.clear_focus()
            await update.message.reply_text(f"Cleared {count} focus task(s).")
            return

        # If arg is a digit, treat as "add"
        if action.isdigit():
            tid = int(action)
            result = task_service.set_focus(tid)
            if result["ok"]:
                await update.message.reply_text(f"Focused: #{tid}")
            else:
                await update.message.reply_text(result["message"])
            return

    # Show focus tasks
    focused = task_service.get_focus_tasks()
    if focused:
        lines = [f"*Daily Focus ({len(focused)}/3):*\n"]
        for t in focused:
            effort = f" [{t.effort_estimate}]" if t.effort_estimate != "moderate" else ""
            lines.append(f"  #{t.id} {t.title}{effort}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    else:
        suggestions = task_service.suggest_focus()
        lines = ["*No tasks focused today.*\n"]
        if suggestions:
            lines.append("Suggestions:")
            for t in suggestions[:3]:
                lines.append(f"  #{t.id} {t.title}")
            lines.append("\nUse /daily add ID to pin one.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Low Energy Day ───────────────────────────────────────────────────

@authorized
async def cmd_lowday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/lowday — set low energy mode and show light tasks."""
    task_service.set_energy_mode("low")
    tasks = task_service.list_matching_effort_tasks("low", limit=10)

    if not tasks:
        await update.message.reply_text(
            "Low energy day set. No light tasks found.\n"
            "Set effort=light on tasks to see them here."
        )
        return

    lines = ["*Low energy day — light tasks:*\n"]
    for t in tasks:
        lines.append(_format_task_obj(t))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Someday / Shelve ─────────────────────────────────────────────────

@authorized
async def cmd_someday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/someday — list shelved tasks.
    /someday ID — toggle shelve/unshelve.
    """
    if context.args and context.args[0].isdigit():
        tid = int(context.args[0])
        task = task_service.get_task(tid)
        if not task:
            await update.message.reply_text(f"Task #{tid} not found.")
            return
        if task.someday:
            task_service.unshelve_task(tid)
            await update.message.reply_text(f"Unshelved: #{tid} {task.title}")
        else:
            task_service.shelve_task(tid)
            await update.message.reply_text(f"Shelved: #{tid} {task.title}")
        return

    # List someday tasks
    tasks = task_service.list_someday_tasks(limit=20)
    someday_tasks = [t for t in tasks if t.someday]

    if not someday_tasks:
        await update.message.reply_text("No shelved tasks. Use /someday ID to shelve one.")
        return

    lines = [f"*Someday pile ({len(someday_tasks)}):*\n"]
    for t in someday_tasks:
        lines.append(f"  #{t.id} {t.title}")

    lines.append("\n/someday ID to unshelve.")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Shutdown Protocol ────────────────────────────────────────────────

@authorized
async def cmd_shutdown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/shutdown — pause all work, defer today's deadlines."""
    if task_service.is_shutdown_active():
        await update.message.reply_text(
            "Day is already paused. Use /resumeday to resume."
        )
        return

    from roost.bot.keyboards import shutdown_confirm_keyboard
    await update.message.reply_text(
        "*Pause Day?*\n\n"
        "This will:\n"
        "- Pause all in-progress tasks\n"
        "- Defer today's deadlines by 1 day\n"
        "- Suppress deadline reminders\n",
        parse_mode="Markdown",
        reply_markup=shutdown_confirm_keyboard(),
    )


@authorized
async def cmd_resumeday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/resumeday — resume from shutdown, restore paused tasks."""
    if not task_service.is_shutdown_active():
        await update.message.reply_text("No active shutdown to resume from.")
        return

    result = task_service.execute_resume()
    await update.message.reply_text(
        f"Day resumed! Restored {result['resumed_count']} task(s) to in-progress."
    )


# ── Reorder ──────────────────────────────────────────────────────────

@authorized
async def cmd_move(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/move FROM TO — move task at position FROM to position TO.
    Example: /move 3 1 — moves task at position 3 to the top.
    """
    if len(context.args) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("Usage: /move FROM TO\nExample: /move 3 1")
        return

    from_pos = int(context.args[0])
    to_pos = int(context.args[1])

    # Find the task at from_pos
    tasks = task_service.list_tasks(
        exclude_paused_projects=True,
        order_by="position",
    )
    active = [t for t in tasks if t.status.value != "done"]

    target = None
    for t in active:
        if t.sort_order == from_pos:
            target = t
            break

    if not target:
        await update.message.reply_text(f"No task at position {from_pos}.")
        return

    task_service.reorder_task(target.id, to_pos)

    # Show updated list
    tasks = task_service.list_tasks(exclude_paused_projects=True)
    active = [t for t in tasks if t.status.value != "done"]
    lines = [f"Moved *{target.title}* to position {to_pos}.\n"]
    for t in active[:10]:
        lines.append(_format_task_obj(t))

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── Calendar ─────────────────────────────────────────────────────────

@authorized
async def cmd_cal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Calendar events + task deadlines. Supports date args and dual providers.

    Usage:
      /cal              — today
      /cal tomorrow     — tomorrow
      /cal mon          — next Monday
      /cal 25/2         — specific date
      /cal week         — 7-day view
    """
    from roost.config import GOOGLE_ENABLED
    from roost.calendar_service import _is_calendar_available, fetch_events_for_range

    start_date, end_date, label, _remaining = _parse_date_arg(context.args or [])
    is_today = (start_date == get_local_now().date() and end_date == get_local_now().date())
    is_range = start_date != end_date

    # Determine active providers
    google_ok = GOOGLE_ENABLED and _is_calendar_available()
    try:
        from roost.microsoft import is_microsoft_available
        ms_ok = is_microsoft_available()
    except Exception:
        ms_ok = False
    dual = google_ok and ms_ok  # show [G]/[M] tags only when both active

    if not google_ok and not ms_ok:
        await update.message.reply_text("_Calendar: not connected_", parse_mode="Markdown")
        return

    # Normalised event list: {summary, start_dt, end_dt, location, all_day, source}
    all_events: list[dict] = []

    # ── Google Calendar events ──
    if google_ok:
        try:
            end_fetch = end_date + timedelta(days=1)
            g_events = fetch_events_for_range(start_date, end_fetch)
            for e in g_events:
                all_events.append({
                    "summary": e.get("summary", "(No title)"),
                    "start_dt": e.get("start"),
                    "end_dt": e.get("end"),
                    "location": e.get("location", ""),
                    "all_day": e.get("all_day", False),
                    "source": "G",
                })
        except Exception:
            logger.exception("Failed to fetch Google Calendar events")

    # ── Microsoft Calendar events ──
    if ms_ok:
        try:
            from roost.mcp.ms_graph_helpers import get_calendar_events as ms_get_events
            ms_start = datetime.combine(start_date, time(0, 0)).isoformat()
            ms_end = datetime.combine(end_date + timedelta(days=1), time(0, 0)).isoformat()
            ms_events = ms_get_events(start=ms_start, end=ms_end)
            for e in ms_events:
                # Parse MS start/end ISO strings to datetime
                s_str = e.get("start", "")
                e_str = e.get("end", "")
                start_dt = _parse_iso_dt(s_str)
                end_dt = _parse_iso_dt(e_str)
                all_events.append({
                    "summary": e.get("summary", "(No title)"),
                    "start_dt": start_dt,
                    "end_dt": end_dt,
                    "location": e.get("location", ""),
                    "all_day": e.get("all_day", False),
                    "source": "M",
                })
        except Exception:
            logger.exception("Failed to fetch MS Calendar events")

    # Sort chronologically
    all_events.sort(key=lambda e: e["start_dt"] or datetime.max)

    # ── Format output ──
    lines = [f"*Schedule for {label}*\n"]

    if all_events:
        if is_range:
            # Group by date for multi-day view
            current_day = None
            for e in all_events:
                event_date = e["start_dt"].date() if e["start_dt"] else None
                if event_date != current_day:
                    current_day = event_date
                    day_label = current_day.strftime("%a %d %b") if current_day else "?"
                    lines.append(f"\n*{day_label}:*")
                lines.append(_format_cal_event(e, dual))
        else:
            lines.append("*Calendar:*")
            for e in all_events[:15]:
                lines.append(_format_cal_event(e, dual))

    # Task triage — only for today view
    if is_today:
        from roost.triage import get_today_tasks
        triage = get_today_tasks()

        due = triage.get("due_today", [])
        if due:
            lines.append(f"\n*Task deadlines today:*")
            for t in due[:5]:
                lines.append(_format_task_line(t))

        overdue = triage.get("overdue", [])
        if overdue:
            lines.append(f"\n*Overdue:*")
            for t in overdue[:5]:
                lines.append(_format_task_line(t))

    if len(lines) == 1:
        lines.append("Clear schedule!")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def _parse_iso_dt(s: str) -> datetime | None:
    """Parse an ISO datetime string and convert to local timezone for display."""
    if not s:
        return None
    try:
        # Handle "2026-02-20T09:00:00.0000000" or "2026-02-20T09:00:00Z"
        s_clean = s.replace("Z", "+00:00")
        # Strip sub-second precision beyond 6 digits (MS Graph returns 7)
        s_clean = re.sub(r"(\.\d{6})\d+", r"\1", s_clean)
        dt = datetime.fromisoformat(s_clean)
        # Convert to configured local TZ, then strip tzinfo for display
        return to_local_dt(dt).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


def _format_cal_event(event: dict, dual: bool) -> str:
    """Format a single normalised calendar event for Telegram display."""
    start_dt = event.get("start_dt")
    if event.get("all_day"):
        time_str = "all day"
    elif start_dt:
        time_str = start_dt.strftime("%H:%M")
    else:
        time_str = "?"
    tag = f" [{event['source']}]" if dual else ""
    loc = f" @ {event['location']}" if event.get("location") else ""
    return f"  📅 {time_str}{tag} {event['summary']}{loc}"


@authorized
async def cmd_calexport(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export task deadlines as downloadable .ics file."""
    try:
        from roost.calendar_service import export_tasks_to_ics
        path = export_tasks_to_ics()
        await update.message.reply_document(
            document=open(path, "rb"),
            filename="task_deadlines.ics",
            caption="Task deadlines exported. Import into your calendar app.",
        )
    except RuntimeError as e:
        await update.message.reply_text(f"Error: {e}")
    except Exception as e:
        await update.message.reply_text(f"Export failed: {e}")


# ── Calendar Slot Blocking ──────────────────────────────────────────

def _parse_block_time(text: str) -> tuple[time | None, time | None, str]:
    """Parse '/block 2pm-4pm deep work' → (start_time, end_time, description).

    Supported time formats:
      2pm-4pm, 14:00-16:00, 2:30pm-4:30pm
      #42 in description links to a task
    """
    text = text.strip()

    # Match time range at the start
    m = re.match(
        r"(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*[-–]\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\s*(.*)",
        text,
        re.IGNORECASE,
    )
    if not m:
        return None, None, ""

    start = _parse_block_time_str(m.group(1))
    end = _parse_block_time_str(m.group(2))
    desc = m.group(3).strip()

    return start, end, desc


def _parse_block_time_str(s: str) -> time | None:
    """Parse a single time string for /block."""
    s = s.strip().lower()

    # "14:00"
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return time(h, mn)
        return None

    # "2pm", "2:30pm"
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", s)
    if m:
        h = int(m.group(1))
        mn = int(m.group(2)) if m.group(2) else 0
        if m.group(3) == "pm" and h != 12:
            h += 12
        elif m.group(3) == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return time(h, mn)

    # Just an hour: "14"
    m = re.match(r"^(\d{1,2})$", s)
    if m:
        h = int(m.group(1))
        if 0 <= h <= 23:
            return time(h, 0)

    return None


@authorized
async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/block [ms|google] TIME_RANGE DESCRIPTION — block a calendar slot.

    Examples:
      /block 2pm-4pm deep work on M5
      /block ms 14:00-16:00 client prep
      /block google 9am-11am #42 lab prep
    """
    args = list(context.args or [])

    if not args:
        await update.message.reply_text(
            "Usage: /block [ms|google] TIME DESCRIPTION\n"
            "Examples:\n"
            "  /block 2pm-4pm deep work on M5\n"
            "  /block ms 14:00-16:00 client prep\n"
            "  /block google 9am-11am #42 lab prep"
        )
        return

    # Detect calendar selector in first arg
    force_provider = None
    first_lower = args[0].lower()
    if first_lower in ("ms", "outlook", "microsoft"):
        force_provider = "microsoft"
        args = args[1:]
    elif first_lower in ("google", "gcal"):
        force_provider = "google"
        args = args[1:]

    if not args:
        await update.message.reply_text("Missing time range. Try: /block 2pm-4pm deep work")
        return

    raw = " ".join(args)
    start_time, end_time, desc = _parse_block_time(raw)

    if not start_time or not end_time:
        await update.message.reply_text(
            "Could not parse time range.\n"
            "Try: 2pm-4pm, 14:00-16:00, 9am-11:30am"
        )
        return

    if not desc:
        desc = "Blocked time"

    # Check for task reference #ID
    task_ref = ""
    task_match = re.search(r"#(\d+)", desc)
    if task_match:
        tid = int(task_match.group(1))
        task = task_service.get_task(tid)
        if task:
            task_ref = f"\nLinked task: #{task.id} {task.title}"

    # Build datetimes
    today = get_local_now().date()
    start_dt = datetime.combine(today, start_time)
    end_dt = datetime.combine(today, end_time)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    # Determine which provider to use
    from roost.config import GOOGLE_ENABLED, REMINDER_TIMEZONE
    from roost.calendar_service import _is_calendar_available

    google_ok = GOOGLE_ENABLED and _is_calendar_available()
    try:
        from roost.microsoft import is_microsoft_available
        ms_ok = is_microsoft_available()
    except Exception:
        ms_ok = False

    use_ms = False
    if force_provider == "microsoft":
        if not ms_ok:
            await update.message.reply_text("Microsoft Calendar is not available.")
            return
        use_ms = True
    elif force_provider == "google":
        if not google_ok:
            await update.message.reply_text("Google Calendar is not available.")
            return
        use_ms = False
    else:
        # Auto: prefer Google, fall back to MS
        if google_ok:
            use_ms = False
        elif ms_ok:
            use_ms = True
        else:
            await update.message.reply_text("No calendar service is enabled on this instance.")
            return

    try:
        if use_ms:
            from roost.mcp.ms_graph_helpers import create_event

            result = create_event(
                summary=desc,
                start=start_dt.isoformat(),
                end=end_dt.isoformat(),
                description=f"Blocked via Roost /block{task_ref}",
                timezone=REMINDER_TIMEZONE,
            )
            if "error" in result:
                await update.message.reply_text(f"Calendar block failed: {result['error']}")
                return

            time_str = f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
            provider_label = "Outlook" if (google_ok and ms_ok) else "Calendar"
            msg = f"Blocked on {provider_label}: {time_str} — {desc}"
            web_link = result.get("web_link", "")
            if web_link:
                msg += f"\n[Open in Calendar]({web_link})"
            if task_ref:
                msg += task_ref
            await update.message.reply_text(msg, parse_mode="Markdown")
        else:
            from roost.gmail import get_calendar_service

            service = get_calendar_service()
            if not service:
                await update.message.reply_text("Google Calendar service unavailable.")
                return

            event_body = {
                "summary": desc,
                "description": f"Blocked via Roost /block{task_ref}",
                "start": {
                    "dateTime": start_dt.isoformat(),
                    "timeZone": "Asia/Singapore",
                },
                "end": {
                    "dateTime": end_dt.isoformat(),
                    "timeZone": "Asia/Singapore",
                },
                "transparency": "opaque",
                "reminders": {
                    "useDefault": False,
                    "overrides": [
                        {"method": "popup", "minutes": 10},
                    ],
                },
            }

            event = service.events().insert(
                calendarId="primary", body=event_body,
            ).execute()

            event_link = event.get("htmlLink", "")
            time_str = f"{start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')}"
            provider_label = "Google Calendar" if (google_ok and ms_ok) else "Calendar"
            msg = f"Blocked on {provider_label}: {time_str} — {desc}"
            if event_link:
                msg += f"\n[Open in Calendar]({event_link})"
            if task_ref:
                msg += task_ref
            await update.message.reply_text(msg, parse_mode="Markdown")

    except Exception as e:
        logger.exception("Failed to create calendar block")
        await update.message.reply_text(f"Calendar block failed: {e}")


# ── /next — Just One Thing ──────────────────────────────────────────

@authorized
async def cmd_next(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/next — show the single most important task right now."""
    task = task_service.get_next_task()
    if not task:
        await update.message.reply_text("No active tasks. Enjoy the calm!")
        return

    effort = f" [{task.effort_estimate}]" if task.effort_estimate != "moderate" else ""
    proj = f" [{task.project_name}]" if task.project_name else ""
    ctx = f"\n{task.context_note}" if task.context_note else ""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Start", callback_data=f"next:start:{task.id}"),
            InlineKeyboardButton("Skip", callback_data="next:skip"),
        ],
    ])

    await update.message.reply_text(
        f"*Right now, just this one thing:*\n\n"
        f"#{task.id} {task.title}{proj}{effort}{ctx}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ── /pick — Smart Picker ───────────────────────────────────────────

@authorized
async def cmd_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/pick [light|moderate|heavy] — system picks a task for you."""
    energy = None
    if context.args and context.args[0].lower() in ("light", "low", "moderate", "heavy"):
        energy = context.args[0].lower()
        if energy == "low":
            energy = "light"

    task = task_service.pick_task(energy_budget=energy)
    if not task:
        await update.message.reply_text("No matching tasks to pick from.")
        return

    effort = f" [{task.effort_estimate}]" if task.effort_estimate != "moderate" else ""
    proj = f" [{task.project_name}]" if task.project_name else ""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Let's go!", callback_data=f"pick:go:{task.id}"),
            InlineKeyboardButton("Pick again", callback_data=f"pick:again:{energy or 'all'}"),
        ],
    ])

    await update.message.reply_text(
        f"*How about this one?*\n\n"
        f"#{task.id} {task.title}{proj}{effort}",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ── /timer — Pomodoro Timer ────────────────────────────────────────

@authorized
async def cmd_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/timer [task_id] [minutes] — start a pomodoro timer.
    /timer stop — cancel current timer.
    /timer status — show remaining time.
    """
    from roost.bot.pomodoro import (
        start_timer, stop_timer, get_timer_status, get_timer,
    )

    args = context.args or []

    if args and args[0].lower() == "stop":
        state = await stop_timer(update.effective_chat.id, context)
        if state:
            elapsed = (get_local_now() - state.started_at).total_seconds() / 60
            await update.message.reply_text(f"Timer stopped. {elapsed:.0f}min elapsed.")
        else:
            await update.message.reply_text("No active timer.")
        return

    if args and args[0].lower() == "status":
        status = get_timer_status(update.effective_chat.id)
        if not status:
            await update.message.reply_text("No active timer.")
            return
        task_info = f' on "{status["task_title"]}"' if status["task_title"] else ""
        await update.message.reply_text(
            f"Timer ({status['phase']}): {status['remaining_min']}m {status['remaining_sec']}s remaining{task_info}"
        )
        return

    # Parse args: /timer [task_id] [duration]
    task_id = None
    duration = 25
    task_title = ""

    for arg in args:
        if arg.isdigit():
            val = int(arg)
            if task_id is None and val > 60:
                # Probably a task ID
                task_id = val
            elif task_id is None and val <= 60:
                # Could be task ID or duration — check if task exists
                t = task_service.get_task(val)
                if t:
                    task_id = val
                    task_title = t.title
                else:
                    duration = val
            else:
                duration = val

    if task_id and not task_title:
        t = task_service.get_task(task_id)
        if t:
            task_title = t.title
        else:
            await update.message.reply_text(f"Task #{task_id} not found.")
            return

    state = await start_timer(
        update.effective_chat.id,
        context,
        task_id=task_id,
        task_title=task_title,
        duration=duration,
    )

    task_info = f' on "{task_title}"' if task_title else ""
    await update.message.reply_text(
        f"Timer started! {duration} minutes{task_info}.\n"
        f"Use /timer stop to cancel, /timer status to check."
    )


# ── /checkin — Transition Prompts ───────────────────────────────────

@authorized
async def cmd_checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/checkin on|off|Nh — configure check-in prompts."""
    args = context.args or []

    if not args:
        enabled = task_service.get_setting("checkin_enabled")
        interval = task_service.get_setting("checkin_interval_hours") or "2"
        status = "on" if enabled == "1" else "off"
        await update.message.reply_text(
            f"*Check-in prompts:* {status}\n"
            f"Interval: every {interval}h (9am-6pm)\n\n"
            f"`/checkin on` — enable\n"
            f"`/checkin off` — disable\n"
            f"`/checkin 3h` — set interval",
            parse_mode="Markdown",
        )
        return

    action = args[0].lower()

    if action == "on":
        task_service.set_setting("checkin_enabled", "1")
        await update.message.reply_text("Check-in prompts enabled. You'll get gentle nudges during work hours.")

    elif action == "off":
        task_service.set_setting("checkin_enabled", "0")
        await update.message.reply_text("Check-in prompts disabled.")

    elif action.endswith("h") and action[:-1].isdigit():
        hours = int(action[:-1])
        if 1 <= hours <= 8:
            task_service.set_setting("checkin_interval_hours", str(hours))
            await update.message.reply_text(f"Check-in interval set to every {hours}h.")
        else:
            await update.message.reply_text("Interval must be 1-8 hours.")

    else:
        await update.message.reply_text("Usage: /checkin on|off|Nh")


# ── /done today — Activity Log ──────────────────────────────────────

@authorized
async def cmd_done_today(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/done today — show today's activity timeline."""
    activities = task_service.get_today_activity()

    if not activities:
        await update.message.reply_text("No activity logged today yet. Get started!")
        return

    lines = ["*Today's Activity:*\n"]
    for a in activities:
        time_str = a["created_at"][11:16] if a["created_at"] and len(a["created_at"]) > 16 else "?"
        title = a.get("task_title") or ""
        detail = f' — {a["detail"]}' if a.get("detail") else ""
        action_icon = {
            "completed": "done",
            "started": "started",
            "paused": "paused",
            "resumed": "resumed",
            "timer_done": "timer",
        }.get(a["action"], a["action"])

        if title:
            lines.append(f"  {time_str}  {action_icon}: \"{title}\"{detail}")
        else:
            lines.append(f"  {time_str}  {action_icon}{detail}")

    # Streak info
    streak = task_service.get_streak()
    if streak["current"] > 1:
        lines.append(f"\nStreak: {streak['current']} days (best: {streak['best']})")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ── /routine — Morning/Evening Checklists ───────────────────────────

@authorized
async def cmd_routine(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/routine [morning|evening] — show routine checklist.
    /routine add morning "item" — add item.
    /routine remove ID — remove item.
    """
    args = context.args or []

    # Add item
    if len(args) >= 3 and args[0].lower() == "add":
        routine_name = args[1].lower()
        title = " ".join(args[2:]).strip('"').strip("'")
        if routine_name not in ("morning", "evening"):
            await update.message.reply_text("Routine must be 'morning' or 'evening'.")
            return
        result = task_service.add_routine_item(routine_name, title)
        await update.message.reply_text(
            f"Added to {routine_name} routine: {title}\n"
            f"({len(result['items'])} items total)"
        )
        return

    # Remove item
    if len(args) >= 2 and args[0].lower() == "remove":
        if not args[1].isdigit():
            await update.message.reply_text("Usage: /routine remove <item_id>")
            return
        item_id = int(args[1])
        if task_service.remove_routine_item(item_id):
            await update.message.reply_text(f"Removed routine item #{item_id}.")
        else:
            await update.message.reply_text(f"Item #{item_id} not found.")
        return

    # Show routine
    routine_name = args[0].lower() if args else "morning"
    if routine_name not in ("morning", "evening"):
        routine_name = "morning"

    routine = task_service.get_routine(routine_name)
    if not routine or not routine["items"]:
        await update.message.reply_text(
            f"*{routine_name.title()} routine* — empty\n\n"
            f"Add items: /routine add {routine_name} \"Check calendar\"",
            parse_mode="Markdown",
        )
        return

    lines = [f"*{routine_name.title()} Routine:*\n"]
    buttons = []
    for item in routine["items"]:
        check = "x" if item["completed"] else " "
        lines.append(f"  [{check}] {item['title']}")
        # Toggle button
        if item["completed"]:
            buttons.append(InlineKeyboardButton(
                f"Uncheck: {item['title'][:20]}",
                callback_data=f"routine:uncheck:{item['id']}:{routine_name}",
            ))
        else:
            buttons.append(InlineKeyboardButton(
                f"Check: {item['title'][:20]}",
                callback_data=f"routine:check:{item['id']}:{routine_name}",
            ))

    done_count = sum(1 for i in routine["items"] if i["completed"])
    total = len(routine["items"])
    lines.append(f"\n{done_count}/{total} completed")

    # Layout buttons in rows of 1 (each is a checklist item)
    rows = [[b] for b in buttons]
    keyboard = InlineKeyboardMarkup(rows) if rows else None

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


# ── /break — Task Decomposition ────────────────────────────────────

@authorized
async def cmd_break_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/break ID — break a task into subtasks using capture mode."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /break <task_id>")
        return

    task_id = int(context.args[0])
    task = task_service.get_task(task_id)
    if not task:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    # Start capture session in subtask mode
    from roost.bot.capture import start_session, capture_control_keyboard, CaptureSession

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    session = start_session(user_id, chat_id, "subtasks", target_id=task_id)

    await update.message.reply_text(
        f"*Break down:* #{task.id} {task.title}\n\n"
        f"Type each subtask, one per message.\n"
        f"Send /capture stop when done.",
        parse_mode="Markdown",
        reply_markup=capture_control_keyboard(session),
    )


# ── /spoons — Spoon Budget ─────────────────────────────────────────

@authorized
async def cmd_spoons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/spoons — show remaining spoon budget.
    /spoons N — set daily budget.
    /spoons reset — reset today's count.
    """
    args = context.args or []

    if args and args[0].lower() == "reset":
        status = task_service.reset_spoons()
        await update.message.reply_text(
            f"Spoons reset! Budget: {status['budget']}/{status['budget']}"
        )
        return

    if args and args[0].isdigit():
        budget = int(args[0])
        if 1 <= budget <= 50:
            status = task_service.set_spoon_budget(budget)
            await update.message.reply_text(f"Daily spoon budget set to {budget}.")
        else:
            await update.message.reply_text("Budget must be 1-50.")
        return

    status = task_service.get_spoon_status()
    filled = round(status["remaining"] / status["budget"] * 10) if status["budget"] > 0 else 0
    empty = 10 - filled
    meter = "●" * filled + "○" * empty

    lines = [
        f"*Spoons:* {meter} {status['remaining']}/{status['budget']} remaining",
        f"Spent today: {status['spent']}",
    ]

    if status["remaining"] <= 2 and status["remaining"] < status["budget"]:
        lines.append("\nLow on spoons. Consider light tasks only.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
