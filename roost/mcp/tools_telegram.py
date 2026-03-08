"""MCP tools for sending Telegram messages via Bot HTTP API."""

from roost.mcp.server import mcp


@mcp.tool()
def telegram_send_message(
    text: str,
    chat_id: int | None = None,
    parse_mode: str | None = None,
) -> dict:
    """Send a Telegram message to authorized users via the bot.

    Args:
        text: Message text to send.
        chat_id: Specific user ID to send to (must be in TELEGRAM_ALLOWED_USERS).
                 If omitted, sends to ALL authorized users (broadcast).
        parse_mode: Optional: "HTML" or "Markdown" for rich formatting.
    """
    try:
        import httpx
        from roost.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS

        if not TELEGRAM_BOT_TOKEN:
            return {"error": "TELEGRAM_BOT_TOKEN not configured"}
        if not TELEGRAM_ALLOWED_USERS:
            return {"error": "No TELEGRAM_ALLOWED_USERS configured"}

        targets = [chat_id] if chat_id else TELEGRAM_ALLOWED_USERS
        for t in targets:
            if t not in TELEGRAM_ALLOWED_USERS:
                return {"error": f"User {t} is not in TELEGRAM_ALLOWED_USERS"}

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        sent, failed = [], []

        with httpx.Client(timeout=10) as client:
            for uid in targets:
                payload = {"chat_id": uid, "text": text}
                if parse_mode:
                    payload["parse_mode"] = parse_mode
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    sent.append(uid)
                else:
                    failed.append({"user_id": uid, "error": resp.text})

        return {"sent_to": sent, "failed": failed}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def telegram_get_bot_info() -> dict:
    """Get info about the configured Telegram bot (verify token works)."""
    try:
        import httpx
        from roost.config import TELEGRAM_BOT_TOKEN

        if not TELEGRAM_BOT_TOKEN:
            return {"error": "TELEGRAM_BOT_TOKEN not configured"}

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe"
        with httpx.Client(timeout=10) as client:
            resp = client.post(url)
            if resp.status_code != 200:
                return {"error": f"Bot API error: {resp.text}"}
            data = resp.json().get("result", {})
            return {
                "bot_id": data.get("id"),
                "username": data.get("username"),
                "first_name": data.get("first_name"),
                "is_bot": data.get("is_bot"),
            }
    except Exception as e:
        return {"error": str(e)}
