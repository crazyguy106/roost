"""Shopify webhook endpoint.

Spec: https://shopify.dev/docs/apps/webhooks/configuration/https#step-5-verify-the-webhook
HMAC-SHA256 over the raw request body keyed with the shared secret;
result is base64-encoded in `X-Shopify-Hmac-Sha256`.

Topic comes via `X-Shopify-Topic` (e.g. "orders/create").
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from roost.config import (
    SHOPIFY_ENABLED,
    SHOPIFY_WEBHOOK_SECRET,
    SME_OPS_ENABLED,
)
from roost.extras.sme_ops.services.zapier import record_event
from roost.services.sop_triggers import fire_event_sync

logger = logging.getLogger("roost.web.api_shopify")

router = APIRouter(prefix="/api/shopify", tags=["sme-ops", "shopify"])


def _verify(secret: str, raw_body: bytes, header: str) -> None:
    if not header:
        raise HTTPException(status_code=401, detail="Missing X-Shopify-Hmac-Sha256")
    expected = base64.b64encode(
        hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, header):
        raise HTTPException(status_code=401, detail="Invalid HMAC")


@router.post("/webhook")
async def shopify_webhook(
    request: Request,
    x_shopify_hmac_sha256: str | None = Header(default=None),
    x_shopify_topic: str | None = Header(default=None),
) -> dict:
    if not (SME_OPS_ENABLED and SHOPIFY_ENABLED):
        raise HTTPException(status_code=404, detail="Shopify webhook disabled")
    if not SHOPIFY_WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503, detail="SHOPIFY_WEBHOOK_SECRET not configured",
        )
    raw = await request.body()
    _verify(SHOPIFY_WEBHOOK_SECRET, raw, x_shopify_hmac_sha256 or "")

    try:
        envelope = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not JSON")

    topic = (x_shopify_topic or "unknown").strip()
    event_id = record_event("shopify", topic, envelope)
    fire_event_sync(
        "shopify_event",
        {
            "shopify_topic": topic,
            "shopify_event_id": event_id,
            "data": envelope,
        },
    )
    return {"ok": True, "event_id": event_id, "topic": topic}
