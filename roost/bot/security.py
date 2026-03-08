"""Telegram bot security — user ID allowlist + command logging."""

import time
import logging
from functools import wraps
from telegram import Update
from telegram.ext import ContextTypes
from roost.config import TELEGRAM_ALLOWED_USERS

logger = logging.getLogger("roost.bot.commands")


def authorized(func):
    """Decorator that restricts bot commands to allowed user IDs.

    Also logs every command invocation with args, user, and duration.
    """
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id not in TELEGRAM_ALLOWED_USERS:
            logger.warning("Unauthorized: user=%s func=%s", user_id, func.__name__)
            if update.callback_query:
                await update.callback_query.answer(
                    f"Not authorized (ID: {user_id})", show_alert=True,
                )
            elif update.message:
                await update.message.reply_text(
                    f"You're not authorized. Your Telegram ID: {user_id}\n"
                    "Ask your admin to add you."
                )
            return

        # Build log context
        user_name = update.effective_user.first_name or str(user_id)
        if update.message and update.message.text:
            cmd_text = update.message.text[:120]
        elif update.callback_query and update.callback_query.data:
            cmd_text = f"callback:{update.callback_query.data[:80]}"
        else:
            cmd_text = func.__name__

        start = time.monotonic()
        try:
            result = await func(update, context)
            elapsed = (time.monotonic() - start) * 1000
            logger.info("%s | %s | %.0fms", user_name, cmd_text, elapsed)
            return result
        except Exception:
            elapsed = (time.monotonic() - start) * 1000
            logger.exception("%s | %s | FAILED after %.0fms", user_name, cmd_text, elapsed)
            raise
    return wrapper
