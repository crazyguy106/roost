"""Telegram bot handler that routes inbound text to the qualification flow
when an in-progress qualifying session exists for the sender's chat_id.

Runs at handler-group -1 (before the agent catch-all), and raises
ApplicationHandlerStop when it consumes a message so the agent doesn't
also try to answer.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("roost.lead_nurture.bot.qualify")

try:
    from telegram.ext import ApplicationHandlerStop as _Stop
except ImportError:  # pragma: no cover — only if python-telegram-bot is very old
    class _Stop(Exception):
        pass


async def handle_qualify_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Consume the message if the chat_id has an in-progress qualifying session.

    Channel "telegram" + identifier=chat_id (stringified) is matched against
    enrollments paused with `pause_reason='qualifying'`.
    """
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        return

    try:
        from roost.extras.lead_nurture.services import qualification
        result = qualification.process_answer(
            channel="telegram", identifier=str(chat_id), text=text,
        )
    except Exception:
        logger.exception("qualification process_answer failed")
        return

    if not result.get("handled"):
        return  # let other handlers run

    # Send a one-line acknowledgement when finished; mid-flow the next
    # question was already sent by process_answer.
    if result.get("done"):
        if result.get("error"):
            logger.warning("qualification ended in error: %s", result["error"])
        else:
            label = result.get("label", "?")
            score = result.get("score")
            logger.info(
                "Telegram qualification done chat=%s label=%s score=%s",
                chat_id, label, score,
            )

    raise _Stop()
