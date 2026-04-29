"""Telegram account linking handler.

Handles "LINK <code>" messages to connect a Telegram account to a web user.
This handler intentionally does NOT use the @authorized decorator because
linking is the mechanism by which a Telegram user becomes authorized.
"""

import logging
from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("roost.bot.linking")

# Stop propagation so "LINK <code>" doesn't reach the agent handler
try:
    from telegram.ext import ApplicationHandlerStop as _Stop
except ImportError:
    class _Stop(Exception):
        pass


async def handle_link_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process 'LINK <code>' messages for account linking.

    No @authorized — linking is the auth step itself. Any Telegram user
    can attempt to link, but only valid unexpired codes succeed.
    """
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()

    # Only handle messages that start with "LINK " (case-insensitive)
    if not text.upper().startswith("LINK "):
        return  # Let other handlers process non-LINK messages

    code = text[5:].strip()
    if not code:
        await update.message.reply_text(
            "Please include the code.\n\n"
            "Usage: `LINK <code>`\n\n"
            "Get a code from Settings > Telegram in the web UI.",
            parse_mode="Markdown",
        )
        raise _Stop()

    telegram_id = update.effective_user.id
    telegram_name = update.effective_user.first_name or str(telegram_id)

    try:
        from roost.services.telegram_linking import verify_link_code
        result = verify_link_code(code, telegram_id)
    except Exception:
        logger.exception("Link verification failed for telegram_id=%d", telegram_id)
        await update.message.reply_text("Something went wrong. Please try again.")
        raise _Stop()

    if result["ok"]:
        web_name = result.get("name", "your account")
        await update.message.reply_text(
            f"Linked successfully!\n\n"
            f"Your Telegram account ({telegram_name}) is now connected to "
            f"web account: *{web_name}*\n\n"
            f"You can now:\n"
            f"- Receive OTP codes for web chat confirmations\n"
            f"- Use all bot commands\n\n"
            f"Type /help to get started.",
            parse_mode="Markdown",
        )
        logger.info("Linked telegram %d (%s) to web user %s",
                     telegram_id, telegram_name, web_name)
    else:
        error = result.get("error", "Unknown error")
        await update.message.reply_text(
            f"Linking failed: {error}\n\n"
            "Make sure you copied the code correctly from Settings.\n"
            "Codes expire after 10 minutes.",
        )

    raise _Stop()
