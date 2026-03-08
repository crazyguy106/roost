"""Interactive email triage via Telegram — browse, reply, AI-draft, archive.

Usage:
    /inbox           — triage unread inbox (last 10)
    /inbox <query>   — custom Gmail query (e.g. /inbox from:harun)
    /inbox stop      — end session with recap

Session model follows capture.py's pattern: per-user in-memory dict with
auto-timeout. Callbacks use the `email:` prefix.
"""

import logging
import re
import time
from dataclasses import dataclass, field

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from roost.bot.security import authorized

logger = logging.getLogger("roost.bot.email_triage")

# Telegram message length limit (leave margin)
TG_MAX_LEN = 4000

# ── Session state ────────────────────────────────────────────────────


@dataclass
class EmailTriageSession:
    chat_id: int
    provider: str = "gmail"  # "gmail" or "microsoft"
    mode: str = "browsing"  # "browsing" | "reading" | "replying" | "ai_prompting"
    email_queue: list = field(default_factory=list)  # message dicts from search_messages
    current_index: int = 0
    current_thread_id: str | None = None
    current_thread: dict | None = None  # cached thread data
    draft: dict | None = None  # {to, subject, body, cc, thread_id}
    actions_taken: list = field(default_factory=list)  # [(action, thread_id, summary)]
    started_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    timeout_seconds: int = 600  # 10 minutes


_sessions: dict[int, EmailTriageSession] = {}


def start_triage(user_id: int, chat_id: int, emails: list) -> EmailTriageSession:
    """Create or replace a triage session."""
    session = EmailTriageSession(chat_id=chat_id, email_queue=emails)
    _sessions[user_id] = session
    return session


def get_triage(user_id: int) -> EmailTriageSession | None:
    """Return active session or None. Auto-cleans if timed out."""
    session = _sessions.get(user_id)
    if session is None:
        return None
    if time.time() - session.last_activity > session.timeout_seconds:
        _sessions.pop(user_id, None)
        return None
    return session


def touch_triage(user_id: int) -> None:
    """Bump last_activity timestamp."""
    session = _sessions.get(user_id)
    if session:
        session.last_activity = time.time()


def stop_triage(user_id: int) -> EmailTriageSession | None:
    """Remove and return session for recap."""
    return _sessions.pop(user_id, None)


# ── Formatting helpers ───────────────────────────────────────────────


def _extract_email_address(from_header: str) -> str:
    """Extract bare email from 'Name <email>' format."""
    match = re.search(r"<([^>]+)>", from_header)
    return match.group(1) if match else from_header


def _extract_name(from_header: str) -> str:
    """Extract display name from 'Name <email>' format."""
    match = re.match(r"^([^<]+)<", from_header)
    if match:
        return match.group(1).strip().strip('"')
    return from_header


def _format_email_preview(msg: dict, index: int, total: int, provider: str = "") -> str:
    """Format a single email for Telegram display (browsing mode).

    Args:
        provider: "gmail", "microsoft", or "" for generic header.
    """
    sender = msg.get("from", "Unknown")
    subject = msg.get("subject", "(no subject)")
    snippet = msg.get("snippet", "")
    date = msg.get("date", "")

    # Truncate long snippets
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."

    # Clean up date — just show the essential part
    if "T" in date:
        # ISO format from MS Graph: "2026-02-19T10:30:00Z" → "2026-02-19 10:30"
        date_short = date[:16].replace("T", " ")
    elif date:
        # Gmail format: "Mon, 3 Feb 2026 10:30:00 +0800"
        date_short = date.split("+")[0].split("-0")[0].strip()
    else:
        date_short = ""

    # Provider-aware header
    if provider == "gmail":
        header = "Gmail Triage"
    elif provider == "microsoft":
        header = "Outlook Triage"
    else:
        header = "Inbox Triage"

    return (
        f"*{header}* ({index + 1}/{total})\n\n"
        f"*From:* {_escape_md(sender)}\n"
        f"*Subject:* {_escape_md(subject)}\n"
        f"*Date:* {_escape_md(date_short)}\n\n"
        f"_{_escape_md(snippet)}_"
    )


def _format_thread_view(thread: dict) -> str:
    """Format a thread for reading in Telegram (last 2 messages)."""
    messages = thread.get("messages", [])
    if not messages:
        return "Empty thread."

    # Show last 2 messages
    recent = messages[-2:] if len(messages) > 2 else messages
    lines = [f"*Thread* ({thread.get('message_count', len(messages))} messages)\n"]

    for msg in recent:
        sender = msg.get("from", "Unknown")
        date = msg.get("date", "")
        if "T" in date:
            # ISO format from MS Graph: "2026-02-19T10:30:00Z" → "2026-02-19 10:30"
            date_short = date[:16].replace("T", " ")
        elif date:
            # Gmail format: "Mon, 3 Feb 2026 10:30:00 +0800"
            date_short = date.split("+")[0].split("-0")[0].strip()
        else:
            date_short = ""
        body = msg.get("body", "")

        # Truncate body for Telegram
        max_body = 1500 if len(recent) == 1 else 800
        if len(body) > max_body:
            body = body[:max_body] + "\n[... truncated ...]"

        lines.append(
            f"---\n"
            f"*{_escape_md(_extract_name(sender))}* ({_escape_md(date_short)})\n\n"
            f"{_escape_md(body)}"
        )

    full_text = "\n".join(lines)
    # Hard cap for Telegram
    if len(full_text) > TG_MAX_LEN:
        full_text = full_text[:TG_MAX_LEN - 20] + "\n\n[... truncated ...]"
    return full_text


def _format_draft(draft: dict) -> str:
    """Format a draft for confirmation display."""
    lines = ["*Draft Reply:*\n"]
    lines.append(f"*To:* {_escape_md(draft['to'])}")
    if draft.get("cc"):
        lines.append(f"*Cc:* {_escape_md(draft['cc'])}")
    lines.append(f"*Subject:* {_escape_md(draft['subject'])}\n")
    lines.append(_escape_md(draft["body"]))

    full_text = "\n".join(lines)
    if len(full_text) > TG_MAX_LEN:
        full_text = full_text[:TG_MAX_LEN - 20] + "\n\n[... truncated ...]"
    return full_text


def _triage_recap(session: EmailTriageSession) -> str:
    """Build a recap message from a finished session."""
    count = len(session.actions_taken)
    total = len(session.email_queue)
    elapsed = int(time.time() - session.started_at)
    mins = elapsed // 60
    secs = elapsed % 60

    lines = [f"*Triage complete* — {count} action{'s' if count != 1 else ''} on {total} emails in {mins}m{secs}s\n"]
    for action, thread_id, summary in session.actions_taken:
        icon = {"replied": ">>", "archived": "<<", "task": "#", "skipped": "--"}.get(action, "-")
        lines.append(f"  {icon} {summary}")

    if not session.actions_taken:
        lines.append("  No actions taken.")

    return "\n".join(lines)


from roost.bot.handlers.common import escape_md as _escape_md  # noqa: E402


# ── Keyboards ────────────────────────────────────────────────────────


def _browsing_keyboard(index: int) -> InlineKeyboardMarkup:
    """Build inline keyboard for browsing mode."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("View", callback_data=f"email:view:{index}"),
            InlineKeyboardButton("Reply", callback_data=f"email:reply:{index}"),
            InlineKeyboardButton("AI Draft", callback_data=f"email:ai:{index}"),
        ],
        [
            InlineKeyboardButton("Archive", callback_data=f"email:archive:{index}"),
            InlineKeyboardButton("Task", callback_data=f"email:task:{index}"),
            InlineKeyboardButton("Skip >>", callback_data="email:skip"),
        ],
        [
            InlineKeyboardButton("<< Prev", callback_data="email:prev"),
            InlineKeyboardButton("Stop", callback_data="email:stop"),
        ],
    ])


def _reading_keyboard(index: int) -> InlineKeyboardMarkup:
    """Build inline keyboard for reading mode (viewing a thread)."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Reply", callback_data=f"email:reply:{index}"),
            InlineKeyboardButton("AI Draft", callback_data=f"email:ai:{index}"),
        ],
        [
            InlineKeyboardButton("Archive", callback_data=f"email:archive:{index}"),
            InlineKeyboardButton("Back", callback_data="email:back"),
        ],
    ])


def _draft_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for draft confirmation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Send", callback_data="email:send"),
            InlineKeyboardButton("Edit", callback_data="email:edit"),
        ],
        [
            InlineKeyboardButton("AI Draft", callback_data=f"email:ai_redraft"),
            InlineKeyboardButton("Cancel", callback_data="email:cancel"),
        ],
    ])


# ── Command handler ──────────────────────────────────────────────────


@authorized
async def cmd_inbox(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/inbox [gmail|outlook|ms] [query|stop] — start interactive email triage.

    Provider selector (first arg):
      gmail / google  → force Gmail
      outlook / ms    → force Microsoft Outlook
      (omitted)       → auto (Gmail if enabled, else MS)
    """
    args = list(context.args or [])
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Check for capture session conflict
    from roost.bot.capture import get_session as get_capture
    if get_capture(user_id):
        await update.message.reply_text(
            "You have an active /capture session. "
            "Use `/capture stop` first, then try /inbox again.",
            parse_mode="Markdown",
        )
        return

    # Stop command
    if args and args[0].lower() == "stop":
        session = stop_triage(user_id)
        if not session:
            await update.message.reply_text("No active triage session.")
            return
        await update.message.reply_text(_triage_recap(session), parse_mode="Markdown")
        return

    # Help
    if args and args[0].lower() == "help":
        await update.message.reply_text(
            "*Email Triage*\n\n"
            "`/inbox` — unread inbox (last 10)\n"
            "`/inbox gmail` — force Gmail\n"
            "`/inbox outlook` — force Outlook\n"
            "`/inbox from:harun` — custom query\n"
            "`/inbox outlook from:harun` — Outlook search\n"
            "`/inbox stop` — end session",
            parse_mode="Markdown",
        )
        return

    # Detect provider selector in first arg
    from roost.config import GOOGLE_ENABLED, GMAIL_ENABLED
    from roost.microsoft import is_microsoft_available

    force_provider = None
    if args:
        first_lower = args[0].lower()
        if first_lower in ("outlook", "ms", "microsoft"):
            force_provider = "microsoft"
            args = args[1:]
        elif first_lower in ("gmail", "google"):
            force_provider = "gmail"
            args = args[1:]

    google_ok = GOOGLE_ENABLED and GMAIL_ENABLED
    ms_ok = is_microsoft_available()
    # Determine whether to show provider-qualified header
    dual = google_ok and ms_ok

    # Resolve which provider to use
    use_provider = None
    if force_provider == "microsoft":
        if not ms_ok:
            await update.message.reply_text("Microsoft Outlook is not available on this instance.")
            return
        use_provider = "microsoft"
    elif force_provider == "gmail":
        if not google_ok:
            await update.message.reply_text("Gmail is not enabled on this instance.")
            return
        use_provider = "gmail"
    else:
        # Auto: prefer Gmail, fall back to MS
        if google_ok:
            use_provider = "gmail"
        elif ms_ok:
            use_provider = "microsoft"
        else:
            await update.message.reply_text("Email is not enabled on this instance.")
            return

    # Provider label for header (only show specific name when both are available)
    provider_label = use_provider if dual else ""

    if use_provider == "microsoft":
        query = " ".join(args) if args else "isread:false"
        status_msg = await update.message.reply_text("Searching Outlook...")
        try:
            from roost.mcp.ms_graph_helpers import search_messages as ms_search
            ms_emails = ms_search(query, max_results=10)
        except Exception as e:
            logger.exception("Failed to search MS emails")
            await status_msg.edit_text(f"Outlook error: {e}")
            return

        if not ms_emails:
            await status_msg.edit_text(
                f"No emails found for: `{query}`", parse_mode="Markdown",
            )
            return

        # Map MS fields → triage-compatible format (threadId key)
        emails = []
        for m in ms_emails:
            emails.append({**m, "threadId": m.get("conversationId", "")})

        session = start_triage(user_id, chat_id, emails)
        session.provider = "microsoft"

        # Show first email
        msg = emails[0]
        preview = _format_email_preview(msg, 0, len(emails), provider=provider_label)
        try:
            await status_msg.edit_text(
                preview,
                parse_mode="Markdown",
                reply_markup=_browsing_keyboard(0),
            )
        except Exception:
            await status_msg.edit_text(
                f"Outlook Triage ({len(emails)} emails)\n\n"
                f"From: {msg.get('from', '?')}\n"
                f"Subject: {msg.get('subject', '?')}\n"
                f"{msg.get('snippet', '')[:200]}",
                reply_markup=_browsing_keyboard(0),
            )
        return

    # Gmail path
    query = " ".join(args) if args else "is:unread label:INBOX"
    status_msg = await update.message.reply_text("Searching Gmail...")

    try:
        from roost.mcp.gmail_helpers import search_messages
        emails = search_messages(query, max_results=10)
    except Exception as e:
        logger.exception("Failed to search emails")
        await status_msg.edit_text(f"Gmail error: {e}")
        return

    if not emails:
        await status_msg.edit_text(f"No emails found for: `{query}`", parse_mode="Markdown")
        return

    # Start session
    session = start_triage(user_id, chat_id, emails)
    session.provider = "gmail"

    # Show first email
    msg = emails[0]
    preview = _format_email_preview(msg, 0, len(emails), provider=provider_label)
    try:
        await status_msg.edit_text(
            preview,
            parse_mode="Markdown",
            reply_markup=_browsing_keyboard(0),
        )
    except Exception:
        # Fallback without Markdown if escaping fails
        await status_msg.edit_text(
            f"Gmail Triage ({len(emails)} emails)\n\n"
            f"From: {msg.get('from', '?')}\n"
            f"Subject: {msg.get('subject', '?')}\n"
            f"{msg.get('snippet', '')[:200]}",
            reply_markup=_browsing_keyboard(0),
        )


# ── Message handler (group -1) ──────────────────────────────────────


async def handle_triage_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Intercept text when triage session is in replying or ai_prompting mode.

    Registered in group -1 alongside handle_capture_message.
    Returns immediately if no active triage session in the right mode.
    """
    if not update.message or not update.message.text:
        return
    user_id = update.effective_user.id
    session = get_triage(user_id)
    if session is None:
        return
    if session.mode not in ("replying", "ai_prompting"):
        return

    text = update.message.text.strip()
    if not text:
        return

    touch_triage(user_id)

    if session.mode == "replying":
        # User typed a reply body — build draft
        msg = session.email_queue[session.current_index]
        reply_to_email = _extract_email_address(msg.get("from", ""))
        subject = msg.get("subject", "")
        if not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"

        session.draft = {
            "to": reply_to_email,
            "subject": subject,
            "body": text,
            "cc": "",
            "thread_id": msg.get("threadId", ""),
        }
        session.mode = "browsing"  # Back to browsing while showing draft

        await update.message.reply_text(
            _format_draft(session.draft),
            parse_mode="Markdown",
            reply_markup=_draft_keyboard(),
        )

        # Stop propagation
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop()

    elif session.mode == "ai_prompting":
        # User typed an instruction for AI draft
        session.mode = "browsing"
        await _generate_ai_draft(update, session, text)

        # Stop propagation
        from telegram.ext import ApplicationHandlerStop
        raise ApplicationHandlerStop()


# ── Callback handler ─────────────────────────────────────────────────


async def handle_email_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all email:* callback queries."""
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    user_id = update.effective_user.id

    parts = data.split(":")
    if len(parts) < 2:
        return

    action = parts[1]

    session = get_triage(user_id)
    if not session:
        await query.edit_message_text("No active triage session. Use /inbox to start.")
        return

    touch_triage(user_id)

    # Extract index from callback data if present
    idx = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else session.current_index

    if action == "view":
        await _handle_view(query, session, idx)

    elif action == "reply":
        await _handle_reply(query, session, idx)

    elif action == "ai":
        await _handle_ai_prompt(query, session, idx)

    elif action == "ai_redraft":
        await _handle_ai_redraft_prompt(query, session)

    elif action == "archive":
        await _handle_archive(query, session, idx, user_id)

    elif action == "task":
        await _handle_create_task(query, session, idx, user_id)

    elif action == "skip":
        await _handle_navigate(query, session, 1)  # forward

    elif action == "prev":
        await _handle_navigate(query, session, -1)  # backward

    elif action == "back":
        # Return from reading to browsing
        session.mode = "browsing"
        await _show_current_email(query, session)

    elif action == "send":
        await _handle_send(query, session, user_id)

    elif action == "edit":
        await _handle_edit(query, session)

    elif action == "cancel":
        session.draft = None
        session.mode = "browsing"
        await _show_current_email(query, session)

    elif action == "stop":
        sess = stop_triage(user_id)
        if sess:
            await query.edit_message_text(_triage_recap(sess), parse_mode="Markdown")


# ── Action implementations ───────────────────────────────────────────


async def _handle_view(query, session: EmailTriageSession, idx: int):
    """View full thread for email at index."""
    if idx >= len(session.email_queue):
        await query.edit_message_text("Email not found.")
        return

    msg = session.email_queue[idx]
    session.current_index = idx
    session.current_thread_id = msg.get("threadId")
    session.mode = "reading"

    try:
        if session.provider == "microsoft":
            from roost.mcp.ms_graph_helpers import read_conversation
            thread = read_conversation(session.current_thread_id)
        else:
            from roost.mcp.gmail_helpers import read_thread
            thread = read_thread(session.current_thread_id)
        session.current_thread = thread
    except Exception as e:
        logger.exception("Failed to read thread")
        await query.edit_message_text(f"Failed to load thread: {e}")
        return

    thread_text = _format_thread_view(thread)
    try:
        await query.edit_message_text(
            thread_text,
            parse_mode="Markdown",
            reply_markup=_reading_keyboard(idx),
        )
    except Exception:
        # Fallback without Markdown
        thread_plain = re.sub(r'[*_`\[\]\\]', '', thread_text)
        if len(thread_plain) > TG_MAX_LEN:
            thread_plain = thread_plain[:TG_MAX_LEN - 20] + "\n\n[... truncated ...]"
        await query.edit_message_text(
            thread_plain,
            reply_markup=_reading_keyboard(idx),
        )


async def _handle_reply(query, session: EmailTriageSession, idx: int):
    """Enter reply mode — prompt user to type reply body."""
    if idx >= len(session.email_queue):
        return

    session.current_index = idx
    session.mode = "replying"
    msg = session.email_queue[idx]

    await query.edit_message_text(
        f"*Replying to:* {_escape_md(_extract_name(msg.get('from', '?')))}\n"
        f"*Subject:* {_escape_md(msg.get('subject', ''))}\n\n"
        f"Type your reply message below:",
        parse_mode="Markdown",
    )


async def _handle_ai_prompt(query, session: EmailTriageSession, idx: int):
    """Prompt user for AI draft instruction."""
    if idx >= len(session.email_queue):
        return

    session.current_index = idx
    session.mode = "ai_prompting"
    msg = session.email_queue[idx]

    await query.edit_message_text(
        f"*AI Draft for:* {_escape_md(_extract_name(msg.get('from', '?')))}\n"
        f"*Subject:* {_escape_md(msg.get('subject', ''))}\n\n"
        f"What should the reply say? Type a brief instruction\n"
        f"(e.g. \"confirm the dates and ask about venue\")",
        parse_mode="Markdown",
    )


async def _handle_ai_redraft_prompt(query, session: EmailTriageSession):
    """Prompt user for AI redraft instruction (from draft screen)."""
    session.mode = "ai_prompting"
    await query.edit_message_text(
        "*Redraft with AI*\n\n"
        "Type a new instruction for the AI draft:",
        parse_mode="Markdown",
    )


async def _generate_ai_draft(update: Update, session: EmailTriageSession, instruction: str):
    """Generate AI draft and show it for confirmation."""
    idx = session.current_index
    msg = session.email_queue[idx]

    status_msg = await update.message.reply_text("Generating AI draft...")

    # Load thread if not cached
    if not session.current_thread or session.current_thread_id != msg.get("threadId"):
        try:
            session.current_thread_id = msg.get("threadId")
            if session.provider == "microsoft":
                from roost.mcp.ms_graph_helpers import read_conversation
                session.current_thread = read_conversation(session.current_thread_id)
            else:
                from roost.mcp.gmail_helpers import read_thread
                session.current_thread = read_thread(session.current_thread_id)
        except Exception as e:
            await status_msg.edit_text(f"Failed to load thread: {e}")
            session.mode = "browsing"
            return

    # Generate draft
    try:
        from roost.bot.email_draft import draft_reply
        thread_messages = session.current_thread.get("messages", [])
        draft_body = await draft_reply(thread_messages, instruction)
    except Exception as e:
        logger.exception("AI draft generation failed")
        await status_msg.edit_text(f"AI draft failed: {e}")
        session.mode = "browsing"
        return

    if draft_body.startswith("[Error"):
        await status_msg.edit_text(f"AI draft error: {draft_body}")
        session.mode = "browsing"
        return

    # Build draft
    reply_to_email = _extract_email_address(msg.get("from", ""))
    subject = msg.get("subject", "")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    session.draft = {
        "to": reply_to_email,
        "subject": subject,
        "body": draft_body,
        "cc": "",
        "thread_id": msg.get("threadId", ""),
    }

    await status_msg.edit_text(
        _format_draft(session.draft),
        parse_mode="Markdown",
        reply_markup=_draft_keyboard(),
    )


async def _handle_archive(query, session: EmailTriageSession, idx: int, user_id: int):
    """Archive the email thread (remove INBOX label)."""
    if idx >= len(session.email_queue):
        return

    msg = session.email_queue[idx]
    thread_id = msg.get("threadId")

    try:
        if session.provider == "microsoft":
            # MS: mark as read (closest equivalent to Gmail archive)
            from roost.microsoft import get_graph_session
            ms_session = get_graph_session()
            if ms_session:
                msg_id = msg.get("id")
                if msg_id:
                    ms_session.patch(
                        f"https://graph.microsoft.com/v1.0/me/messages/{msg_id}",
                        json={"isRead": True},
                    )
        else:
            from roost.gmail import get_gmail_service
            service = get_gmail_service()
            if service:
                # Remove INBOX label (= archive)
                service.users().threads().modify(
                    userId="me",
                    id=thread_id,
                    body={"removeLabelIds": ["INBOX"]},
                ).execute()
    except Exception as e:
        logger.exception("Failed to archive thread")
        await query.edit_message_text(f"Archive failed: {e}")
        return

    summary = f"Archived: {msg.get('subject', '?')[:40]}"
    session.actions_taken.append(("archived", thread_id, summary))

    # Move to next email
    session.current_index = min(idx + 1, len(session.email_queue) - 1)
    if idx + 1 >= len(session.email_queue):
        # No more emails
        sess = stop_triage(user_id)
        await query.edit_message_text(
            f"Archived. {_triage_recap(sess)}",
            parse_mode="Markdown",
        )
    else:
        session.mode = "browsing"
        await _show_current_email(query, session)


async def _handle_create_task(query, session: EmailTriageSession, idx: int, user_id: int):
    """Create a task from the email subject."""
    if idx >= len(session.email_queue):
        return

    msg = session.email_queue[idx]
    subject = msg.get("subject", "(no subject)")
    sender = _extract_name(msg.get("from", "Unknown"))

    try:
        from roost import task_service
        from roost.models import TaskCreate
        task = task_service.create_task(
            TaskCreate(
                title=f"Email: {subject}",
                description=f"From: {sender}\nThread: {msg.get('threadId', '')}",
            ),
            source="telegram",
        )
        summary = f"Task #{task.id}: {subject[:40]}"
        session.actions_taken.append(("task", msg.get("threadId"), summary))

        await query.edit_message_text(
            f"Task #{task.id} created: _{_escape_md(subject[:60])}_\n\n"
            f"Continuing triage...",
            parse_mode="Markdown",
        )

        # Auto-advance after a short pause by showing current email
        import asyncio
        await asyncio.sleep(1)
        await _show_current_email_new_msg(query, session)

    except Exception as e:
        logger.exception("Failed to create task from email")
        await query.edit_message_text(f"Failed to create task: {e}")


async def _handle_navigate(query, session: EmailTriageSession, direction: int):
    """Navigate forward (+1) or backward (-1) in the email queue."""
    new_idx = session.current_index + direction
    if new_idx < 0:
        new_idx = 0
    if new_idx >= len(session.email_queue):
        new_idx = len(session.email_queue) - 1

    if new_idx == session.current_index and direction > 0:
        # At the end
        await query.edit_message_text(
            "End of queue. Use /inbox stop to finish, or << Prev to go back.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("<< Prev", callback_data="email:prev"),
                    InlineKeyboardButton("Stop", callback_data="email:stop"),
                ],
            ]),
        )
        return

    session.current_index = new_idx
    session.mode = "browsing"
    session.current_thread = None
    session.current_thread_id = None
    await _show_current_email(query, session)


async def _handle_send(query, session: EmailTriageSession, user_id: int):
    """Send the current draft."""
    if not session.draft:
        await query.edit_message_text("No draft to send.")
        return

    draft = session.draft
    try:
        if session.provider == "microsoft":
            from roost.mcp.ms_graph_helpers import send_message as ms_send

            # MS reply needs the last message's ID — load thread if not cached
            if not session.current_thread:
                try:
                    from roost.mcp.ms_graph_helpers import read_conversation
                    conv_id = session.email_queue[session.current_index].get("threadId", "")
                    if conv_id:
                        session.current_thread = read_conversation(conv_id)
                        session.current_thread_id = conv_id
                except Exception:
                    logger.debug("Failed to load MS thread for reply", exc_info=True)

            reply_to_id = ""
            if session.current_thread and session.current_thread.get("messages"):
                reply_to_id = session.current_thread["messages"][-1].get("id", "")

            result = ms_send(
                to=draft["to"],
                subject=draft["subject"],
                body=draft["body"],
                cc=draft.get("cc", ""),
                reply_to_id=reply_to_id,
            )
        else:
            from roost.mcp.gmail_helpers import send_message, read_thread as _read_thread

            # Auto-fetch threading headers
            in_reply_to = ""
            references = ""
            if draft["thread_id"]:
                try:
                    thread = _read_thread(draft["thread_id"])
                    if thread["messages"]:
                        last_msg = thread["messages"][-1]
                        in_reply_to = last_msg.get("message_id", "")
                        references = last_msg.get("references", "")
                        if in_reply_to:
                            references = f"{references} {in_reply_to}".strip()
                except Exception:
                    logger.debug("Failed to fetch threading headers for reply", exc_info=True)

            result = send_message(
                to=draft["to"],
                subject=draft["subject"],
                body=draft["body"],
                cc=draft.get("cc", ""),
                thread_id=draft["thread_id"],
                in_reply_to=in_reply_to,
                references=references,
            )

        if "error" in result:
            await query.edit_message_text(f"Send failed: {result['error']}")
            return

    except Exception as e:
        logger.exception("Failed to send email")
        await query.edit_message_text(f"Send failed: {e}")
        return

    # Apply label management (Gmail only)
    if session.provider != "microsoft":
        _apply_post_send_labels(draft["thread_id"])

    summary = f"Replied to: {draft['to'][:30]} re: {draft['subject'][:30]}"
    session.actions_taken.append(("replied", draft["thread_id"], summary))
    session.draft = None

    # Advance to next email
    idx = session.current_index
    session.current_index = min(idx + 1, len(session.email_queue) - 1)

    if idx + 1 >= len(session.email_queue):
        sess = stop_triage(user_id)
        await query.edit_message_text(
            f"Sent! {_triage_recap(sess)}",
            parse_mode="Markdown",
        )
    else:
        session.mode = "browsing"
        try:
            next_msg = session.email_queue[session.current_index]
            preview = _format_email_preview(next_msg, session.current_index, len(session.email_queue), provider=_provider_label(session))
            await query.edit_message_text(
                f"Sent!\n\n{preview}",
                parse_mode="Markdown",
                reply_markup=_browsing_keyboard(session.current_index),
            )
        except Exception:
            await query.edit_message_text(
                "Sent! Moving to next email...",
                reply_markup=_browsing_keyboard(session.current_index),
            )


async def _handle_edit(query, session: EmailTriageSession):
    """Re-enter reply editing mode for the current draft."""
    session.mode = "replying"
    if session.draft:
        await query.edit_message_text(
            f"*Editing reply to:* {_escape_md(session.draft['to'])}\n"
            f"*Subject:* {_escape_md(session.draft['subject'])}\n\n"
            f"Current draft:\n{_escape_md(session.draft['body'][:500])}\n\n"
            f"Type your new reply to replace it:",
            parse_mode="Markdown",
        )
    else:
        await query.edit_message_text("Type your reply message:")


# ── Internal helpers ─────────────────────────────────────────────────


def _provider_label(session: EmailTriageSession) -> str:
    """Return provider label for header — only if dual-provider context applies."""
    # We always store the provider; the header shows specific name for clarity
    return session.provider if session.provider else ""


async def _show_current_email(query, session: EmailTriageSession):
    """Show the current email in the queue (edits existing message)."""
    idx = session.current_index
    if idx >= len(session.email_queue):
        return

    msg = session.email_queue[idx]
    preview = _format_email_preview(msg, idx, len(session.email_queue), provider=_provider_label(session))
    try:
        await query.edit_message_text(
            preview,
            parse_mode="Markdown",
            reply_markup=_browsing_keyboard(idx),
        )
    except Exception:
        await query.edit_message_text(
            f"Email {idx + 1}/{len(session.email_queue)}\n"
            f"From: {msg.get('from', '?')}\n"
            f"Subject: {msg.get('subject', '?')}",
            reply_markup=_browsing_keyboard(idx),
        )


async def _show_current_email_new_msg(query, session: EmailTriageSession):
    """Show current email as a new message (for task creation flow where we can't edit)."""
    idx = session.current_index
    if idx >= len(session.email_queue):
        return

    msg = session.email_queue[idx]
    preview = _format_email_preview(msg, idx, len(session.email_queue), provider=_provider_label(session))
    try:
        await query.message.reply_text(
            preview,
            parse_mode="Markdown",
            reply_markup=_browsing_keyboard(idx),
        )
    except Exception:
        await query.message.reply_text(
            f"Email {idx + 1}/{len(session.email_queue)}\n"
            f"From: {msg.get('from', '?')}\n"
            f"Subject: {msg.get('subject', '?')}",
            reply_markup=_browsing_keyboard(idx),
        )


def _apply_post_send_labels(thread_id: str):
    """After sending a reply: add (Waiting for Reply), remove (To Reply)."""
    try:
        from roost.gmail import get_gmail_service
        from roost.gmail.auto_label import _ensure_label_cache, _action_label_ids, _label_cache

        service = get_gmail_service()
        if not service:
            return

        _ensure_label_cache(service)

        to_reply_id = _action_label_ids.get("to_reply", "")
        waiting_id = _action_label_ids.get("waiting", "")

        if not waiting_id:
            return

        modify_body = {"addLabelIds": [waiting_id]}
        if to_reply_id:
            modify_body["removeLabelIds"] = [to_reply_id]

        service.users().threads().modify(
            userId="me",
            id=thread_id,
            body=modify_body,
        ).execute()

    except Exception:
        logger.exception("Failed to apply post-send labels for thread %s", thread_id)
