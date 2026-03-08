"""Task and note CRUD command handlers + audit log + inline keyboards."""

import math
import re
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.models import TaskCreate, TaskUpdate, NoteCreate
from roost import task_service
from roost.bot.handlers.common import _format_task_obj, escape_md, resolve_bot_user_id
from roost.bot.tz import get_local_now
from roost.bot.keyboards import (
    task_list_keyboard,
    task_detail_keyboard,
    task_edit_keyboard,
    priority_picker_keyboard,
    status_picker_keyboard,
    energy_picker_keyboard,
    effort_picker_keyboard,
    time_picker_date_keyboard,
    time_picker_hour_keyboard,
)

TASKS_PER_PAGE = 8

# ── Filter mapping ──────────────────────────────────────────────────

_FILTER_STATUS = {
    "all": None,
    "todo": "todo",
    "wip": "in_progress",
    "done": "done",
}

_PRIORITY_MAP = {
    "low": "low",
    "med": "medium",
    "high": "high",
    "urg": "urgent",
}

_STATUS_MAP = {
    "todo": "todo",
    "wip": "in_progress",
    "blk": "blocked",
    "done": "done",
}


# ── Paginated task list ─────────────────────────────────────────────

async def _send_task_page(message_or_query, filter_key: str, page: int,
                          edit: bool = False, user_id: int | None = None):
    """Build and send/edit a paginated task list."""
    status = _FILTER_STATUS.get(filter_key)

    kwargs = {"order_by": "urgency", "exclude_paused_projects": True}
    if status:
        kwargs["status"] = status
    if user_id is not None:
        kwargs["user_id"] = user_id

    tasks = task_service.list_tasks(**kwargs)
    if filter_key == "all":
        tasks = [t for t in tasks if t.status.value != "done"]

    total = len(tasks)
    total_pages = max(1, math.ceil(total / TASKS_PER_PAGE))
    page = max(1, min(page, total_pages))

    start = (page - 1) * TASKS_PER_PAGE
    page_tasks = tasks[start : start + TASKS_PER_PAGE]

    if not tasks:
        text = "No tasks found."
        keyboard = task_list_keyboard(filter_key, 1, 1, [])
    else:
        lines = [f"*Tasks* ({total} total)\n"]
        for t in page_tasks:
            lines.append(_format_task_obj(t))
        text = "\n".join(lines)
        task_ids = [t.id for t in page_tasks]
        task_positions = [getattr(t, "sort_order", 0) for t in page_tasks]
        keyboard = task_list_keyboard(filter_key, page, total_pages, task_ids, task_positions)

    if edit:
        await message_or_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard,
        )
    else:
        await message_or_query.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard,
        )


@authorized
async def cmd_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paginated task list with filter buttons."""
    uid = resolve_bot_user_id(update)
    await _send_task_page(update.message, "all", 1, user_id=uid)


async def handle_tasks_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle tasks:<filter>:<page> callback queries."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    filter_key = parts[1] if len(parts) > 1 else "all"
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1

    uid = resolve_bot_user_id(update)
    await _send_task_page(query, filter_key, page, edit=True, user_id=uid)


# ── Task detail with action buttons ─────────────────────────────────

async def _send_task_detail(message_or_query, task_id: int, edit: bool = False):
    """Build and send/edit a task detail view with action buttons."""
    task = task_service.get_task(task_id)
    if not task:
        text = f"Task #{task_id} not found."
        if edit:
            await message_or_query.edit_message_text(text)
        else:
            await message_or_query.reply_text(text)
        return

    proj = task.project_name or "none"
    someday_tag = " [someday]" if task.someday else ""
    focus_tag = " [focused]" if task.focus_date else ""
    text = (
        f"*Task #{task.id}*{someday_tag}{focus_tag}\n"
        f"Title: {escape_md(task.title)}\n"
        f"Status: {task.status.value}\n"
        f"Priority: {task.priority.value}\n"
        f"Project: {proj}\n"
        f"Energy: {task.energy_level}\n"
        f"Effort: {task.effort_estimate}\n"
        f"Urgency: {task.urgency_score:.0f}\n"
    )
    if task.deadline:
        text += f"Deadline: {task.deadline}\n"
    if task.context_note:
        text += f"Context: {escape_md(task.context_note)}\n"
    if task.last_worked_at:
        text += f"Last worked: {task.last_worked_at}\n"
    if task.subtask_count > 0:
        text += f"Subtasks: {task.subtask_done}/{task.subtask_count}\n"
    if task.description:
        text += f"\n{escape_md(task.description)}"

    keyboard = task_detail_keyboard(task_id, task.status.value)

    if edit:
        await message_or_query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=keyboard,
        )
    else:
        await message_or_query.reply_text(
            text, parse_mode="Markdown", reply_markup=keyboard,
        )


@authorized
async def cmd_show(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show task details with action buttons."""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /show ID")
        return
    task_id = int(context.args[0])
    await _send_task_detail(update.message, task_id)


async def handle_task_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle task:<id>:<action> callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    task_id = int(parts[1])
    action = parts[2] if len(parts) > 2 else "view"

    if action == "view":
        await query.answer()
        await _send_task_detail(query, task_id, edit=True)

    elif action == "done":
        task = task_service.complete_task(task_id, source="telegram")
        if task:
            celebration = task_service.get_celebration()
            await query.answer(celebration[:200])
        else:
            await query.answer(f"Task #{task_id} not found.", show_alert=True)
        await _send_task_detail(query, task_id, edit=True)

    elif action == "reopen":
        from roost.models import TaskStatus
        task = task_service.update_task(
            task_id, TaskUpdate(status=TaskStatus.TODO), source="telegram"
        )
        if task:
            await query.answer(f"#{task_id} reopened!")
        else:
            await query.answer(f"Task #{task_id} not found.", show_alert=True)
        await _send_task_detail(query, task_id, edit=True)

    elif action == "settime":
        await query.answer()
        now = get_local_now()
        keyboard = time_picker_date_keyboard(task_id, now.year, now.month)
        await query.edit_message_text(
            f"*Set deadline for #{task_id}*\nPick a date:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif action == "subtasks":
        await query.answer()
        subtasks = task_service.list_subtasks(task_id)
        if not subtasks:
            text = f"*Subtasks of #{task_id}*\n\nNo subtasks."
        else:
            lines = [f"*Subtasks of #{task_id}*\n"]
            for st in subtasks:
                lines.append(_format_task_obj(st))
            text = "\n".join(lines)
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        back_kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("\u2b05 Back", callback_data=f"task:{task_id}:view")]
        ])
        await query.edit_message_text(text, parse_mode="Markdown", reply_markup=back_kb)

    elif action == "edit":
        await query.answer()
        await query.edit_message_text(
            f"*Edit task #{task_id}*\nChoose what to change:",
            parse_mode="Markdown",
            reply_markup=task_edit_keyboard(task_id),
        )

    elif action == "eprio":
        await query.answer()
        await query.edit_message_text(
            f"*Set priority for #{task_id}:*",
            parse_mode="Markdown",
            reply_markup=priority_picker_keyboard(task_id),
        )

    elif action == "estat":
        await query.answer()
        await query.edit_message_text(
            f"*Set status for #{task_id}:*",
            parse_mode="Markdown",
            reply_markup=status_picker_keyboard(task_id),
        )

    elif action == "eenrg":
        await query.answer()
        await query.edit_message_text(
            f"*Set energy level for #{task_id}:*",
            parse_mode="Markdown",
            reply_markup=energy_picker_keyboard(task_id),
        )

    elif action == "eefrt":
        await query.answer()
        await query.edit_message_text(
            f"*Set effort estimate for #{task_id}:*",
            parse_mode="Markdown",
            reply_markup=effort_picker_keyboard(task_id),
        )

    elif action == "shelve":
        task_service.shelve_task(task_id)
        await query.answer(f"#{task_id} shelved (someday)")
        await _send_task_detail(query, task_id, edit=True)

    elif action == "unshelve":
        task_service.unshelve_task(task_id)
        await query.answer(f"#{task_id} unshelved")
        await _send_task_detail(query, task_id, edit=True)

    elif action == "sp":
        # Set priority: task:ID:sp:low/med/high/urg
        prio_key = parts[3] if len(parts) > 3 else ""
        from roost.models import Priority
        prio_val = _PRIORITY_MAP.get(prio_key)
        if prio_val:
            task_service.update_task(
                task_id, TaskUpdate(priority=Priority(prio_val)), source="telegram"
            )
            await query.answer(f"Priority set to {prio_val}")
        else:
            await query.answer("Invalid priority", show_alert=True)
        await _send_task_detail(query, task_id, edit=True)

    elif action == "ss":
        # Set status: task:ID:ss:todo/wip/blk/done
        status_key = parts[3] if len(parts) > 3 else ""
        from roost.models import TaskStatus
        status_val = _STATUS_MAP.get(status_key)
        if status_val:
            task_service.update_task(
                task_id, TaskUpdate(status=TaskStatus(status_val)), source="telegram"
            )
            await query.answer(f"Status set to {status_val}")
        else:
            await query.answer("Invalid status", show_alert=True)
        await _send_task_detail(query, task_id, edit=True)

    elif action == "se":
        # Set energy: task:ID:se:low/med/high
        energy_key = parts[3] if len(parts) > 3 else ""
        from roost.models import EnergyLevel
        energy_map = {"low": "low", "med": "medium", "high": "high"}
        energy_val = energy_map.get(energy_key)
        if energy_val:
            task_service.update_task(
                task_id, TaskUpdate(energy_level=EnergyLevel(energy_val)), source="telegram"
            )
            await query.answer(f"Energy set to {energy_val}")
        else:
            await query.answer("Invalid energy level", show_alert=True)
        await _send_task_detail(query, task_id, edit=True)

    elif action == "sef":
        # Set effort: task:ID:sef:light/mod/heavy
        effort_key = parts[3] if len(parts) > 3 else ""
        from roost.models import EffortEstimate
        effort_map = {"light": "light", "mod": "moderate", "heavy": "heavy"}
        effort_val = effort_map.get(effort_key)
        if effort_val:
            task_service.update_task(
                task_id, TaskUpdate(effort_estimate=EffortEstimate(effort_val)), source="telegram"
            )
            await query.answer(f"Effort set to {effort_val}")
        else:
            await query.answer("Invalid effort level", show_alert=True)
        await _send_task_detail(query, task_id, edit=True)

    else:
        await query.answer("Unknown action.")


# ── Inline time picker callback ──────────────────────────────────────

async def handle_settime_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settime:<id>:<step>:... callback queries for the time picker wizard."""
    query = update.callback_query
    parts = query.data.split(":")
    task_id = int(parts[1])
    step = parts[2] if len(parts) > 2 else ""

    if step == "nav":
        # Navigate month: settime:ID:nav:year:month
        year = int(parts[3])
        month = int(parts[4])
        # Handle month overflow
        if month < 1:
            month = 12
            year -= 1
        elif month > 12:
            month = 1
            year += 1
        await query.answer()
        keyboard = time_picker_date_keyboard(task_id, year, month)
        await query.edit_message_text(
            f"*Set deadline for #{task_id}*\nPick a date:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif step == "day":
        # Date selected: settime:ID:day:year:month:day → show hour picker
        year = int(parts[3])
        month = int(parts[4])
        day = int(parts[5])
        date_str = f"{year}-{month:02d}-{day:02d}"
        await query.answer()
        keyboard = time_picker_hour_keyboard(task_id, date_str)
        await query.edit_message_text(
            f"*Set deadline for #{task_id}*\nDate: {date_str}\nPick a time:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif step == "hour":
        # Time selected: settime:ID:hour:date_str:HH or allday
        date_str = parts[3]
        hour_val = parts[4]

        if hour_val == "allday":
            deadline = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=23, minute=59
            )
        else:
            deadline = datetime.strptime(date_str, "%Y-%m-%d").replace(
                hour=int(hour_val), minute=0
            )

        task = task_service.update_task(
            task_id, TaskUpdate(deadline=deadline), source="telegram"
        )

        if not task:
            await query.answer(f"Task #{task_id} not found.", show_alert=True)
            return

        # Push to Google Calendar
        cal_msg = ""
        try:
            from roost.gmail.calendar_write import create_event_from_task
            event_id = create_event_from_task(task)
            if event_id:
                cal_msg = "\nPushed to Google Calendar."
        except Exception:
            cal_msg = "\n(Calendar push failed — check Gmail config)"

        await query.answer(f"Deadline set!")
        await _send_task_detail(query, task_id, edit=True)

    else:
        await query.answer("Unknown time picker step.")


# ── /settime text command ────────────────────────────────────────────

def _parse_natural_time(text: str) -> datetime | None:
    """Parse natural time expressions into a datetime.

    Supports:
      today [HH:MM|HHam/pm]
      tomorrow [HH:MM|HHam/pm]
      YYYY-MM-DD [HH:MM]
      HH:MM (today)
      Ham/Hpm (today)
    """
    text = text.strip().lower()
    now = get_local_now()

    # "today 2pm", "tomorrow 14:00"
    m = re.match(r"(today|tomorrow)\s+(.*)", text)
    if m:
        base = now.date() if m.group(1) == "today" else (now + timedelta(days=1)).date()
        time_part = _parse_time_str(m.group(2))
        if time_part is not None:
            return datetime.combine(base, time_part)
        return datetime.combine(base, datetime.min.time().replace(hour=23, minute=59))

    if text == "today":
        return datetime.combine(now.date(), datetime.min.time().replace(hour=23, minute=59))
    if text == "tomorrow":
        return datetime.combine(
            (now + timedelta(days=1)).date(),
            datetime.min.time().replace(hour=23, minute=59),
        )

    # "2026-02-15 14:00"
    m = re.match(r"(\d{4}-\d{2}-\d{2})\s+(.*)", text)
    if m:
        try:
            base = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            return None
        time_part = _parse_time_str(m.group(2))
        if time_part is not None:
            return datetime.combine(base, time_part)
        return datetime.combine(base, datetime.min.time().replace(hour=23, minute=59))

    # Just a date: "2026-02-15"
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
        return dt.replace(hour=23, minute=59)
    except ValueError:
        pass

    # Just a time: "2pm", "14:00" → today
    time_part = _parse_time_str(text)
    if time_part is not None:
        return datetime.combine(now.date(), time_part)

    return None


def _parse_time_str(s: str) -> "datetime.time | None":
    """Parse a time string like '14:00', '2pm', '2:30pm'."""
    from datetime import time
    s = s.strip().lower()

    # "14:00", "9:30"
    m = re.match(r"^(\d{1,2}):(\d{2})$", s)
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mn <= 59:
            return time(h, mn)
        return None

    # "2pm", "2:30pm", "11am"
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

    return None


@authorized
async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/settime ID [time expression] — set task deadline.

    Examples:
      /settime 42 tomorrow 2pm
      /settime 42 2026-02-15 14:00
      /settime 42            (opens inline date picker)
    """
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "Usage: /settime ID [time]\n"
            "Examples:\n"
            "  /settime 42 tomorrow 2pm\n"
            "  /settime 42 2026-02-15 14:00\n"
            "  /settime 42  (opens date picker)"
        )
        return

    task_id = int(context.args[0])
    task = task_service.get_task(task_id)
    if not task:
        await update.message.reply_text(f"Task #{task_id} not found.")
        return

    time_text = " ".join(context.args[1:]).strip()

    if not time_text:
        # No time given → open inline date picker
        now = get_local_now()
        keyboard = time_picker_date_keyboard(task_id, now.year, now.month)
        await update.message.reply_text(
            f"*Set deadline for #{task_id}*\nPick a date:",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return

    deadline = _parse_natural_time(time_text)
    if not deadline:
        await update.message.reply_text(
            f"Could not parse time: {time_text}\n"
            "Try: tomorrow 2pm, 2026-02-15 14:00, today 9:30am"
        )
        return

    task = task_service.update_task(
        task_id, TaskUpdate(deadline=deadline), source="telegram"
    )

    # Push to Google Calendar
    cal_msg = ""
    try:
        from roost.gmail.calendar_write import create_event_from_task
        event_id = create_event_from_task(task)
        if event_id:
            cal_msg = "\nPushed to Google Calendar."
    except Exception:
        cal_msg = "\n(Calendar push failed — check Gmail config)"

    await update.message.reply_text(
        f"Deadline set for #{task_id}: {deadline.strftime('%Y-%m-%d %H:%M')}{cal_msg}"
    )


# ── Quick commands (unchanged) ──────────────────────────────────────

@authorized
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /add Task title here")
        return

    title = " ".join(context.args)
    task = task_service.create_task(TaskCreate(title=title), source="telegram")
    await update.message.reply_text(f"Created #{task.id}: {escape_md(task.title)}", parse_mode="Markdown")


@authorized
async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /done today — redirect to activity log
    if context.args and context.args[0].lower() == "today":
        from roost.bot.handlers.triage import cmd_done_today
        return await cmd_done_today(update, context)

    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /done ID\n/done today — see today's activity")
        return

    task_id = int(context.args[0])
    task = task_service.complete_task(task_id, source="telegram")
    if task:
        celebration = task_service.get_celebration()
        await update.message.reply_text(f"#{task.id} {escape_md(task.title)}\n{celebration}")
    else:
        await update.message.reply_text(f"Task #{task_id} not found.")


# ── Notes ────────────────────────────────────────────────────────────

@authorized
async def cmd_note(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /note Your note here\n/note #tag Your note here")
        return

    args = list(context.args)
    tag = ""
    if args[0].startswith("#"):
        tag = args.pop(0).lstrip("#")

    if not args:
        await update.message.reply_text("Note content can't be empty.")
        return

    content = " ".join(args)
    note = task_service.create_note(NoteCreate(content=content, tag=tag))
    tag_str = f" [{tag}]" if tag else ""
    await update.message.reply_text(f"Noted #{note.id}{tag_str}: {content[:80]}")


@authorized
async def cmd_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tag = None
    if context.args and context.args[0].startswith("#"):
        tag = context.args[0].lstrip("#")

    notes = task_service.list_notes(tag=tag, limit=20)
    if not notes:
        await update.message.reply_text("No notes found.")
        return

    lines = []
    for n in notes:
        tag_str = f" [{n.tag}]" if n.tag else ""
        date = n.created_at.split(" ")[0]
        lines.append(f"#{n.id}{tag_str} {n.display_title}\n  _{date}_")

    text = "\n\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."
    await update.message.reply_text(text, parse_mode="Markdown")


@authorized
async def cmd_delnote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /delnote ID")
        return

    note_id = int(context.args[0])
    if task_service.delete_note(note_id):
        await update.message.reply_text(f"Deleted note #{note_id}")
    else:
        await update.message.reply_text(f"Note #{note_id} not found.")


# ── Audit Log ────────────────────────────────────────────────────────

@authorized
async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE):
    entries = task_service.get_command_log(limit=10)
    if not entries:
        await update.message.reply_text("No command history.")
        return

    lines = []
    for e in entries:
        lines.append(f"`{e.created_at}` {e.source}: {e.command[:60]}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
