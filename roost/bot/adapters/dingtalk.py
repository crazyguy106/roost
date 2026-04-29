"""DingTalk adapter — stub for Alibaba DingTalk bot integration.

DingTalk uses webhook-based messaging with crypto signature verification.
Requires:
  - DINGTALK_APP_KEY
  - DINGTALK_APP_SECRET
  - DINGTALK_ROBOT_CODE

API docs: https://open.dingtalk.com/document/orgapp/robot-overview

Status: STUB — not yet implemented.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Config (to be added to config.py when implemented)
# DINGTALK_APP_KEY: str
# DINGTALK_APP_SECRET: str
# DINGTALK_ROBOT_CODE: str


async def handle_dingtalk_webhook(body: dict) -> dict:
    """Handle incoming DingTalk webhook message.

    Stub — returns not-implemented response.
    """
    logger.info("DingTalk webhook received (stub): %s", str(body)[:200])
    return {"error": "DingTalk adapter not yet implemented"}


async def send_dingtalk_message(
    conversation_id: str,
    text: str,
) -> dict:
    """Send a message to a DingTalk conversation.

    Stub — returns not-implemented response.
    """
    return {"error": "DingTalk adapter not yet implemented"}
