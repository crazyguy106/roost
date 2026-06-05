"""Operator heads-ups via Telegram — best-effort, fire-and-forget.

Used to ping the human operator when Roost deliberately does *not* act on an
inbound message (the automation gate paused it, or the contact was dormant),
so they know to pick it up in Chatwoot. Silently no-ops when Telegram isn't
configured.
"""

from __future__ import annotations

import logging

logger = logging.getLogger("roost.operator_notify")


async def notify_operator(text: str) -> None:
    """Send a plain-text message to every allowed Telegram operator."""
    try:
        from roost.config import TELEGRAM_ALLOWED_USERS, TELEGRAM_BOT_TOKEN
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_USERS:
            return
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            for user_id in TELEGRAM_ALLOWED_USERS:
                try:
                    await client.post(url, json={"chat_id": user_id, "text": text})
                except Exception:
                    logger.debug("operator notify failed for %s", user_id)
    except Exception:
        logger.exception("operator notify failed")


async def notify_gated(*, channel: str, who: str, text: str, gate: dict) -> None:
    """Heads-up that an inbound message was gated and is waiting for a human.

    No-op for non-gated (``reason == "ok"``) results.
    """
    reason = (gate or {}).get("reason")
    if reason == "dormant":
        head = (
            f"💤 {channel.title()} lead resurfaced after "
            f"{gate.get('dormant_hours')}h dormant — left for you to handle:"
        )
    elif reason == "paused":
        head = f"⏸ Automations paused — {channel.title()} inbound left for you:"
    else:
        return
    preview = (text or "").strip()[:200]
    await notify_operator(f'{head}\n{who}\n"{preview}"')
