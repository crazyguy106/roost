"""Telegram outbound adapter — customer DM via Bot API.

Counterpart to `sms.py` / `whatsapp.py` for the lead-nurture dispatcher.
Reuses the same `TELEGRAM_BOT_TOKEN` the operator bot logs in with —
the bot can DM any chat_id that has previously initiated contact with
it. (Telegram bots cannot cold-DM users; the customer must `/start`
or send any message first to establish the chat.)

Named `telegram_out.py` so it does not shadow the `telegram` package
shipped by `python-telegram-bot`.

Fail-closed: returns an error envelope when disabled or unconfigured,
matching the WhatsApp / SMS adapter shape per CLAUDE.md.
"""

from __future__ import annotations

import logging

import httpx

from roost.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ENABLED

logger = logging.getLogger("roost.telegram_out")

_BASE = "https://api.telegram.org"


def send_text_message(chat_id: str, body: str) -> dict:
    """Send a plain-text Telegram message to a single chat.

    Args:
        chat_id: Telegram chat_id (integer-as-string, e.g. '123456789').
                 For private chats this is the user_id; for groups it is
                 negative. Caller is responsible for shape — we just pass
                 it through.
        body: Message text. Telegram caps at 4096 chars per message; we
              do not split, caller should chunk if needed.

    Returns:
        {"ok": True, "message_id": "<int>", "provider": "telegram"} on success
        {"ok": False, "error": "..."} on disabled/misconfigured/API errors
    """
    if not TELEGRAM_ENABLED:
        return {"ok": False, "error": "Telegram not enabled (TELEGRAM_ENABLED=false)"}

    if not TELEGRAM_BOT_TOKEN:
        return {"ok": False, "error": "Telegram bot token not configured"}

    if not chat_id:
        return {"ok": False, "error": "chat_id is required"}

    url = f"{_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": body}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            # Bot API returns {"ok": true, "result": {"message_id": N, ...}}
            if not data.get("ok"):
                # API-level failure (e.g. chat not found, bot blocked by user)
                desc = data.get("description", "unknown Telegram API error")
                logger.warning("Telegram sendMessage rejected: %s", desc)
                return {"ok": False, "error": desc, "details": data}
            result = data.get("result", {})
            msg_id = str(result.get("message_id", ""))
            logger.info("Telegram message sent to %s: id=%s", chat_id, msg_id)
            return {"ok": True, "message_id": msg_id, "provider": "telegram"}
    except httpx.HTTPStatusError as e:
        # Telegram returns 400/403 with JSON body for blocked-by-user,
        # chat-not-found etc. Surface the description so callers can
        # decide whether to retry or mark the contact unreachable.
        details = {}
        try:
            details = e.response.json()
        except Exception:
            details = {"body": e.response.text[:500]}
        logger.error("Telegram API %s: %s", e.response.status_code, details)
        return {
            "ok": False,
            "error": f"Telegram API {e.response.status_code}: "
                     f"{details.get('description', '')}",
            "details": details,
        }
    except Exception as e:
        logger.exception("Telegram send failed")
        return {"ok": False, "error": str(e)}
