"""Telegram handlers for the end-of-day summary.

Commands:
  /summary               — render and send the summary right now.
  /summarytime HH:MM [tz] — set the daily delivery time + timezone.
                            Default tz: Asia/Singapore.
  /summaryoff            — disable daily delivery (manual /summary still works).
  /summarystatus         — show current schedule.
"""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized

logger = logging.getLogger(__name__)

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@authorized
async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Build and send the summary on demand."""
    from roost.services.daily_summary import build_summary, format_summary
    from roost.services.settings import get_setting

    user_id = update.effective_user.id
    tz_name = get_setting("daily_summary_tz", user_id=user_id) \
        or "Asia/Singapore"
    summary = build_summary(user_id=str(user_id), tz_name=tz_name)
    text = format_summary(summary)
    await update.message.reply_text(text, parse_mode="Markdown")


@authorized
async def cmd_summarytime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the daily delivery time and (optionally) timezone."""
    from roost.services.settings import set_setting
    from zoneinfo import ZoneInfo

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /summarytime HH:MM [tz]\n"
            "Examples:\n"
            "  /summarytime 18:00\n"
            "  /summarytime 21:30 Asia/Singapore\n"
            "  /summarytime 17:00 Europe/London"
        )
        return

    hhmm = args[0]
    if not _HHMM_RE.match(hhmm):
        await update.message.reply_text(
            f"Bad time {hhmm!r}. Use 24-hour HH:MM (e.g. 18:00, 09:30)."
        )
        return

    tz_name = args[1] if len(args) > 1 else "Asia/Singapore"
    try:
        ZoneInfo(tz_name)
    except Exception:
        await update.message.reply_text(
            f"Unknown timezone {tz_name!r}. Use an IANA name like "
            "Asia/Singapore, Europe/London, America/New_York."
        )
        return

    user_id = update.effective_user.id
    set_setting("daily_summary_time", hhmm, user_id=user_id)
    set_setting("daily_summary_tz", tz_name, user_id=user_id)
    # Clear the dedupe marker so today's summary can still fire if the new
    # time hasn't passed yet.
    from roost.services.settings import delete_setting
    try:
        delete_setting("daily_summary_last_sent", user_id=user_id)
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ Daily summary scheduled for {hhmm} {tz_name}.\n"
        "Use /summary to preview now, /summaryoff to disable."
    )


@authorized
async def cmd_summaryoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disable scheduled daily delivery."""
    from roost.services.settings import delete_setting

    user_id = update.effective_user.id
    try:
        delete_setting("daily_summary_time", user_id=user_id)
    except Exception:
        pass
    await update.message.reply_text(
        "Daily summary disabled. /summary still works on demand."
    )


@authorized
async def cmd_summarystatus(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current daily-summary configuration."""
    from roost.services.settings import get_setting

    user_id = update.effective_user.id
    hhmm = get_setting("daily_summary_time", user_id=user_id)
    tz_name = get_setting("daily_summary_tz", user_id=user_id) \
        or "Asia/Singapore"
    last_sent = get_setting("daily_summary_last_sent", user_id=user_id)

    if not hhmm:
        await update.message.reply_text(
            "Daily summary: *disabled*\n"
            "Set with: /summarytime HH:MM [tz]",
            parse_mode="Markdown",
        )
        return

    lines = [
        "*Daily summary*",
        f"  Time: {hhmm} {tz_name}",
        f"  Last sent: {last_sent or '(never)'}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
