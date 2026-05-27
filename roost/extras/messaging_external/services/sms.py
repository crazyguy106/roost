"""SMS adapter — Twilio outbound REST.

Thin wrapper over Twilio's Messages API. Plain httpx + HTTP Basic auth so
we don't pull in the Twilio SDK (one extra dep for one endpoint).

Fail-closed: if `SMS_ENABLED` is off or credentials are missing the function
returns an error envelope instead of raising — matches the WhatsApp /
DNC / CDD pattern documented in CLAUDE.md ("Adapters fail closed").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

import httpx

from roost.config import (
    SMS_ENABLED,
    SMS_PROVIDER,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_FROM_NUMBER,
)

logger = logging.getLogger("roost.sms")

_TWILIO_BASE = "https://api.twilio.com/2010-04-01"


def verify_twilio_signature(url: str, params: dict, signature: str) -> bool:
    """Validate an inbound Twilio webhook against X-Twilio-Signature.

    Twilio signs:  base64(HMAC-SHA1(AuthToken, full_url + concat(sorted(key+value))))
    `params` is the POST form-data (already decoded). `url` must be the
    exact URL Twilio called, including scheme + host + path + querystring
    in the order configured in the Twilio console.

    Empty signature or missing auth token → fail-closed (False).
    """
    if not (signature and TWILIO_AUTH_TOKEN):
        return False
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params.keys()))
    mac = hmac.new(TWILIO_AUTH_TOKEN.encode(), data.encode(), hashlib.sha1)
    expected = base64.b64encode(mac.digest()).decode()
    return hmac.compare_digest(expected, signature)


def send_sms(to: str, body: str) -> dict:
    """Send a single SMS message.

    Args:
        to: Recipient phone number in E.164 format (e.g. '+6591234567').
        body: Message text. Twilio splits >160 GSM-7 chars into segments
              automatically and bills per segment.

    Returns:
        {"ok": True, "message_id": "SM..."} on success
        {"ok": False, "error": "..."} on disabled/misconfigured/API errors
    """
    if not SMS_ENABLED:
        return {"ok": False, "error": "SMS not enabled (SMS_ENABLED=false)"}

    if SMS_PROVIDER != "twilio":
        return {"ok": False, "error": f"Unsupported SMS provider: {SMS_PROVIDER}"}

    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        return {
            "ok": False,
            "error": "Twilio not configured (account SID / auth token / from-number missing)",
        }

    url = f"{_TWILIO_BASE}/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    data = {"To": to, "From": TWILIO_FROM_NUMBER, "Body": body}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                url,
                data=data,
                auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            )
            resp.raise_for_status()
            payload = resp.json()
            sid = payload.get("sid", "")
            logger.info("SMS sent to %s: sid=%s", to, sid)
            return {"ok": True, "message_id": sid, "provider": "twilio"}
    except httpx.HTTPStatusError as e:
        details = {}
        try:
            details = e.response.json()
        except Exception:
            details = {"body": e.response.text[:500]}
        logger.error("Twilio API %s: %s", e.response.status_code, details)
        return {
            "ok": False,
            "error": f"Twilio API {e.response.status_code}",
            "details": details,
        }
    except Exception as e:
        logger.exception("SMS send failed")
        return {"ok": False, "error": str(e)}
