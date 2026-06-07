"""Chatwoot webhook endpoint.

Handles:
- GET  /api/chatwoot/webhook — liveness probe (Chatwoot has no Meta-style
                               verification handshake; a 200 here just
                               confirms the URL is reachable from the user's
                               browser when setting up the integration).
- POST /api/chatwoot/webhook — Inbound events from Chatwoot.

Critical rule from `docs/chatwoot-webhook-samples/README.md` quirk #6:
**only `message_created` with `message_type == "incoming"` runs the AI
pipeline.** `conversation_updated` fires alongside almost every conversation
mutation (status flips, agent typing, label changes) — routing it through
recipes / Telegram notify / drafts would flood the operator. Outgoing
messages, system activity messages, and contact/conversation lifecycle
events are acked with 200 and ignored.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from roost.config import CHATWOOT_ENABLED

router = APIRouter(prefix="/api/chatwoot", tags=["chatwoot"])
_logger = logging.getLogger("roost.web.chatwoot")


# Keyword sets mirror the WhatsApp/SMS adapters so STOP/HELP UX is uniform
# across customer channels.
_STOP_KEYWORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}
_HELP_KEYWORDS = {"HELP", "INFO"}


def _first_token(body: str) -> str:
    """Return the first whitespace-delimited token, uppercased and stripped
    of common punctuation. Matches the api_whatsapp.py implementation."""
    if not body:
        return ""
    parts = body.strip().split()
    if not parts:
        return ""
    return parts[0].upper().strip(".,!?;:'\"")


@router.get("/webhook")
def liveness():
    """Liveness probe — Chatwoot doesn't do a verification handshake, so
    this exists only to let the user confirm the URL is reachable when
    pasting it into Chatwoot's UI."""
    if not CHATWOOT_ENABLED:
        raise HTTPException(status_code=404, detail="Chatwoot not enabled")
    return {"ok": True, "adapter": "chatwoot"}


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Receive a Chatwoot webhook delivery.

    Flow:
    1. Verify X-Chatwoot-Timestamp + X-Chatwoot-Signature
    2. Parse and normalise the envelope
    3. Dispatch only `message_created` + `incoming` into the AI pipeline;
       all other events return 200 ack with no side effects
    """
    if not CHATWOOT_ENABLED:
        raise HTTPException(status_code=404, detail="Chatwoot not enabled")

    body = await request.body()
    ts = request.headers.get("X-Chatwoot-Timestamp", "")
    signature = request.headers.get("X-Chatwoot-Signature", "")
    delivery_id = request.headers.get("X-Chatwoot-Delivery", "")

    from roost.extras.messaging_external.services.chatwoot import (
        parse_webhook_event,
        verify_webhook_signature,
    )

    # Signature is mandatory in production; reject if absent or wrong.
    # Returning 401 (not 500) means Chatwoot will retry sanely.
    if not verify_webhook_signature(ts, body, signature):
        _logger.warning(
            "Chatwoot webhook signature/ts rejected delivery=%s", delivery_id,
        )
        raise HTTPException(status_code=401, detail="Invalid signature")

    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = parse_webhook_event(payload)
    event = parsed["event"]

    # Only inbound messages drive the pipeline. Everything else is a
    # structural / lifecycle event — ack and move on. See quirk #6.
    if event != "message_created" or parsed["message_type"] != "incoming":
        _logger.debug(
            "Chatwoot %s delivery=%s — ignored (not an inbound message)",
            event, delivery_id,
        )
        return JSONResponse(content={"ok": True, "ignored": event})

    # Filter out empty / activity-only content (Chatwoot uses
    # message_created with message_type=activity for system notes, but
    # those have message_type="activity" at the top level, not "incoming"
    # — so they're already filtered above. Belt-and-braces:).
    if not parsed["content"]:
        return JSONResponse(content={"ok": True, "ignored": "empty_content"})

    _logger.info(
        "Chatwoot inbound conv=%s contact=%s channel=%s: %s",
        parsed["conversation_id"],
        parsed["contact"].get("phone") or parsed["source_id"],
        parsed["channel"],
        parsed["content"][:100],
    )

    # Buffer + STOP/HELP intercept mirror the WhatsApp adapter so the
    # pipeline behaves identically regardless of which channel an inbound
    # message arrived on.
    keyword = _first_token(parsed["content"])
    if keyword in _STOP_KEYWORDS or keyword in _HELP_KEYWORDS:
        await _process_inbound(parsed)
    else:
        try:
            from roost.extras.messaging_external.services.inbound_buffer import (
                submit,
            )
            buffer_key = parsed["contact"].get("phone") or parsed["source_id"]
            await submit(
                channel="chatwoot",
                sender=buffer_key,
                message=parsed,
                processor=_process_inbound,
            )
        except Exception:
            # If the buffer is unavailable, run inline rather than dropping.
            _logger.exception("inbound buffer submit failed; running inline")
            await _process_inbound(parsed)

    return JSONResponse(content={"ok": True, "delivery": delivery_id})


async def _process_inbound(parsed: dict) -> None:
    """Process a single inbound Chatwoot message.

    Mirrors api_whatsapp._process_inbound: log to conversation, STOP/HELP
    intercepts, qualification dialog, mark-inbound-for-contact, lead ingest,
    recipe pipeline, Telegram notify. The only Chatwoot-specific piece is
    the reply path (back via send_message on the Chatwoot conversation,
    not direct to the channel).
    """
    from roost.extras.messaging_external.services import chatwoot as chatwoot_svc
    from roost.extras.messaging_external.services.ai_cdr import classify_message
    from roost.services.recipes import execute_recipe, list_recipes

    conversation_id = parsed.get("conversation_id")
    contact = parsed.get("contact") or {}
    sender_name = contact.get("name", "")
    sender_phone = contact.get("phone", "") or parsed.get("source_id", "")
    text = parsed.get("content", "")

    # Mark read so the agent UI clears the unread badge promptly. Best-effort.
    if conversation_id is not None:
        try:
            chatwoot_svc.mark_as_read(conversation_id)
        except Exception:
            _logger.debug("Chatwoot mark_as_read failed (non-fatal)")

    # Conversation log — keeps /leads detail consistent across channels.
    try:
        from roost.extras.lead_nurture.services import conversation as conv_log
        conv_log.log_message(
            channel="chatwoot",
            identifier=sender_phone,
            direction="in",
            body=text,
            sender_name=sender_name if sender_name != sender_phone else "",
        )
    except Exception:
        _logger.exception("inbound conversation log failed (non-fatal)")

    keyword = _first_token(text)

    # ── STOP — unsubscribe + confirm, skip everything else. ──────────
    if keyword in _STOP_KEYWORDS:
        try:
            from roost.extras.lead_nurture.services.cadences import (
                store as cadences_store,
            )
            n = cadences_store.exit_enrollments_by_contact(
                phone=sender_phone, reason="opted_out:chatwoot",
            )
            _logger.info(
                "Chatwoot STOP from %s — exited %d enrollments",
                sender_phone, n,
            )
        except Exception:
            _logger.exception(
                "Chatwoot STOP exit-enrollments failed (non-fatal)"
            )
        if conversation_id is not None:
            chatwoot_svc.send_message(
                conversation_id,
                "You've been unsubscribed and will not receive further "
                "messages. Reply START to resubscribe.",
            )
        return

    # ── HELP — support info, skip everything else. ───────────────────
    if keyword in _HELP_KEYWORDS:
        if conversation_id is not None:
            chatwoot_svc.send_message(
                conversation_id,
                "Reply STOP to unsubscribe. For support contact "
                "support@verixiom.com.",
            )
        return

    # Automation gate — pause switch + recency window. STOP/HELP above
    # always run; everything below (qualify / continue / draft) is skipped
    # when gated, so the message simply waits in the inbox for a human.
    try:
        from roost.extras.lead_nurture.services.gating import automation_gate
        gate = automation_gate(phone=sender_phone)
    except Exception:
        gate = {"proceed": True, "reason": "ok"}
    if not gate.get("proceed"):
        _logger.info(
            "Chatwoot inbound from %s gated (%s) — leaving for a human",
            sender_phone, gate.get("reason"),
        )
        try:
            from roost.extras.messaging_external.services.operator_notify import (
                notify_gated,
            )
            await notify_gated(
                channel="chatwoot",
                who=(sender_name or sender_phone),
                text=text, gate=gate,
            )
        except Exception:
            _logger.exception("gated operator notify failed (non-fatal)")
        return

    # Qualification intercept — same pattern as api_whatsapp.
    try:
        from roost.extras.lead_nurture.services import qualification
        q_result = qualification.process_answer(
            channel="chatwoot", identifier=sender_phone, text=text,
        )
        if q_result.get("handled"):
            _logger.info(
                "Chatwoot qualification handled %s done=%s",
                sender_phone, q_result.get("done"),
            )
            return
    except Exception:
        _logger.exception("qualification intercept failed (non-fatal)")

    # Mark active/paused nurture enrollments — wait_for_reply detection.
    try:
        from roost.extras.lead_nurture.services.cadences import (
            store as cadences_store,
        )
        cadences_store.mark_inbound_for_contact(phone=sender_phone)
    except Exception:
        _logger.exception(
            "mark_inbound_for_contact (chatwoot) failed (non-fatal)"
        )

    # Best-effort lead ingest. If it auto-starts a qualification dialog
    # (sends the lead the first question), that IS the reply — return
    # before the recipe / classify branch so the lead doesn't also get an
    # AI draft on the same inbound message.
    try:
        from roost.extras.lead_nurture.services import leads as leads_svc
        from roost.extras.lead_nurture.services import settings as ln_settings
        ingest_result = leads_svc.ingest_lead(
            channel="chatwoot",
            phone=sender_phone,
            name=sender_name if sender_name != sender_phone else "",
            message_text=text,
            vertical=ln_settings.get("default_vertical", "property"),
            source="chatwoot",
            qualifying_identifier=sender_phone,
        )
        if isinstance(ingest_result, dict) and ingest_result.get("qualification_started"):
            _logger.info(
                "Chatwoot inbound from %s started qualification — skipping recipe",
                sender_phone,
            )
            return
    except Exception:
        _logger.exception("lead ingest from Chatwoot failed (non-fatal)")

    # Auto-reply is enabled by the presence of an enabled `chatwoot_inbound`
    # event recipe (the on-switch). The reply itself is a free-form AI draft,
    # not a canned template — so it's held by Guardian below.
    recipes = list_recipes(trigger_type="event", enabled_only=True)
    cw_recipes = [
        r for r in recipes
        if r.get("trigger_config", "") == "chatwoot_inbound"
    ]

    draft = ""
    if cw_recipes:
        # Context-pull (Co-Work move 1): read the recent thread, then have
        # Gemini draft a contextual, MAS-aware reply.
        from roost.extras.messaging_external.services.ai_cdr import draft_reply
        from roost.extras.lead_nurture.services.conversation import recent_context
        ctx = recent_context(channel="chatwoot", identifier=sender_phone)
        draft = await draft_reply(
            text, sender=sender_name or sender_phone, context=ctx,
        )

    if draft and conversation_id is not None:
        from roost.config import GUARDIAN_ENABLED
        if GUARDIAN_ENABLED:
            # Hand the client-facing reply to Guardian: it holds the draft and
            # pings the adviser to Approve/Reject in Telegram before the
            # customer ever sees it. Only on approval does Guardian's executor
            # send it.
            from roost.services.guardian import guardian_gate
            guardian_gate("send_client_reply", {
                "channel": "chatwoot",
                "conversation_id": conversation_id,
                "text": draft,
                "contact": sender_name or sender_phone,
            })
        else:
            # No Guardian to hold it — surface the draft for a manual send;
            # never auto-send an unapproved client message.
            await _notify_telegram(parsed, {
                "classification": {}, "status": "draft_no_guardian",
                "draft": draft,
            })
    else:
        classification = await classify_message(
            message=text, sender=sender_name or sender_phone,
        )
        await _notify_telegram(parsed, {
            "classification": classification,
            "status": "classified_only",
            "draft": "",
        })


async def _notify_telegram(parsed: dict, result: dict) -> None:
    """Send a Telegram notification about the Chatwoot message + classification."""
    try:
        from roost.config import TELEGRAM_ALLOWED_USERS, TELEGRAM_BOT_TOKEN
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_USERS:
            return

        import httpx

        contact = parsed.get("contact") or {}
        sender = contact.get("name") or contact.get("phone") or parsed.get("source_id", "Unknown")
        text_preview = parsed.get("content", "")[:200]
        classification = result.get("classification", {})
        intent = classification.get("intent", "unknown")
        urgency = classification.get("urgency", "cold")
        draft = result.get("draft", "")
        status = result.get("status", "")
        run_id = result.get("run_id", "")

        lines = [
            f"Chatwoot from {sender}:",
            f'"{text_preview}"',
            f"Intent: {intent} | Urgency: {urgency}",
        ]
        if draft:
            lines.append(f"\nDraft reply:\n{draft[:500]}")

        reply_markup: dict | None = None
        if status == "awaiting_approval" and run_id:
            reply_markup = {"inline_keyboard": [[
                {"text": "✅ Approve", "callback_data": f"recipe:approve:{run_id}"},
                {"text": "✏️ Edit", "callback_data": f"recipe:edit:{run_id}"},
                {"text": "⏭ Skip", "callback_data": f"recipe:skip:{run_id}"},
            ]]}

        message = "\n".join(lines)
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            for user_id in TELEGRAM_ALLOWED_USERS:
                try:
                    payload: dict = {"chat_id": user_id, "text": message}
                    if reply_markup is not None:
                        payload["reply_markup"] = reply_markup
                    await client.post(url, json=payload)
                except Exception:
                    _logger.debug("Failed to notify Telegram user %s", user_id)
    except Exception:
        _logger.exception("Telegram notification failed")
