"""WeChat Official Account API client — send/receive messages via Tencent platform.

Uses the official WeChat Official Account API (公众号).
Supports customer service messages, template messages, and inbound webhook.

Token lifecycle: access_token expires every 7200s (2h) — auto-refreshed.
Rate limits: 45 messages/second, 600K messages/day.
"""

import hashlib
import json
import logging
import time
from typing import Any

import httpx

from roost.config import (
    WECHAT_APP_ID,
    WECHAT_APP_SECRET,
    WECHAT_TOKEN,
    WECHAT_ENCODING_AES_KEY,
)

logger = logging.getLogger("roost.wechat")

BASE_URL = "https://api.weixin.qq.com/cgi-bin"

# Token cache
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0}


def _get_access_token() -> str:
    """Get or refresh the WeChat access token.

    Tokens expire after 7200 seconds. We refresh 5 minutes early.
    """
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 300:
        return _token_cache["token"]

    if not WECHAT_APP_ID or not WECHAT_APP_SECRET:
        logger.warning("WECHAT_APP_ID or WECHAT_APP_SECRET not configured")
        return ""

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{BASE_URL}/token",
                params={
                    "grant_type": "client_credential",
                    "appid": WECHAT_APP_ID,
                    "secret": WECHAT_APP_SECRET,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        if "access_token" in data:
            _token_cache["token"] = data["access_token"]
            _token_cache["expires_at"] = now + data.get("expires_in", 7200)
            logger.info("WeChat access token refreshed (expires in %ds)", data.get("expires_in", 7200))
            return _token_cache["token"]
        else:
            errcode = data.get("errcode", "")
            errmsg = data.get("errmsg", "")
            logger.error("WeChat token error: %s %s", errcode, errmsg)
            return ""

    except Exception as e:
        logger.exception("Failed to refresh WeChat access token")
        return ""


def verify_webhook_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """Verify WeChat webhook signature.

    WeChat uses SHA1(sorted(token + timestamp + nonce)) for verification.
    """
    if not WECHAT_TOKEN:
        logger.warning("WECHAT_TOKEN not set — cannot verify webhook")
        return False

    values = sorted([WECHAT_TOKEN, timestamp, nonce])
    computed = hashlib.sha1("".join(values).encode()).hexdigest()
    return computed == signature


def send_text_message(to_openid: str, content: str) -> dict:
    """Send a customer service text message.

    Requires the user to have interacted with the Official Account
    within the last 48 hours (WeChat session window).

    Args:
        to_openid: Recipient's OpenID (WeChat user identifier).
        content: Message text (max 600 chars for customer service).
    """
    token = _get_access_token()
    if not token:
        return {"error": "WeChat not configured (no access token)"}

    url = f"{BASE_URL}/message/custom/send"
    payload = {
        "touser": to_openid,
        "msgtype": "text",
        "text": {"content": content[:600]},
    }

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                params={"access_token": token},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        errcode = data.get("errcode", 0)
        if errcode == 0:
            logger.info("WeChat message sent to %s", to_openid[:8])
            return {"ok": True}
        else:
            logger.error("WeChat send error: %s %s", errcode, data.get("errmsg", ""))
            return {"error": f"WeChat API error {errcode}: {data.get('errmsg', '')}"}

    except Exception as e:
        logger.exception("WeChat send failed")
        return {"error": str(e)}


def send_template_message(
    to_openid: str,
    template_id: str,
    data: dict[str, dict[str, str]],
    url: str = "",
    miniprogram: dict | None = None,
) -> dict:
    """Send a template message (for notifications outside 48h window).

    Template messages must be pre-approved by WeChat.

    Args:
        to_openid: Recipient's OpenID.
        template_id: WeChat-approved template ID.
        data: Template data fields, e.g. {"first": {"value": "Hello"}, "keyword1": {"value": "Order #123"}}.
        url: Optional URL to open when user taps the message.
        miniprogram: Optional mini-program link dict.
    """
    token = _get_access_token()
    if not token:
        return {"error": "WeChat not configured (no access token)"}

    api_url = f"{BASE_URL}/message/template/send"
    payload: dict[str, Any] = {
        "touser": to_openid,
        "template_id": template_id,
        "data": data,
    }
    if url:
        payload["url"] = url
    if miniprogram:
        payload["miniprogram"] = miniprogram

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                api_url,
                params={"access_token": token},
                json=payload,
            )
            resp.raise_for_status()
            result = resp.json()

        errcode = result.get("errcode", 0)
        if errcode == 0:
            msg_id = result.get("msgid", "")
            logger.info("WeChat template sent to %s: msgid=%s", to_openid[:8], msg_id)
            return {"ok": True, "message_id": str(msg_id)}
        else:
            return {"error": f"WeChat API error {errcode}: {result.get('errmsg', '')}"}

    except Exception as e:
        logger.exception("WeChat template send failed")
        return {"error": str(e)}


def parse_webhook_message(xml_data: str) -> dict:
    """Parse an inbound WeChat XML message into a dict.

    WeChat sends messages as XML, not JSON.

    Returns dict with: sender (FromUserName), receiver (ToUserName),
    message_id (MsgId), type (MsgType), text (Content), timestamp.
    """
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError:
        logger.warning("Failed to parse WeChat XML message")
        return {}

    msg = {
        "sender": root.findtext("FromUserName", ""),
        "receiver": root.findtext("ToUserName", ""),
        "message_id": root.findtext("MsgId", ""),
        "type": root.findtext("MsgType", ""),
        "timestamp": root.findtext("CreateTime", ""),
        "text": "",
    }

    msg_type = msg["type"]
    if msg_type == "text":
        msg["text"] = root.findtext("Content", "")
    elif msg_type == "event":
        msg["event"] = root.findtext("Event", "")
        msg["event_key"] = root.findtext("EventKey", "")
    elif msg_type == "image":
        msg["pic_url"] = root.findtext("PicUrl", "")
        msg["media_id"] = root.findtext("MediaId", "")
    elif msg_type == "voice":
        msg["media_id"] = root.findtext("MediaId", "")
        msg["recognition"] = root.findtext("Recognition", "")  # Speech-to-text
    elif msg_type == "location":
        msg["location_x"] = root.findtext("Location_X", "")
        msg["location_y"] = root.findtext("Location_Y", "")
        msg["label"] = root.findtext("Label", "")

    return msg


def build_text_reply(from_user: str, to_user: str, content: str) -> str:
    """Build an XML text reply for the WeChat webhook.

    WeChat expects XML responses to webhook POSTs (passive reply).
    Must reply within 5 seconds or WeChat retries.
    """
    timestamp = int(time.time())
    return (
        f"<xml>"
        f"<ToUserName><![CDATA[{from_user}]]></ToUserName>"
        f"<FromUserName><![CDATA[{to_user}]]></FromUserName>"
        f"<CreateTime>{timestamp}</CreateTime>"
        f"<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        f"</xml>"
    )
