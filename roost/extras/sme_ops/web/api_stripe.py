"""Stripe webhook endpoint — verifies the Stripe-Signature header,
persists the event, and fires `stripe_event` SOP trigger.

Sig spec: https://stripe.com/docs/webhooks/signatures
Format:   `t=<unix>,v1=<hex>,v0=<hex>`
HMAC:     SHA-256 over `<unix>.<raw body>` keyed with the webhook secret.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from fastapi import APIRouter, Header, HTTPException, Request

from roost.config import (
    SME_OPS_ENABLED,
    STRIPE_ENABLED,
    STRIPE_WEBHOOK_SECRET,
)
from roost.extras.sme_ops.services.zapier import record_event
from roost.services.sop_triggers import fire_event_sync

logger = logging.getLogger("roost.web.api_stripe")

router = APIRouter(prefix="/api/stripe", tags=["sme-ops", "stripe"])

# Reject events older than 5 minutes — replay protection per Stripe's docs.
_TOLERANCE_S = 5 * 60


def _verify_signature(secret: str, raw_body: bytes, sig_header: str) -> None:
    """Raise HTTPException(401) on any failure."""
    if not sig_header:
        raise HTTPException(status_code=401, detail="Missing Stripe-Signature")
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    ts = parts.get("t")
    v1 = parts.get("v1")
    if not ts or not v1:
        raise HTTPException(status_code=401, detail="Malformed Stripe-Signature")
    try:
        ts_int = int(ts)
    except ValueError:
        raise HTTPException(status_code=401, detail="Bad timestamp")
    if abs(time.time() - ts_int) > _TOLERANCE_S:
        raise HTTPException(status_code=401, detail="Timestamp outside tolerance")
    expected = hmac.new(
        secret.encode("utf-8"),
        f"{ts}.".encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, v1):
        raise HTTPException(status_code=401, detail="Invalid signature")


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
) -> dict:
    if not (SME_OPS_ENABLED and STRIPE_ENABLED):
        raise HTTPException(status_code=404, detail="Stripe webhook disabled")
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="STRIPE_WEBHOOK_SECRET not configured",
        )
    raw = await request.body()
    _verify_signature(STRIPE_WEBHOOK_SECRET, raw, stripe_signature or "")

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not JSON")

    event_name = envelope.get("type", "unknown")
    event_id = record_event("stripe", event_name, envelope)
    fire_event_sync(
        "stripe_event",
        {
            "stripe_event_name": event_name,
            "stripe_event_id": event_id,
            "stripe_id": envelope.get("id"),
            "data": envelope.get("data", {}),
        },
    )
    return {"ok": True, "event_id": event_id, "event": event_name}
