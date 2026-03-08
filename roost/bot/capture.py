"""Rapid capture mode — enter a mode, every message creates an item until you stop.

Usage:
    /capture tasks       — enter task capture mode
    /capture notes       — enter note capture mode
    /capture subtasks ID — enter subtask mode for a parent task
    /capture wip ID      — enter WIP log mode for a task
    /capture stop        — end session and show recap
"""

import logging
import time
from dataclasses import dataclass, field

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost import task_service
from roost.models import TaskCreate, NoteCreate

logger = logging.getLogger(__name__)

# ── Session state ────────────────────────────────────────────────────

@dataclass
class CaptureSession:
    mode: str                          # "tasks" | "notes" | "subtasks" | "wip"
    chat_id: int
    target_id: int | None = None       # parent task ID for subtasks/wip
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    items_created: list = field(default_factory=list)  # [(type, id, title)]
    timeout_seconds: int = 600         # 10 minutes


_sessions: dict[int, CaptureSession] = {}


def start_session(user_id: int, chat_id: int, mode: str, target_id: int | None = None) -> CaptureSession:
    """Create or replace a capture session for a user."""
    session = CaptureSession(mode=mode, chat_id=chat_id, target_id=target_id)
    _sessions[user_id] = session
    return session


def get_session(user_id: int) -> CaptureSession | None:
    """Return active session or None.  Auto-cleans if timed out."""
    session = _sessions.get(user_id)
    if session is None:
        return None
    if time.time() - session.last_activity > session.timeout_seconds:
        _sessions.pop(user_id, None)
        return None
    return session


def touch_session(user_id: int) -> None:
    """Bump last_activity timestamp."""
    session = _sessions.get(user_id)
    if session:
        session.last_activity = time.time()


def stop_session(user_id: int) -> CaptureSession | None:
    """Remove and return session for recap."""
    return _sessions.pop(user_id, None)


# ── Inline keyboard ─────────────────────────────────────────────────

def capture_control_keyboard(session: CaptureSession) -> InlineKeyboardMarkup:
    """Build inline keyboard showing active mode and stop button."""
    tasks_label = ">Tasks" if session.mode == "tasks" else "Tasks"
    notes_label = ">Notes" if session.mode == "notes" else "Notes"
    count = len(session.items_created)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(tasks_label, callback_data="cap:m:tasks"),
            InlineKeyboardButton(notes_label, callback_data="cap:m:notes"),
        ],
        [
            InlineKeyboardButton(f"Stop ({count} captured)", callback_data="cap:stop"),
        ],
    ])


def _recap_text(session: CaptureSession) -> str:
    """Build a recap message from a finished session."""
    count = len(session.items_created)
    elapsed = int(time.time() - session.started_at)
    mins = elapsed // 60
    secs = elapsed % 60

    lines = [f"*Capture complete* — {count} item{'s' if count != 1 else ''} in {mins}m{secs}s\n"]
    for item_type, item_id, title in session.items_created:
        icon = "📝" if item_type == "note" else "✅"
        lines.append(f"{icon} #{item_id} {title}")
    return "\n".join(lines)


MODE_LABELS = {
    "tasks": "task",
    "notes": "note",
    "subtasks": "subtask",
    "wip": "WIP log",
}


# ── Command handler ──────────────────────────────────────────────────

@authorized
async def cmd_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/capture <mode> [id] — start rapid capture mode."""
    args = context.args or []
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if not args:
        await update.message.reply_text(
            "*Rapid Capture*\n\n"
            "`/capture tasks` — capture tasks\n"
            "`/capture notes` — capture notes\n"
            "`/capture subtasks ID` — subtasks for a task\n"
            "`/capture wip ID` — WIP log entries\n"
            "`/capture stop` — end session",
            parse_mode="Markdown",
        )
        return

    mode = args[0].lower()

    # Stop
    if mode == "stop":
        session = stop_session(user_id)
        if not session:
            await update.message.reply_text("No active capture session.")
            return
        await update.message.reply_text(_recap_text(session), parse_mode="Markdown")
        return

    # Validate mode
    if mode not in ("tasks", "notes", "subtasks", "wip"):
        await update.message.reply_text(
            f"Unknown mode `{mode}`. Use: tasks, notes, subtasks, wip",
            parse_mode="Markdown",
        )
        return

    # Modes requiring a target task ID
    target_id = None
    if mode in ("subtasks", "wip"):
        if len(args) < 2:
            await update.message.reply_text(f"Usage: `/capture {mode} <task_id>`", parse_mode="Markdown")
            return
        try:
            target_id = int(args[1])
        except ValueError:
            await update.message.reply_text("Task ID must be a number.")
            return
        task = task_service.get_task(target_id)
        if not task:
            await update.message.reply_text(f"Task #{target_id} not found.")
            return

    session = start_session(user_id, chat_id, mode, target_id)
    label = MODE_LABELS.get(mode, mode)

    target_info = ""
    if target_id:
        task = task_service.get_task(target_id)
        target_info = f" for *#{target_id}* {task.title}" if task else f" for #{target_id}"

    await update.message.reply_text(
        f"*Capture mode: {label}*{target_info}\n\n"
        f"Every message you send will create a {label}.\n"
        f"Use /capture stop or tap Stop to end.\n"
        f"Timeout: {session.timeout_seconds // 60}min inactivity.",
        parse_mode="Markdown",
        reply_markup=capture_control_keyboard(session),
    )


# ── Message handler (group -1) ──────────────────────────────────────

async def handle_capture_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercept plain text messages when a capture session is active.

    Registered in group -1 so it runs before default handlers.
    If no session is active, returns immediately to let other handlers proceed.
    """
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id
    session = get_session(user_id)
    if session is None:
        # Check if session existed but timed out
        if user_id in _sessions:
            old = _sessions.pop(user_id, None)
            if old:
                await update.message.reply_text(
                    f"Capture session timed out after {old.timeout_seconds // 60}min.\n\n"
                    + _recap_text(old),
                    parse_mode="Markdown",
                )
        return  # No session — let other handlers process this message

    text = update.message.text.strip()
    if not text:
        return

    touch_session(user_id)

    try:
        if session.mode == "tasks":
            task = task_service.create_task(TaskCreate(title=text), source="telegram")
            session.items_created.append(("task", task.id, text))
            icon = "✅"
            confirm = f"{icon} Task #{task.id}"

        elif session.mode == "notes":
            note = task_service.create_note(NoteCreate(content=text), source="telegram")
            session.items_created.append(("note", note.id, text[:50]))
            icon = "📝"
            confirm = f"{icon} Note #{note.id}"

        elif session.mode == "subtasks":
            task = task_service.create_task(
                TaskCreate(title=text, parent_task_id=session.target_id, task_type="subtask"),
                source="telegram",
            )
            session.items_created.append(("subtask", task.id, text))
            icon = "✅"
            confirm = f"{icon} Subtask #{task.id}"

        elif session.mode == "wip":
            result = task_service.mark_wip(session.target_id, context=text)
            if result:
                session.items_created.append(("wip", session.target_id, text[:50]))
                icon = "🔧"
                confirm = f"{icon} WIP logged"
            else:
                await update.message.reply_text("Failed to log WIP — task not found.")
                return

        else:
            return

    except Exception:
        logger.exception("Capture: failed to create item")
        await update.message.reply_text("Failed to create item. Try again or /capture stop.")
        return

    count = len(session.items_created)
    await update.message.reply_text(
        f"{confirm} ({count} total)",
        reply_markup=capture_control_keyboard(session),
    )

    # Stop propagation — prevent other handlers from processing this message
    raise _StopPropagation()


class _StopPropagation(Exception):
    """Signal to python-telegram-bot to stop handler propagation.

    python-telegram-bot's ApplicationHandlerStop is the canonical way,
    but we import it properly here.
    """
    pass


# Replace with the real one at import time
try:
    from telegram.ext import ApplicationHandlerStop as _StopPropagation  # noqa: F811
except ImportError:
    pass


# ── Callback handler ────────────────────────────────────────────────

async def handle_capture_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cap:* callback queries — mode switch or stop."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id

    parts = data.split(":")
    # cap:m:<mode> or cap:stop
    if len(parts) >= 2 and parts[1] == "stop":
        session = stop_session(user_id)
        if session:
            await query.edit_message_text(_recap_text(session), parse_mode="Markdown")
        else:
            await query.edit_message_text("No active capture session.")
        return

    if len(parts) >= 3 and parts[1] == "m":
        new_mode = parts[2]
        session = get_session(user_id)
        if not session:
            await query.edit_message_text("No active capture session.")
            return
        if new_mode in ("tasks", "notes"):
            session.mode = new_mode
            session.target_id = None
            touch_session(user_id)
            label = MODE_LABELS.get(new_mode, new_mode)
            await query.edit_message_text(
                f"Switched to *{label}* capture. Keep typing!",
                parse_mode="Markdown",
                reply_markup=capture_control_keyboard(session),
            )
        else:
            await query.answer("Use /capture command for that mode.")
