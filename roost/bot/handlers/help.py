"""Help and start command handlers with inline keyboard menu."""

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.keyboards import help_menu_keyboard, help_back_keyboard

# ── Category content ─────────────────────────────────────────────────

HELP_CONTENT = {
    "triage": (
        "*Smart Triage*\n\n"
        "/today — Morning briefing: what needs attention\n"
        "/urgent [N] — Top N tasks by urgency score\n"
        "/resume — What was I working on? (in-progress + context)\n"
        "/wip ID [note] — Mark task in-progress + set context\n"
        "/lowenergy — Low-energy tasks for bad days"
    ),
    "tasks": (
        "*Tasks*\n\n"
        "/tasks — List active tasks (paginated)\n"
        "/add Title — Quick-add a task\n"
        "/done ID — Mark task complete\n"
        "/show ID — Show task details + actions\n"
        "/settime ID [time] — Set task deadline"
    ),
    "calendar": (
        "*Calendar*\n\n"
        "/cal — Today's calendar + task deadlines\n"
        "/calexport — Download task deadlines as .ics\n"
        "/settime ID [time] — Set task deadline + push to calendar\n"
        "/block TIME DESCRIPTION — Block a calendar slot"
    ),
    "focus": (
        "*Focus & Projects*\n\n"
        "/entities — List entities (companies/orgs)\n"
        "/entity NAME — Entity detail: projects + people\n"
        "/addentity NAME — Create entity\n"
        "/projects — Projects grouped by entity\n"
        "/project NAME — Project detail + people\n"
        "/addproject NAME — Create project\n"
        "/focus [project] — Show one project's tasks\n"
        "/parking [project] — Pause/resume a project"
    ),
    "people": (
        "*People & Assignments*\n\n"
        "/contacts — List contacts by entity\n"
        "/contact NAME — Contact detail + affiliations\n"
        "/addcontact NAME — Create contact\n"
        "/assign PROJECT PERSON — Assign with role picker\n"
        "/unassign ID — Remove assignment\n"
        "/roles — List role definitions\n"
        "/addrole CODE Label — Add role"
    ),
    "notes": (
        "*Notes*\n\n"
        "/note Your note — Jot a note\n"
        "/notes — Recent notes\n"
        "/delnote ID — Delete a note"
    ),
    "capture": (
        "*Rapid Capture*\n\n"
        "/capture tasks — Brain-dump tasks (one per message)\n"
        "/capture notes — Brain-dump notes\n"
        "/capture subtasks ID — Add subtasks to a parent\n"
        "/capture wip ID — Log WIP entries\n"
        "/capture stop — End session + see recap\n\n"
        "_Tip: Use inline buttons to switch modes or stop._"
    ),
    "currai": (
        "*Curriculum AI*\n\n"
        "/gem PROMPT — Continue Gemini session\n"
        "/new PROMPT — Fresh session\n"
        "/lesson TOPIC — Lesson plan\n"
        "/outline TOPIC — Course outline\n"
        "/doc PROMPT — General doc\n"
        "/refine INST — Iterate on last output"
    ),
    "currkb": (
        "*Curriculum KB*\n\n"
        "/modules — All modules\n"
        "/module M5 — Module overview\n"
        "/labguide M5 — Lab guide\n"
        "/assessment M5 — Assessment rubric\n"
        "/template M5 — Task tree for module\n"
        "/progress [proj] — Progress bar\n"
        "/standards — Frameworks\n"
        "/status — Module dev overview\n"
        "/export M5 — Compile module docs\n"
        "/curricula — List/scan curricula"
    ),
    "email": (
        "*Email Triage*\n\n"
        "/inbox — Triage unread inbox (last 10)\n"
        "/inbox QUERY — Custom Gmail query\n"
        "/inbox stop — End session + recap\n\n"
        "_Browse emails with inline buttons:_\n"
        "  View — read full thread\n"
        "  Reply — type a manual reply\n"
        "  AI Draft — Gemini-powered reply\n"
        "  Archive — remove from inbox\n"
        "  Task — create task from email"
    ),
    "presentations": (
        "*Presentations & Notes*\n\n"
        "/deck PROMPT — Generate a .pptx presentation\n"
        "/deck from notes: TEXT — Deck from meeting notes\n"
        "/mnotes TEXT — Structure raw meeting notes\n\n"
        "_Voice notes also work — say \"deck: topic\" to generate from voice._"
    ),
    "integrations": (
        "*Integrations*\n\n"
        "/gmail — Gmail status/digest/sync/poll\n"
        "/notion — Notion status/export/sync\n"
        "/otter — Dropbox + Otter.ai status\n"
        "/voice — Voice transcription status\n"
        "/share [project] — Create/list share links\n"
        "/team — Manage team members\n"
        "/tutorial — Interactive getting started guide"
    ),
    "files": (
        "*Files & Git*\n\n"
        "/send PATH — Send file\n"
        "/ls PATH — List directory\n"
        "/gdrive ls|get|put|send PATH\n"
        "/git status|log|diff\n"
        "/commit MSG\n"
        "/log — Audit log"
    ),
}


# ── Command handlers ─────────────────────────────────────────────────

@authorized
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message with quick-start suggestions, then show help grid."""
    name = update.effective_user.first_name or "there"
    text = (
        f"*Welcome, {name}!*\n\n"
        "Here are some commands to get started:\n"
        "/tasks — see your task list\n"
        "/add Buy milk — quick-add a task\n"
        "/cal — today's calendar\n"
        "/inbox — triage your email\n"
        "/briefing — morning overview\n\n"
        "Tap a category below for more:"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=help_menu_keyboard(),
    )


@authorized
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send categorized help menu with inline buttons."""
    text = "*Roost Bot*\n\nTap a category to see commands:"
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=help_menu_keyboard(),
    )


# ── Callback handler ────────────────────────────────────────────────

async def handle_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle help:* callback queries — show category or return to menu."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    category = parts[1] if len(parts) > 1 else "menu"

    if category == "menu":
        text = "*Roost Bot*\n\nTap a category to see commands:"
        await query.edit_message_text(
            text, parse_mode="Markdown", reply_markup=help_menu_keyboard(),
        )
    elif category in HELP_CONTENT:
        await query.edit_message_text(
            HELP_CONTENT[category],
            parse_mode="Markdown",
            reply_markup=help_back_keyboard(),
        )
    else:
        await query.edit_message_text("Unknown category.")
