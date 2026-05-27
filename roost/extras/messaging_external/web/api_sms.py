"""Twilio SMS inbound webhook.

POST /api/sms/webhook

Twilio sends form-encoded params (MessageSid, From, To, Body, NumMedia, …)
and an `X-Twilio-Signature` header that signs (URL + sorted form params)
with HMAC-SHA1 keyed on the account auth token.

Behaviour:
* `SMS_ENABLED=false` → 404 (bundle off).
* Signature mismatch → 403.
* Empty `Body` (delivery-status callbacks hitting the same URL) → silent
  empty TwiML ack.
* First-token STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT →
  `cadences.exit_enrollments_by_contact(phone=From, reason='opted_out:sms')`
  plus a confirmation TwiML message. Skips lead ingest.
* First-token HELP/INFO → support-info TwiML message. Skips lead ingest.
* Otherwise → stamps `last_inbound_at` on matching enrollments (so
  `wait_for_reply` gates fire) and calls
  `leads.ingest_lead(channel='sms', source='sms_inbound', ...)`. Any
  exception is logged and swallowed — we always return TwiML so Twilio
  does not retry-storm the endpoint.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from roost.config import SMS_ENABLED

router = APIRouter(prefix="/api/sms", tags=["sms"])
_logger = logging.getLogger("roost.web.sms")

_TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response/>'

# Twilio's documented STOP/HELP keyword set — see
# https://help.twilio.com/articles/223134027 ("Filtered phrases").
_STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
_HELP_KEYWORDS = {"HELP", "INFO"}


def _twiml(body: str = "") -> Response:
    """Return a TwiML response — empty unless `body` is supplied."""
    if body:
        xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{body}</Message></Response>'
    else:
        xml = _TWIML_EMPTY
    return Response(content=xml, media_type="application/xml")


def _first_token(body: str) -> str:
    """Return the first whitespace-delimited token, uppercased and stripped of
    common punctuation, so 'stop.' / 'Stop!' / ' STOP ' all normalise to 'STOP'."""
    if not body:
        return ""
    tok = body.strip().split()[0] if body.strip() else ""
    return tok.upper().strip(".,!?;:'\"")


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Ingest an inbound SMS as a lead.

    1. Verify X-Twilio-Signature against form params + URL.
    2. Parse From/Body/MessageSid.
    3. Call leads.ingest_lead(channel="sms", source="sms_inbound").
    4. Return empty TwiML.
    """
    if not SMS_ENABLED:
        raise HTTPException(status_code=404, detail="SMS not enabled")

    form = await request.form()
    params = {k: str(v) for k, v in form.items()}

    signature = request.headers.get("X-Twilio-Signature", "")
    # Twilio signs the URL it was configured to call. Behind a reverse
    # proxy this may not match request.url; that's a deployment concern
    # — the operator sets the webhook URL in the Twilio console, and we
    # use whatever Starlette saw (proxy headers respected if uvicorn was
    # started with --forwarded-allow-ips).
    from roost.extras.messaging_external.services.sms import verify_twilio_signature
    if not verify_twilio_signature(str(request.url), params, signature):
        _logger.warning("Twilio SMS webhook signature mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")

    sender = params.get("From", "")
    body = params.get("Body", "")
    message_sid = params.get("MessageSid", "")

    if not (sender and body):
        # Twilio always includes both for SMS — if missing, it's not a
        # message event we handle (e.g. delivery-status callback hitting
        # the same URL). Acknowledge silently.
        return _twiml()

    _logger.info("SMS inbound from %s (sid=%s): %s", sender, message_sid, body[:100])

    keyword = _first_token(body)

    if keyword in _STOP_KEYWORDS:
        try:
            from roost.extras.lead_nurture.services.cadences import store as cadences_store
            n = cadences_store.exit_enrollments_by_contact(
                phone=sender, reason="opted_out:sms"
            )
            _logger.info("SMS STOP from %s — exited %d enrollments", sender, n)
        except Exception:
            _logger.exception("SMS STOP exit-enrollments failed (non-fatal)")
        return _twiml(
            "You've been unsubscribed and will not receive further messages. "
            "Reply START to resubscribe."
        )

    if keyword in _HELP_KEYWORDS:
        return _twiml(
            "Reply STOP to unsubscribe. For support contact support@verixiom.com."
        )

    # Mark any active/paused nurture enrollments for this contact so the
    # wait_for_reply gate can detect engagement. Best-effort: a failure
    # here must not interrupt ingest.
    try:
        from roost.extras.lead_nurture.services.cadences import store as cadences_store
        cadences_store.mark_inbound_for_contact(phone=sender)
    except Exception:
        _logger.exception("mark_inbound_for_contact (sms) failed (non-fatal)")

    try:
        from roost.extras.lead_nurture.services import leads as leads_svc
        leads_svc.ingest_lead(
            channel="sms",
            phone=sender,
            message_text=body,
            source="sms_inbound",
            qualifying_identifier=sender,
        )
    except Exception:
        _logger.exception("lead ingest from SMS failed (non-fatal)")

    return _twiml()
