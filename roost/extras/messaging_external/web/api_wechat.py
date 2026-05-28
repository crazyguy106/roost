"""WeChat Official Account webhook endpoint.

Handles:
- GET /api/wechat/webhook — WeChat server verification (echostr challenge)
- POST /api/wechat/webhook — Inbound message processing via AI CDR pipeline

WeChat sends XML payloads (not JSON). Passive replies must be XML within 5s.
For async processing, we reply "success" immediately and process in background.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from roost.config import WECHAT_ENABLED

router = APIRouter(prefix="/api/wechat", tags=["wechat"])
_logger = logging.getLogger("roost.web.wechat")


@router.get("/webhook")
def verify_webhook(
    signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query(""),
):
    """WeChat server verification handshake.

    WeChat sends GET with signature, timestamp, nonce, echostr.
    Must verify signature and return echostr to confirm.
    """
    if not WECHAT_ENABLED:
        raise HTTPException(status_code=404, detail="WeChat not enabled")

    from roost.extras.messaging_external.services.wechat import verify_webhook_signature

    if verify_webhook_signature(signature, timestamp, nonce):
        _logger.info("WeChat webhook verified")
        return PlainTextResponse(content=echostr)

    _logger.warning("WeChat webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Receive inbound WeChat messages and process via AI CDR.

    Flow:
    1. Verify signature from query params
    2. Parse XML message body
    3. For text messages: run recipe pipeline (CDR classify → template → draft)
    4. Reply with "success" (async processing) or passive XML reply
    """
    if not WECHAT_ENABLED:
        raise HTTPException(status_code=404, detail="WeChat not enabled")

    # Verify signature
    signature = request.query_params.get("signature", "")
    timestamp = request.query_params.get("timestamp", "")
    nonce = request.query_params.get("nonce", "")

    from roost.extras.messaging_external.services.wechat import verify_webhook_signature
    if not verify_webhook_signature(signature, timestamp, nonce):
        _logger.warning("WeChat webhook signature mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")

    # Parse XML body
    body = await request.body()
    xml_data = body.decode("utf-8")

    from roost.extras.messaging_external.services.wechat import parse_webhook_message
    msg = parse_webhook_message(xml_data)

    if not msg:
        return PlainTextResponse(content="success")

    msg_type = msg.get("type", "")

    # Handle subscription events
    if msg_type == "event":
        event = msg.get("event", "")
        if event == "subscribe":
            _logger.info("WeChat: new subscriber %s", msg.get("sender", "")[:8])
            from roost.extras.messaging_external.services.wechat import build_text_reply
            return PlainTextResponse(
                content=build_text_reply(
                    msg["sender"], msg["receiver"],
                    "Welcome! How can I help you today?",
                ),
                media_type="application/xml",
            )
        return PlainTextResponse(content="success")

    # Only process text messages
    if msg_type != "text" or not msg.get("text"):
        return PlainTextResponse(content="success")

    _logger.info(
        "WeChat inbound from %s: %s",
        msg["sender"][:8], msg["text"][:100],
    )

    # Hand to the fragment debouncer (see roost-config/settings.yaml).
    # WeChat requires the webhook to return "success" fast or it retries
    # — submit() returns as soon as the buffer is updated (no awaiting
    # the debounce window itself).
    import asyncio
    from roost.extras.messaging_external.services.inbound_buffer import submit
    asyncio.create_task(submit(
        channel="wechat",
        sender=msg.get("sender", ""),
        message=msg,
        processor=_process_inbound,
    ))

    # Reply "success" to prevent WeChat retry
    return PlainTextResponse(content="success")


async def _process_inbound(msg: dict) -> None:
    """Process a single inbound WeChat message.

    Runs CDR classification and template matching, then sends
    the response via customer service API (or notifies via Telegram).
    """
    from roost.services.recipes import list_recipes, execute_recipe
    from roost.extras.messaging_external.services.ai_cdr import classify_message

    sender = msg.get("sender", "")
    text = msg["text"]

    # Qualification intercept: WeChat addresses leads by openid (which the
    # parser puts in msg["sender"]). If an in-progress qualifying session
    # exists for this openid, treat the message as an answer and stop here.
    try:
        from roost.extras.lead_nurture.services import qualification
        q_result = qualification.process_answer(
            channel="wechat", identifier=sender, text=text,
        )
        if q_result.get("handled"):
            _logger.info(
                "WeChat qualification handled %s done=%s",
                sender[:8], q_result.get("done"),
            )
            return
    except Exception:
        _logger.exception("qualification intercept failed (non-fatal)")

    # Best-effort lead ingest. WeChat doesn't expose a phone number, so we
    # use a synthetic `wechat:<openid>` identifier in the phone slot so CRM
    # dedupe still works. The openid is also passed through
    # `qualifying_identifier` so the next reply can be routed back to
    # qualification.process_answer().
    try:
        from roost.extras.lead_nurture.services import leads as leads_svc
        leads_svc.ingest_lead(
            channel="wechat",
            phone=f"wechat:{sender}",
            name="",
            message_text=text,
            vertical="property",
            source="wechat",
            qualifying_identifier=sender,
            fields={"wechat_openid": sender},
        )
    except Exception:
        _logger.exception("lead ingest from WeChat failed (non-fatal)")

    # Find enabled event-triggered recipes for WeChat
    recipes = list_recipes(trigger_type="event", enabled_only=True)
    wc_recipes = [
        r for r in recipes
        if r.get("trigger_config", "") == "wechat_inbound"
    ]

    if wc_recipes:
        recipe = wc_recipes[0]
        result = await execute_recipe(
            recipe_id=recipe["id"],
            message=text,
            sender=sender,
            trigger_data={
                "source": "wechat",
                "sender": sender,
                "message_id": msg.get("message_id", ""),
            },
        )

        # For read_only/internal_write: auto-reply via customer service API
        risk_tier = result.get("risk_tier", "read_only")
        draft = result.get("draft", "")

        if risk_tier != "external_write" and draft:
            from roost.extras.messaging_external.services.wechat import send_text_message
            send_text_message(sender, draft)

        # Always notify via Telegram
        await _notify_telegram(msg, result)
    else:
        classification = await classify_message(message=text, sender=sender)
        await _notify_telegram(msg, {
            "classification": classification,
            "status": "classified_only",
            "draft": "",
        })


async def _notify_telegram(msg: dict, result: dict) -> None:
    """Send a Telegram notification about the WeChat message."""
    try:
        from roost.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_USERS:
            return

        import httpx

        sender = msg.get("sender", "Unknown")[:8]
        text_preview = msg.get("text", "")[:200]
        classification = result.get("classification", {})
        intent = classification.get("intent", "unknown")
        urgency = classification.get("urgency", "cold")
        draft = result.get("draft", "")
        status = result.get("status", "")
        run_id = result.get("run_id", "")

        lines = [
            f"WeChat from {sender}...:",
            f'"{text_preview}"',
            f"Intent: {intent} | Urgency: {urgency}",
        ]

        if draft:
            lines.append(f"\nDraft reply:\n{draft[:500]}")

        if status == "awaiting_approval" and run_id:
            lines.append(f"\n/approve {run_id} to send | /skiprun {run_id} to discard")

        message = "\n".join(lines)

        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            for user_id in TELEGRAM_ALLOWED_USERS:
                try:
                    await client.post(url, json={
                        "chat_id": user_id,
                        "text": message,
                    })
                except Exception:
                    _logger.debug("Failed to notify Telegram user %s", user_id)

    except Exception:
        _logger.exception("Telegram notification failed for WeChat message")
