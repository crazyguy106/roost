"""Telegram customer fallback — the lead-side counterpart to the operator bot.

The same Telegram bot serves two audiences:

* **Operators** (IDs in `TELEGRAM_ALLOWED_USERS`) get the full command set
  — /tasks, /napprove, /inbox, /lead, …
* **Customers** (anyone else who DM's the bot first) get a thin lead-capture
  channel: STOP/HELP follow the Twilio-style keyword conventions used by
  the SMS adapter, mid-flow qualification answers are routed back through
  the qualification engine, and a first-contact message ingests the chat
  as a new lead.

This handler runs at handler-group **-3** in its own group. python-
telegram-bot only fires the first matching handler per group, so customer
sits in a group where it is the sole occupant and is guaranteed to run.
It defers `LINK <code>` messages to the linking handler at group -2 and
slash commands to the per-command dispatchers.

Telegram constraint: bots cannot cold-DM a user; the customer must
initiate contact (any message, or tapping "Start" in the bot profile)
to establish a `chat_id`. From that point onward, both the lead-ingest
pipeline and `telegram_out.send_text_message` can talk back.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from roost.config import TELEGRAM_ALLOWED_USERS

logger = logging.getLogger("roost.lead_nurture.bot.customer")

try:
    from telegram.ext import ApplicationHandlerStop as _Stop
except ImportError:  # pragma: no cover — only if python-telegram-bot is very old
    class _Stop(Exception):
        pass


# Mirror the SMS adapter's keyword set so STOP behaves the same across
# every customer channel. First-token-after-punctuation match.
_STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
_HELP_KEYWORDS = {"HELP", "INFO"}


def _first_token(body: str) -> str:
    """Return the first whitespace-delimited token, uppercased and stripped
    of common trailing punctuation."""
    if not body:
        return ""
    parts = body.strip().split()
    if not parts:
        return ""
    return parts[0].upper().strip(".,!?;:'\"")


async def handle_customer_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """Route non-operator Telegram messages through the lead pipeline.

    Always swallows exceptions and replies on the user-facing path — a
    broken CRM or DB must not bubble back to Telegram (would trigger
    update-retry storms).
    """
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text:
        return

    # Slash commands are handled by their own CommandHandlers.
    if text.startswith("/"):
        return
    # "LINK <code>" is handled by handle_link_message at group -2.
    if text.upper().startswith("LINK "):
        return

    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return

    # Operators flow through to the normal stack.
    if user.id in TELEGRAM_ALLOWED_USERS:
        return

    chat_id = str(chat.id)
    keyword = _first_token(text)
    sender_name = (user.first_name or "").strip()
    if user.last_name:
        sender_name = f"{sender_name} {user.last_name}".strip()

    # ── STOP — unsubscribe and confirm. ─────────────────────────────
    if keyword in _STOP_KEYWORDS:
        try:
            from roost.extras.lead_nurture.services.cadences import (
                store as cadences_store,
            )
            n = cadences_store.exit_enrollments_by_contact(
                telegram_chat_id=chat_id, reason="opted_out:telegram",
            )
            logger.info(
                "Telegram STOP from chat=%s — exited %d enrollments", chat_id, n,
            )
        except Exception:
            logger.exception(
                "Telegram STOP exit-enrollments failed (non-fatal)",
            )
        try:
            await update.message.reply_text(
                "You've been unsubscribed and will not receive further messages. "
                "Reply START to resubscribe."
            )
        except Exception:
            logger.exception("Telegram STOP reply failed")
        raise _Stop()

    # ── HELP — support info, no ingest. ─────────────────────────────
    if keyword in _HELP_KEYWORDS:
        try:
            await update.message.reply_text(
                "Reply STOP to unsubscribe. For support contact support@verixiom.com."
            )
        except Exception:
            logger.exception("Telegram HELP reply failed")
        raise _Stop()

    # ── Qualification reply routing. ────────────────────────────────
    # If this chat has an in-progress qualifying session, feed the text
    # to the qualification engine and skip lead ingest. The engine
    # itself sends the next question / completion message.
    try:
        from roost.extras.lead_nurture.services import qualification
        q_result = qualification.process_answer(
            channel="telegram", identifier=chat_id, text=text,
        )
        if q_result.get("handled"):
            raise _Stop()
    except _Stop:
        raise
    except Exception:
        logger.exception(
            "qualification process_answer failed (non-fatal) — falling through",
        )

    # ── Inbound reply on existing enrollment. ───────────────────────
    # Stamp last_inbound_at on every active/paused enrollment for this
    # chat. If we touched ≥1 enrollment the wait_for_reply gate will
    # see it on the next scheduler tick — do NOT re-ingest, otherwise
    # we'd create duplicate enrollments for every reply.
    touched = 0
    try:
        from roost.extras.lead_nurture.services.cadences import (
            store as cadences_store,
        )
        touched = cadences_store.mark_inbound_for_contact(
            telegram_chat_id=chat_id,
        )
    except Exception:
        logger.exception(
            "mark_inbound_for_contact (telegram) failed (non-fatal)",
        )

    if touched > 0:
        raise _Stop()

    # ── First-contact lead ingest. ──────────────────────────────────
    try:
        from roost.extras.lead_nurture.services import leads as leads_svc
        leads_svc.ingest_lead(
            channel="telegram",
            telegram_chat_id=chat_id,
            name=sender_name,
            message_text=text,
            source="telegram_inbound",
            qualifying_identifier=chat_id,
        )
    except Exception:
        logger.exception("lead ingest from Telegram failed (non-fatal)")

    raise _Stop()
