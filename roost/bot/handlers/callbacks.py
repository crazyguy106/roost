"""Central callback query router.

Parses the prefix from callback_data and dispatches to
the appropriate handler module.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.handlers.common import escape_md
from roost.bot.tz import get_local_now

logger = logging.getLogger(__name__)


@authorized
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route callback queries by data prefix."""
    query = update.callback_query
    data = query.data or ""

    if data == "noop":
        await query.answer()
        return

    prefix = data.split(":")[0]

    if prefix == "help":
        from roost.bot.handlers.help import handle_help_callback
        await handle_help_callback(update, context)

    elif prefix == "tasks":
        from roost.bot.handlers.tasks import handle_tasks_callback
        await handle_tasks_callback(update, context)

    elif prefix == "task":
        from roost.bot.handlers.tasks import handle_task_action_callback
        await handle_task_action_callback(update, context)

    elif prefix == "settime":
        from roost.bot.handlers.tasks import handle_settime_callback
        await handle_settime_callback(update, context)

    elif prefix == "role":
        from roost.bot.handlers.projects import handle_role_callback
        await handle_role_callback(update, context)

    elif prefix == "cap":
        from roost.bot.capture import handle_capture_callback
        await handle_capture_callback(update, context)

    elif prefix == "email":
        from roost.bot.handlers.email_triage import handle_email_callback
        await handle_email_callback(update, context)

    elif prefix == "daily":
        await _handle_daily_callback(update, context)

    elif prefix == "shutdown":
        await _handle_shutdown_callback(update, context)

    elif prefix == "next":
        await _handle_next_callback(update, context)

    elif prefix == "pick":
        await _handle_pick_callback(update, context)

    elif prefix == "timer":
        await _handle_timer_callback(update, context)

    elif prefix == "checkin":
        await _handle_checkin_callback(update, context)

    elif prefix == "routine":
        await _handle_routine_callback(update, context)

    elif prefix == "spoons":
        await _handle_spoons_callback(update, context)

    elif prefix == "tutorial":
        from roost.bot.handlers.tutorial import handle_tutorial_callback
        await handle_tutorial_callback(update, context)

    elif prefix == "skill":
        from roost.bot.handlers.skill_builder import handle_skill_callback
        await handle_skill_callback(update, context)

    else:
        logger.warning("Unknown callback prefix: %s (full data: %s)", prefix, query.data)
        await query.answer("Unknown action.")


async def _handle_daily_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle daily:<action>:<id> callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    from roost import task_service

    if action == "rm" and len(parts) > 2:
        tid = int(parts[2])
        task_service.clear_focus(tid)
        await query.answer(f"Removed #{tid} from focus")
        # Refresh: show updated focus list
        focused = task_service.get_focus_tasks()
        if focused:
            lines = [f"*Daily Focus ({len(focused)}/3):*\n"]
            for t in focused:
                lines.append(f"  #{t.id} {escape_md(t.title)}")
            from roost.bot.keyboards import focus_keyboard
            await query.edit_message_text(
                "\n".join(lines), parse_mode="Markdown",
                reply_markup=focus_keyboard([t.id for t in focused]),
            )
        else:
            await query.edit_message_text("Focus list cleared.")

    elif action == "clear":
        count = task_service.clear_focus()
        await query.answer(f"Cleared {count} focus tasks")
        await query.edit_message_text("Focus list cleared.")

    else:
        await query.answer("Unknown daily action.")


async def _handle_shutdown_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shutdown:confirm/cancel callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    from roost import task_service

    if action == "confirm":
        result = task_service.execute_shutdown()
        await query.answer("Day paused.")
        await query.edit_message_text(
            f"Day paused.\n"
            f"- {result['paused_count']} task(s) paused\n"
            f"- {result['deferred_count']} deadline(s) deferred\n\n"
            f"Use /resumeday to resume."
        )

    elif action == "cancel":
        await query.answer("Cancelled.")
        await query.edit_message_text("Shutdown cancelled.")


async def _handle_next_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle next:start:ID and next:skip callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    from roost import task_service

    if action == "start" and len(parts) > 2:
        tid = int(parts[2])
        task_service.mark_wip(tid)
        await query.answer(f"Started #{tid}!")
        task = task_service.get_task(tid)
        if task:
            await query.edit_message_text(f"Working on: #{task.id} {task.title}")
        return

    if action == "skip":
        # Get next task after current best
        task = task_service.get_next_task()
        if task:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Start", callback_data=f"next:start:{task.id}"),
                    InlineKeyboardButton("Skip", callback_data="next:skip"),
                ],
            ])
            effort = f" [{task.effort_estimate}]" if task.effort_estimate != "moderate" else ""
            await query.answer()
            await query.edit_message_text(
                f"*Right now, just this one thing:*\n\n"
                f"#{task.id} {task.title}{effort}",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        else:
            await query.answer("No more tasks!")
            await query.edit_message_text("No active tasks. Enjoy the calm!")
        return

    await query.answer("Unknown action.")


async def _handle_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pick:go:ID and pick:again:energy callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    from roost import task_service

    if action == "go" and len(parts) > 2:
        tid = int(parts[2])
        task_service.mark_wip(tid)
        await query.answer(f"Let's go! Started #{tid}")
        task = task_service.get_task(tid)
        if task:
            await query.edit_message_text(f"Working on: #{task.id} {task.title}")
        return

    if action == "again" and len(parts) > 2:
        energy = parts[2] if parts[2] != "all" else None
        task = task_service.pick_task(energy_budget=energy)
        if task:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            effort = f" [{task.effort_estimate}]" if task.effort_estimate != "moderate" else ""
            proj = f" [{task.project_name}]" if task.project_name else ""
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Let's go!", callback_data=f"pick:go:{task.id}"),
                    InlineKeyboardButton("Pick again", callback_data=f"pick:again:{energy or 'all'}"),
                ],
            ])
            await query.answer()
            await query.edit_message_text(
                f"*How about this one?*\n\n"
                f"#{task.id} {task.title}{proj}{effort}",
                parse_mode="Markdown",
                reply_markup=keyboard,
            )
        else:
            await query.answer("No matching tasks!")
            await query.edit_message_text("No tasks to pick from.")
        return

    await query.answer("Unknown action.")


async def _handle_timer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle timer:break, timer:another, timer:stop callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    from roost.bot.pomodoro import start_break, start_another_round, stop_timer

    chat_id = update.effective_chat.id

    if action == "break":
        state = await start_break(chat_id, context)
        if state:
            await query.answer("Break started!")
            await query.edit_message_text(
                f"Break time! {state.break_duration} minutes. Relax."
            )
        else:
            await query.answer("No active timer.")
        return

    if action == "another":
        state = await start_another_round(chat_id, context)
        if state:
            task_info = f' on "{state.task_title}"' if state.task_title else ""
            await query.answer("New round started!")
            await query.edit_message_text(
                f"Round started! {state.duration} minutes{task_info}."
            )
        else:
            await query.answer("No active timer.")
        return

    if action == "stop":
        state = await stop_timer(chat_id, context)
        if state:
            elapsed = (
                get_local_now() - state.started_at
            ).total_seconds() / 60
            await query.answer("Timer stopped.")
            await query.edit_message_text(f"Timer stopped. {elapsed:.0f}min elapsed.")
        else:
            await query.answer("No active timer.")
        return

    await query.answer("Unknown action.")


async def _handle_checkin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle checkin:yes, checkin:switch, checkin:snooze callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "yes":
        await query.answer("Great, carry on!")
        await query.edit_message_text("Carrying on. You've got this!")

    elif action == "switch":
        from roost import task_service
        tasks = task_service.list_tasks(
            status="todo", order_by="urgency", limit=3,
            exclude_paused_projects=True,
        )
        if tasks:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            buttons = [
                [InlineKeyboardButton(
                    f"#{t.id} {t.title[:30]}",
                    callback_data=f"next:start:{t.id}",
                )]
                for t in tasks
            ]
            await query.answer()
            await query.edit_message_text(
                "*Switch to:*",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        else:
            await query.answer("No tasks to switch to.")

    elif action == "snooze":
        await query.answer("Snoozed for 1h.")
        await query.edit_message_text("Snoozed. I'll check in again later.")

    elif action == "break":
        await query.answer("Taking a break!")
        await query.edit_message_text("Break time. Take care of yourself.")

    else:
        await query.answer("Unknown action.")


async def _handle_routine_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle routine:check:ID:name and routine:uncheck:ID:name callback queries."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    item_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    routine_name = parts[3] if len(parts) > 3 else "morning"

    from roost import task_service

    if action == "check" and item_id:
        task_service.complete_routine_item(item_id)
        await query.answer("Checked!")
    elif action == "uncheck" and item_id:
        task_service.uncomplete_routine_item(item_id)
        await query.answer("Unchecked!")
    else:
        await query.answer("Unknown action.")
        return

    # Refresh routine view
    routine = task_service.get_routine(routine_name)
    if not routine or not routine["items"]:
        await query.edit_message_text(f"{routine_name.title()} routine is empty.")
        return

    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    lines = [f"*{routine_name.title()} Routine:*\n"]
    buttons = []
    for item in routine["items"]:
        check = "x" if item["completed"] else " "
        lines.append(f"  [{check}] {item['title']}")
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

    rows = [[b] for b in buttons]
    keyboard = InlineKeyboardMarkup(rows) if rows else None

    await query.edit_message_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=keyboard,
    )


async def _handle_spoons_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle spoons:reset callback."""
    query = update.callback_query
    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""

    from roost import task_service

    if action == "reset":
        status = task_service.reset_spoons()
        await query.answer("Spoons reset!")
        await query.edit_message_text(
            f"Spoons reset! Budget: {status['budget']}/{status['budget']}"
        )
    else:
        await query.answer("Unknown action.")
