"""WhatsApp Cloud API webhook endpoint.

Handles:
- GET /api/whatsapp/webhook — Meta verification challenge
- POST /api/whatsapp/webhook — Inbound message processing via AI CDR pipeline

Inbound messages are classified using the tool-less AI CDR sandbox,
matched to response templates, and held for approval (external_write tier).
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from roost.config import WHATSAPP_ENABLED, WHATSAPP_VERIFY_TOKEN

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])
_logger = logging.getLogger("roost.web.whatsapp")


# Mirror api_sms.py's keyword set — STOP/HELP behaves the same across every
# customer channel. WhatsApp has no Twilio-equivalent "advisory keywords"
# requirement, but consistent UX matters more than the Meta minimum.
_STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
_HELP_KEYWORDS = {"HELP", "INFO"}


def _first_token(body: str) -> str:
    """Return the first whitespace-delimited token, uppercased and stripped
    of common punctuation. 'stop.' / 'Stop!' / ' STOP ' all → 'STOP'."""
    if not body:
        return ""
    parts = body.strip().split()
    if not parts:
        return ""
    return parts[0].upper().strip(".,!?;:'\"")


@router.get("/webhook")
def verify_webhook(
    hub_mode: str = Query("", alias="hub.mode"),
    hub_verify_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """Meta webhook verification (subscribe handshake).

    Meta sends a GET with hub.mode=subscribe, hub.verify_token, hub.challenge.
    We must return the challenge value if the token matches.
    """
    if not WHATSAPP_ENABLED:
        raise HTTPException(status_code=404, detail="WhatsApp not enabled")

    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        _logger.info("WhatsApp webhook verified")
        return PlainTextResponse(content=hub_challenge)

    _logger.warning("WhatsApp webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Receive inbound WhatsApp messages and process via AI CDR.

    Flow:
    1. Verify X-Hub-Signature-256
    2. Parse message(s) from webhook payload
    3. For each text message: run recipe pipeline (CDR classify → template → draft)
    4. Notify via Telegram for approval
    """
    if not WHATSAPP_ENABLED:
        raise HTTPException(status_code=404, detail="WhatsApp not enabled")

    # Verify signature
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    from roost.extras.messaging_external.services.whatsapp import verify_webhook_signature
    if signature and not verify_webhook_signature(body, signature):
        _logger.warning("WhatsApp webhook signature mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")

    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Meta sends a test notification on subscribe — just acknowledge
    obj = payload.get("object", "")
    if obj != "whatsapp_business_account":
        return JSONResponse(content={"ok": True})

    # Parse inbound messages
    from roost.extras.messaging_external.services.whatsapp import parse_webhook_entry

    processed = 0
    for entry in payload.get("entry", []):
        messages = parse_webhook_entry(entry)

        for msg in messages:
            if not msg.get("text"):
                continue

            _logger.info(
                "WhatsApp inbound from %s (%s): %s",
                msg["sender"], msg["sender_name"], msg["text"][:100],
            )

            # Mark as read
            from roost.extras.messaging_external.services.whatsapp import mark_as_read
            mark_as_read(msg["message_id"])

            # Find a WhatsApp recipe to run, or use default classification
            await _process_inbound(msg)
            processed += 1

    return JSONResponse(content={"ok": True, "processed": processed})


async def _process_inbound(msg: dict) -> None:
    """Process a single inbound WhatsApp message.

    Looks for an enabled WhatsApp recipe. If found, runs the full
    recipe pipeline (CDR classify → template select → draft → approval).
    Otherwise, just classifies and notifies via Telegram.
    """
    from roost.services.recipes import list_recipes, execute_recipe
    from roost.extras.messaging_external.services.ai_cdr import classify_message

    sender = msg.get("sender_name") or msg.get("sender", "")
    text = msg["text"]
    sender_phone = msg.get("sender", "")

    # ── STOP — unsubscribe + confirm, skip everything else. ─────────
    # Customers can opt out from any channel. We exit every enrollment
    # for this phone (active or paused) and reply with the standard
    # confirmation. The recipe pipeline does NOT run — STOP is not an
    # inbound that operators should draft a reply to.
    keyword = _first_token(text)
    if keyword in _STOP_KEYWORDS:
        try:
            from roost.extras.lead_nurture.services.cadences import (
                store as cadences_store,
            )
            n = cadences_store.exit_enrollments_by_contact(
                phone=sender_phone, reason="opted_out:whatsapp",
            )
            _logger.info(
                "WhatsApp STOP from %s — exited %d enrollments",
                sender_phone, n,
            )
        except Exception:
            _logger.exception(
                "WhatsApp STOP exit-enrollments failed (non-fatal)"
            )
        try:
            from roost.extras.messaging_external.services.whatsapp import (
                send_text_message,
            )
            send_text_message(
                to=sender_phone,
                body=("You've been unsubscribed and will not receive further "
                      "messages. Reply START to resubscribe."),
            )
        except Exception:
            _logger.exception("WhatsApp STOP reply send failed (non-fatal)")
        return

    # ── HELP — support info, skip everything else. ───────────────────
    if keyword in _HELP_KEYWORDS:
        try:
            from roost.extras.messaging_external.services.whatsapp import (
                send_text_message,
            )
            send_text_message(
                to=sender_phone,
                body=("Reply STOP to unsubscribe. For support contact "
                      "support@verixiom.com."),
            )
        except Exception:
            _logger.exception("WhatsApp HELP reply send failed (non-fatal)")
        return

    # Qualification intercept: if this phone has an in-progress qualifying
    # session, treat the reply as an answer and stop here. The recipe
    # pipeline (auto-replies, drafts) would otherwise step on the
    # qualification dialog.
    try:
        from roost.extras.lead_nurture.services import qualification
        q_result = qualification.process_answer(
            channel="whatsapp", identifier=sender_phone, text=text,
        )
        if q_result.get("handled"):
            _logger.info(
                "WhatsApp qualification handled %s done=%s",
                sender_phone, q_result.get("done"),
            )
            return
    except Exception:
        _logger.exception("qualification intercept failed (non-fatal)")

    # Mark any active/paused nurture enrollments for this contact so the
    # wait_for_reply step can detect engagement. Best-effort: a failure
    # here must not interrupt ingest or the recipe pipeline.
    try:
        from roost.extras.lead_nurture.services.cadences import (
            store as cadences_store,
        )
        cadences_store.mark_inbound_for_contact(phone=sender_phone)
    except Exception:
        _logger.exception(
            "mark_inbound_for_contact (whatsapp) failed (non-fatal)"
        )

    # Best-effort lead ingest: dedupes on phone via CRM, enrolls in default
    # property cadence if new. Never blocks the recipe pipeline.
    try:
        from roost.extras.lead_nurture.services import leads as leads_svc
        leads_svc.ingest_lead(
            channel="whatsapp",
            phone=sender_phone,
            name=sender if sender != sender_phone else "",
            message_text=text,
            vertical="property",
            source="whatsapp",
            qualifying_identifier=sender_phone,
        )
    except Exception:
        _logger.exception("lead ingest from WhatsApp failed (non-fatal)")

    # Find enabled event-triggered recipes for WhatsApp
    recipes = list_recipes(trigger_type="event", enabled_only=True)
    wa_recipes = [
        r for r in recipes
        if r.get("trigger_config", "") == "whatsapp_inbound"
    ]

    if wa_recipes:
        # Run the first matching recipe
        recipe = wa_recipes[0]
        result = await execute_recipe(
            recipe_id=recipe["id"],
            message=text,
            sender=sender,
            trigger_data={
                "source": "whatsapp",
                "sender": msg.get("sender", ""),
                "sender_name": sender,
                "message_id": msg.get("message_id", ""),
            },
        )

        # Notify via Telegram
        await _notify_telegram(msg, result)
    else:
        # No recipe — just classify and notify
        classification = await classify_message(message=text, sender=sender)
        await _notify_telegram(msg, {
            "classification": classification,
            "status": "classified_only",
            "draft": "",
        })


async def _notify_telegram(msg: dict, result: dict) -> None:
    """Send a Telegram notification about the WhatsApp message + classification."""
    try:
        from roost.config import TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_USERS:
            return

        import httpx

        sender = msg.get("sender_name") or msg.get("sender", "Unknown")
        text_preview = msg.get("text", "")[:200]
        classification = result.get("classification", {})
        intent = classification.get("intent", "unknown")
        urgency = classification.get("urgency", "cold")
        draft = result.get("draft", "")
        status = result.get("status", "")
        run_id = result.get("run_id", "")

        lines = [
            f"WhatsApp from {sender}:",
            f'"{text_preview}"',
            f"Intent: {intent} | Urgency: {urgency}",
        ]

        if draft:
            lines.append(f"\nDraft reply:\n{draft[:500]}")

        if status == "awaiting_approval" and run_id:
            lines.append(f"\n/approve_{run_id} to send | /skip_{run_id} to discard")

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
        _logger.exception("Telegram notification failed")
