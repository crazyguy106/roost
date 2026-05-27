"""Attio webhook endpoint — react to deal stage changes.

Wired stages (case-insensitive, matches `roost.extras.lead_nurture.services.cadences.store`):

* `won` / `closed won` / `lost` / `closed lost` / `disqualified` / `unqualified`
  / `do not contact` → exit every active or paused enrollment for the deal.
* `qualified` / `meeting booked` / `demo scheduled` / `negotiation`
  / `proposal sent` / `in conversation` → pause every active enrollment
  (a human is now driving — automation steps back).

All other stage values are no-ops. The endpoint is signature-verified with
HMAC-SHA256 over the raw request body using `ATTIO_WEBHOOK_SECRET`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from roost.config import ATTIO_WEBHOOK_SECRET
# cadences imported lazily inside the handler — keeps crm bundle independent
# of lead_nurture when LEAD_NURTURE_ENABLED is off.

router = APIRouter(prefix="/api/attio", tags=["attio"])
_logger = logging.getLogger("roost.web.attio_webhook")


def _verify(body: bytes, signature: str) -> bool:
    """HMAC-SHA256 verify. Header may be `sha256=<hex>` or raw hex."""
    if not ATTIO_WEBHOOK_SECRET or not signature:
        return False
    sig = signature.split("=", 1)[1] if "=" in signature else signature
    expected = hmac.new(
        ATTIO_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _extract_stage_change(event: dict) -> tuple[str, str] | None:
    """Pull (deal_id, new_stage) out of an Attio event.

    Attio's payload shape varies by event type; we accept the common forms:
    - record.updated with `target_object == 'deals'` and a `stage` field
      change in `actions` or directly under `target_record.values.stage`.
    - record.created on a deal with a stage value (rare but harmless).
    """
    obj = (event.get("target_object") or "").lower()
    if obj not in ("deals", "deal"):
        return None
    target = event.get("target_record") or event.get("target") or {}
    deal_id = (
        target.get("record_id")
        or target.get("id")
        or event.get("record_id")
        or ""
    )
    if not deal_id:
        return None

    # Try a few likely places for the stage value.
    stage = ""
    values = target.get("values") or {}
    stage_value = values.get("stage") or values.get("Stage")
    if isinstance(stage_value, list) and stage_value:
        first = stage_value[0]
        if isinstance(first, dict):
            stage = first.get("status") or first.get("title") or first.get("value") or ""
        elif isinstance(first, str):
            stage = first
    elif isinstance(stage_value, dict):
        stage = stage_value.get("status") or stage_value.get("title") or ""
    elif isinstance(stage_value, str):
        stage = stage_value

    # Diff-style payloads put the new value in actions[].new_value
    if not stage:
        for action in event.get("actions", []) or []:
            if (action.get("attribute") or "").lower() == "stage":
                nv = action.get("new_value") or action.get("value")
                if isinstance(nv, dict):
                    stage = nv.get("status") or nv.get("title") or ""
                elif isinstance(nv, str):
                    stage = nv
                if stage:
                    break

    if not stage:
        return None
    return deal_id, stage


@router.post("/webhook")
async def receive_webhook(request: Request):
    """Receive Attio events and apply nurture-cadence stage policy."""
    if not ATTIO_WEBHOOK_SECRET:
        raise HTTPException(status_code=404, detail="Attio webhook not configured")

    body = await request.body()
    signature = (
        request.headers.get("X-Attio-Signature")
        or request.headers.get("X-Hub-Signature-256")
        or ""
    )
    if not _verify(body, signature):
        _logger.warning("Attio webhook signature mismatch")
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    events = payload.get("events") or [payload]
    applied = 0
    actions: list[dict] = []
    from roost.extras.lead_nurture.services import cadences as cadences_svc
    for event in events:
        change = _extract_stage_change(event)
        if not change:
            continue
        deal_id, new_stage = change
        result = cadences_svc.handle_stage_change(deal_id, new_stage)
        applied += int(result.get("applied", 0))
        actions.append({
            "deal_id": deal_id,
            "stage": new_stage,
            "action": result.get("action"),
            "applied": result.get("applied", 0),
        })
        _logger.info(
            "Attio stage change deal=%s stage=%s -> %s (%d enrollments)",
            deal_id, new_stage, result.get("action"), result.get("applied", 0),
        )

    return JSONResponse({"ok": True, "applied": applied, "actions": actions})
