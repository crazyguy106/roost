"""Web API for the Zapier ingress webhook (SME Ops bundle).

Bearer-token authenticated. Configure in Zapier as a "Webhooks by Zapier →
POST" action with custom header `Authorization: Bearer <ZAPIER_INGRESS_TOKEN>`
and JSON body shaped as `{"event": "...", "payload": {...}}`.

On valid POST: persists the envelope to `sme_ops_events`, fires a
`zapier_event` SOP trigger so any matching recipe runs, and returns the
event id.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Header, HTTPException

from roost.config import SME_OPS_ENABLED, ZAPIER_INGRESS_TOKEN
from roost.extras.sme_ops.services.zapier import record_event
from roost.services.sop_triggers import fire_event_sync

logger = logging.getLogger("roost.web.api_zapier")

router = APIRouter(prefix="/api/zapier", tags=["sme-ops", "zapier"])


def _check_auth(authorization: str | None) -> None:
    """Bearer-token check. 401 on miss/mismatch, 503 if no token configured."""
    if not ZAPIER_INGRESS_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="Zapier ingress not configured (ZAPIER_INGRESS_TOKEN unset)",
        )
    expected = f"Bearer {ZAPIER_INGRESS_TOKEN}"
    if not authorization or authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid bearer token")


@router.post("/inbound")
def zapier_inbound(
    payload: dict[str, Any] = Body(...),
    authorization: str | None = Header(default=None),
) -> dict:
    """Accept a Zapier webhook and dispatch as an SOP event.

    Expected body: `{"event": "<name>", "payload": {...}}`.
    """
    if not SME_OPS_ENABLED:
        raise HTTPException(status_code=404, detail="SME Ops bundle disabled")
    _check_auth(authorization)

    event = str(payload.get("event") or "").strip()
    if not event:
        raise HTTPException(status_code=400, detail="Missing 'event' field")
    body = payload.get("payload") or {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="'payload' must be an object")

    event_id = record_event("zapier", event, body)
    fire_event_sync(
        "zapier_event",
        {"zapier_event_name": event, "zapier_event_id": event_id, **body},
    )
    return {"ok": True, "event_id": event_id, "event": event}
