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
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from roost.config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_APP_SECRET,
    WHATSAPP_PHONE_NUMBER_ID,
)
from roost.extras.messaging_external.services.text_format import collapse_soft_wraps

logger = logging.getLogger("roost.whatsapp")

BASE_URL = "https://graph.facebook.com/v21.0"


def _components_to_processed_params(
    components: list[dict] | None,
) -> dict[str, str]:
    """Translate Meta-style template components into Chatwoot's
    `processed_params` shape.

    Meta passes `components=[{"type": "body", "parameters": [{"type": "text",
    "text": "John"}, ...]}]`; Chatwoot wants `{"1": "John", "2": "..."}`
    keyed by 1-based position. Header/button parameters are dropped — only
    body text params translate; callers using rich template features should
    talk to Chatwoot's REST directly.
    """
    if not components:
        return {}
    for c in components:
        if c.get("type") == "body":
            params = c.get("parameters") or []
            return {
                str(i + 1): str(p.get("text", ""))
                for i, p in enumerate(params)
                if p.get("type") == "text"
            }
    return {}


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

    FA edition: when CHATWOOT_ENABLED is set, the message is routed through
    Chatwoot (which fronts WhatsApp Cloud) instead of going direct to Meta.
    Callers don't need to know — the return shape stays `{"ok": True,
    "message_id": <opaque>}`, only the id type changes (Chatwoot numeric id
    vs Meta wamid string). See docs/chatwoot.md.
    """
    body = collapse_soft_wraps(body)
    from roost.config import CHATWOOT_ENABLED
    if CHATWOOT_ENABLED:
        from roost.extras.messaging_external.services import chatwoot
        result = chatwoot.route_text_to_whatsapp(to, body)
        if "error" in result:
            return result
        logger.info(
            "WhatsApp via Chatwoot to %s: conv=%s msg=%s",
            to, result.get("conversation_id"), result.get("message_id"),
        )
        return {"ok": True, "message_id": result.get("message_id"),
                "via": "chatwoot",
                "conversation_id": result.get("conversation_id")}

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
    # FA edition: route through Chatwoot REST (`template_params` payload on
    # the same /messages endpoint). Meta `components` get translated to the
    # `processed_params` dict Chatwoot expects.
    from roost.config import CHATWOOT_ENABLED
    if CHATWOOT_ENABLED:
        from roost.extras.messaging_external.services import chatwoot
        processed = _components_to_processed_params(components)
        result = chatwoot.route_template_to_whatsapp(
            to,
            template_name,
            processed_params=processed,
            language=language_code,
        )
        if "error" in result:
            return result
        logger.info(
            "WhatsApp template '%s' via Chatwoot to %s: conv=%s msg=%s",
            template_name, to, result.get("conversation_id"),
            result.get("message_id"),
        )
        return {"ok": True, "message_id": result.get("message_id"),
                "via": "chatwoot",
                "conversation_id": result.get("conversation_id")}

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


def upload_media(path: str | Path, mime_type: str | None = None) -> dict:
    """Upload a local file to WhatsApp's media store.

    Returns {"ok": True, "media_id": "..."} or {"error": "..."}.
    The returned media_id is reusable for 30 days and lets us send the
    media multiple times without re-uploading.
    """
    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"error": "WhatsApp not configured"}

    p = Path(path)
    if not p.is_file():
        return {"error": f"file not found: {p}"}
    if mime_type is None:
        mime_type, _ = mimetypes.guess_type(str(p))
        mime_type = mime_type or "application/octet-stream"

    url = f"{BASE_URL}/{WHATSAPP_PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
    try:
        with httpx.Client(timeout=120) as client, p.open("rb") as fh:
            files = {"file": (p.name, fh, mime_type)}
            data = {"messaging_product": "whatsapp", "type": mime_type}
            resp = client.post(url, headers=headers, data=data, files=files)
            resp.raise_for_status()
            payload = resp.json()
            media_id = payload.get("id", "")
            if not media_id:
                return {"error": "no media id returned", "details": payload}
            logger.info("WhatsApp media uploaded: %s -> %s", p.name, media_id)
            return {"ok": True, "media_id": media_id, "mime_type": mime_type}
    except httpx.HTTPStatusError as e:
        details = e.response.json() if e.response.content else {}
        return {"error": f"WhatsApp upload {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("WhatsApp media upload failed")
        return {"error": str(e)}


def _send_media_message(
    to: str,
    media_type: str,
    *,
    path: str | Path | None = None,
    link: str | None = None,
    media_id: str | None = None,
    caption: str | None = None,
    filename: str | None = None,
) -> dict:
    """Shared implementation for send_document / send_image.

    Pick exactly one of `path` (local file, will be uploaded), `media_id`
    (already-uploaded reference), or `link` (publicly fetchable URL).
    """
    # FA edition: route through Chatwoot's multipart upload on the same
    # /messages endpoint. Only the local-path branch maps cleanly —
    # Chatwoot takes file bytes, not a Meta media_id or external link.
    from roost.config import CHATWOOT_ENABLED
    if CHATWOOT_ENABLED:
        if not path:
            return {"error": "Chatwoot media upload needs a local file path "
                    "(`link` and `media_id` are Meta-only); download the file "
                    "first and pass `path=`"}
        from roost.extras.messaging_external.services import chatwoot
        result = chatwoot.route_media_to_whatsapp(to, path, caption=caption or "")
        if "error" in result:
            return result
        logger.info(
            "WhatsApp %s via Chatwoot to %s: conv=%s msg=%s",
            media_type, to, result.get("conversation_id"),
            result.get("message_id"),
        )
        return {"ok": True, "message_id": result.get("message_id"),
                "via": "chatwoot",
                "conversation_id": result.get("conversation_id")}

    if not WHATSAPP_ACCESS_TOKEN or not WHATSAPP_PHONE_NUMBER_ID:
        return {"error": "WhatsApp not configured"}

    if media_type not in ("document", "image"):
        return {"error": f"unsupported media_type {media_type!r}"}

    media_obj: dict[str, Any] = {}
    if path:
        upload = upload_media(path)
        if upload.get("error"):
            return upload
        media_obj["id"] = upload["media_id"]
        if media_type == "document" and not filename:
            filename = Path(path).name
    elif media_id:
        media_obj["id"] = media_id
    elif link:
        media_obj["link"] = link
    else:
        return {"error": "one of path|media_id|link required"}

    if caption:
        media_obj["caption"] = caption[:1024]
    if filename and media_type == "document":
        media_obj["filename"] = filename

    url = f"{BASE_URL}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to.lstrip("+"),
        "type": media_type,
        media_type: media_obj,
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("messages", [{}])[0].get("id", "")
            logger.info("WhatsApp %s sent to %s: msg_id=%s", media_type, to, msg_id)
            return {"ok": True, "message_id": msg_id, "media_id": media_obj.get("id", "")}
    except httpx.HTTPStatusError as e:
        details = e.response.json() if e.response.content else {}
        logger.error("WhatsApp %s send error: %s %s", media_type, e.response.status_code, details)
        return {"error": f"WhatsApp API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("WhatsApp %s send failed", media_type)
        return {"error": str(e)}


def send_document(
    to: str,
    path: str | Path | None = None,
    *,
    link: str | None = None,
    media_id: str | None = None,
    caption: str | None = None,
    filename: str | None = None,
) -> dict:
    """Send a PDF / docx / spreadsheet via WhatsApp.

    Pass exactly one source: `path` (local file → uploaded automatically),
    `media_id` (already uploaded), or `link` (publicly reachable URL).
    """
    return _send_media_message(
        to, "document",
        path=path, link=link, media_id=media_id,
        caption=caption, filename=filename,
    )


def send_image(
    to: str,
    path: str | Path | None = None,
    *,
    link: str | None = None,
    media_id: str | None = None,
    caption: str | None = None,
) -> dict:
    """Send a JPG / PNG via WhatsApp. See send_document for source semantics."""
    return _send_media_message(
        to, "image",
        path=path, link=link, media_id=media_id, caption=caption,
    )


def mark_as_read(message_id: str) -> dict:
    """Mark a received message as read (sends blue ticks).

    Args:
        message_id: The wamid of the message to mark as read.

    FA edition: Chatwoot owns inbound receipts on its inbox — the
    `update_last_seen` bump fires from the api_chatwoot router's inbound
    handler instead. This becomes a no-op so existing call sites don't
    need to branch.
    """
    from roost.config import CHATWOOT_ENABLED
    if CHATWOOT_ENABLED:
        return {"ok": True, "via": "chatwoot", "no_op": True}

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
