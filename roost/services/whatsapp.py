"""WhatsApp Cloud API client — send messages via Meta Business Platform.

Uses the official WhatsApp Cloud API (not Baileys/reverse-engineered).
Students own their own Meta credentials; Roost never stores third-party tokens.

Rate limits: 80 messages/second (business tier).
Free tier: 1,000 service conversations/month.
"""

import hashlib
import hmac
import json
import logging
from typing import Any

import httpx

from roost.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)

logger = logging.getLogger("roost.whatsapp")

BASE_URL = "https://graph.facebook.com/v21.0"


def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """Verify X-Hub-Signature-256 from Meta webhook.

    Returns True if valid, False otherwise.
    """
    if not WHATSAPP_APP_SECRET:
        logger.warning("WHATSAPP_APP_SECRET not set — cannot verify webhook")
        return False

    expected = hmac.new(
        WHATSAPP_APP_SECRET.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


def send_text_message(to: str, body: str) -> dict:
    """Send a text message via WhatsApp Cloud API.

    Args:
        to: Recipient phone number in E.164 format (e.g. '+6591234567').
        body: Message text (max 4096 chars).

    Returns:
        API response dict with message_id on success, error on failure.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"error": "WhatsApp not configured (missing token or phone number ID)"}

    url = f"{BASE_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "text",
        "text": {"body": body[:4096]},
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
            logger.info("WhatsApp sent to %s: msg_id=%s", to, msg_id)
            return {"ok": True, "message_id": msg_id}
    except httpx.HTTPStatusError as e:
        error_body = e.response.json() if e.response.content else {}
        logger.error("WhatsApp API error: %s %s", e.response.status_code, error_body)
        return {"error": f"WhatsApp API {e.response.status_code}", "details": error_body}
    except Exception as e:
        logger.exception("WhatsApp send failed")
        return {"error": str(e)}


def send_template_message(
    to: str,
    template_name: str,
    language_code: str = "en",
    components: list[dict] | None = None,
) -> dict:
    """Send a pre-approved template message via WhatsApp Cloud API.

    Template messages are required for initiating conversations (outside
    the 24-hour customer service window).

    Args:
        to: Recipient phone number in E.164 format.
        template_name: Meta-approved template name.
        language_code: Template language (default 'en').
        components: Optional template components (header, body, button params).
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"error": "WhatsApp not configured"}

    url = f"{BASE_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    template_obj: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code},
    }
    if components:
        template_obj["components"] = components

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": "template",
        "template": template_obj,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
            logger.info("WhatsApp template '%s' sent to %s: msg_id=%s",
                        template_name, to, msg_id)
            return {"ok": True, "message_id": msg_id}
    except httpx.HTTPStatusError as e:
        error_body = e.response.json() if e.response.content else {}
        return {"error": f"WhatsApp API {e.response.status_code}", "details": error_body}
    except Exception as e:
        logger.exception("WhatsApp template send failed")
        return {"error": str(e)}


def mark_as_read(message_id: str) -> dict:
    """Mark a received message as read (sends blue ticks).

    Args:
        message_id: The wamid of the message to mark as read.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"error": "WhatsApp not configured"}

    url = f"{BASE_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": message_id,
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            return {"ok": True}
    except Exception as e:
        logger.debug("Failed to mark message as read: %s", e)
        return {"error": str(e)}


def parse_webhook_entry(entry: dict) -> list[dict]:
    """Parse a webhook entry into a list of inbound messages.

    WhatsApp webhook payloads can contain multiple changes/messages.
    Returns a list of dicts with: sender, message_id, timestamp, type, text.
    """
    messages = []
    for change in entry.get("changes", []):
        value = change.get("value", {})
        contacts = {c["wa_id"]: c.get("profile", {}).get("name", "")
                     for c in value.get("contacts", [])}

        for msg in value.get("messages", []):
            parsed = {
                "sender": msg.get("from", ""),
                "sender_name": contacts.get(msg.get("from", ""), ""),
                "message_id": msg.get("id", ""),
                "timestamp": msg.get("timestamp", ""),
                "type": msg.get("type", ""),
                "text": "",
            }
            if msg.get("type") == "text":
                parsed["text"] = msg.get("text", {}).get("body", "")
            elif msg.get("type") == "button":
                parsed["text"] = msg.get("button", {}).get("text", "")
            elif msg.get("type") == "interactive":
                interactive = msg.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    parsed["text"] = interactive.get("button_reply", {}).get("title", "")
                elif interactive.get("type") == "list_reply":
                    parsed["text"] = interactive.get("list_reply", {}).get("title", "")

            messages.append(parsed)

    return messages
