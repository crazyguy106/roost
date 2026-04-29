"""Feishu/Lark adapter — stub for ByteDance Feishu (international: Lark) bot.

Feishu uses event subscriptions with AES-encrypted payloads.
Requires:
  - FEISHU_APP_ID
  - FEISHU_APP_SECRET
  - FEISHU_VERIFICATION_TOKEN
  - FEISHU_ENCRYPT_KEY

API docs: https://open.feishu.cn/document/home/index

Status: STUB — not yet implemented.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def handle_feishu_event(body: dict) -> dict:
    """Handle incoming Feishu event callback.

    Stub — returns not-implemented response.
    """
    # Feishu requires URL verification challenge response
    if body.get("type") == "url_verification":
        return {"challenge": body.get("challenge", "")}

    logger.info("Feishu event received (stub): %s", str(body)[:200])
    return {"error": "Feishu/Lark adapter not yet implemented"}


async def send_feishu_message(
    chat_id: str,
    text: str,
) -> dict:
    """Send a message to a Feishu chat.

    Stub — returns not-implemented response.
    """
    return {"error": "Feishu/Lark adapter not yet implemented"}
