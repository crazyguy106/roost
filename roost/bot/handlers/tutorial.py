"""Interactive getting started tutorial via inline keyboard navigation."""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from roost.bot.security import authorized

TUTORIAL_SECTIONS = {
    "tasks": (
        "*Tasks & Projects*\n\n"
        "/add Buy groceries — quick-add a task\n"
        "/tasks — browse your task list\n"
        "/done 42 — mark task #42 complete\n"
        "/show 42 — view task details + actions\n"
        "/projects — see projects grouped by entity\n\n"
        "_Tip: Use /capture tasks for rapid brain-dump mode._"
    ),
    "calendar": (
        "*Calendar*\n\n"
        "/cal — today's events + task deadlines\n"
        "/block 14:00 Team standup — create a calendar event\n"
        "/settime 42 — set a deadline for task #42\n\n"
        "_Your calendar source depends on your instance: "
        "Google Calendar or Microsoft 365._"
    ),
    "email": (
        "*Email Triage*\n\n"
        "/inbox — browse your latest unread emails\n\n"
        "Use the inline buttons to:\n"
        "  *View* — read the full thread\n"
        "  *Reply* — type a manual reply\n"
        "  *AI Draft* — get a Gemini-powered reply suggestion\n"
        "  *Archive* — clean up your inbox\n"
        "  *Task* — create a task from the email\n\n"
        "_Works with Gmail or Microsoft Outlook depending on your setup._"
    ),
    "present": (
        "*Presentations & Voice*\n\n"
        "/deck Company overview — generate a .pptx presentation\n"
        "/mnotes [paste notes] — structure raw meeting notes\n\n"
        "*Voice notes:* Send a voice message and it's transcribed automatically.\n"
        "Prefix routing:\n"
        "  \"task: ...\" — creates a task\n"
        "  \"note: ...\" — saves a note\n"
        "  \"deck: ...\" — generates a presentation\n"
        "  \"meeting: ...\" — structures meeting notes"
    ),
    "daily": (
        "*Daily Routines*\n\n"
        "/briefing — morning overview: calendar + tasks + overdue\n"
        "/today — smart triage: what needs attention\n"
        "/next — just one thing: the single best task to do now\n"
        "/spoons — track your energy budget\n"
        "/routine — morning/evening checklists\n"
        "/shutdown — pause all work for the day\n\n"
        "_Designed for focus and energy management._"
    ),
    "tips": (
        "*Tips & Tricks*\n\n"
        "/help — full command reference with interactive menu\n"
        "/capture tasks — brain-dump mode: one task per message\n"
        "/pick — random task picker when you can't decide\n"
        "/focus — show tasks for one project only\n\n"
        "*Web app:*\n"
        "  Pull to refresh on any page\n"
        "  Add to home screen for app-like experience\n"
        "  Gear icon in top bar for settings"
    ),
}

TUTORIAL_MENU_TEXT = (
    "*Welcome to Roost!*\n\n"
    "Your personal productivity hub. Tap a topic below to learn more:"
)


def _tutorial_menu_keyboard() -> InlineKeyboardMarkup:
    """Build the tutorial menu keyboard — 2 buttons per row."""
    buttons = [
        ("Tasks & Projects", "tutorial:tasks"),
        ("Calendar", "tutorial:calendar"),
        ("Email Triage", "tutorial:email"),
        ("Presentations", "tutorial:present"),
        ("Daily Routines", "tutorial:daily"),
        ("Tips & Tricks", "tutorial:tips"),
    ]
    rows = []
    for i in range(0, len(buttons), 2):
        row = [
            InlineKeyboardButton(label, callback_data=data)
            for label, data in buttons[i:i + 2]
        ]
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _tutorial_back_keyboard() -> InlineKeyboardMarkup:
    """Single 'Back to menu' button."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("\u2b05 Back to menu", callback_data="tutorial:menu")]
    ])


@authorized
async def cmd_tutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send interactive tutorial menu."""
    await update.message.reply_text(
        TUTORIAL_MENU_TEXT,
        parse_mode="Markdown",
        reply_markup=_tutorial_menu_keyboard(),
    )


async def handle_tutorial_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle tutorial:* callback queries — show section or return to menu."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    section = parts[1] if len(parts) > 1 else "menu"

    if section == "menu":
        await query.edit_message_text(
            TUTORIAL_MENU_TEXT,
            parse_mode="Markdown",
            reply_markup=_tutorial_menu_keyboard(),
        )
    elif section in TUTORIAL_SECTIONS:
        await query.edit_message_text(
            TUTORIAL_SECTIONS[section],
            parse_mode="Markdown",
            reply_markup=_tutorial_back_keyboard(),
        )
    else:
        await query.edit_message_text("Unknown section.")
