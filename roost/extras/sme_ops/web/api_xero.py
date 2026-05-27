"""Xero OAuth2 + webhook endpoints (Phase 1B).

Routes
------
GET  /api/xero/oauth/start     → 302 to Xero authorize URL
GET  /api/xero/oauth/callback  → exchange code, store tokens, list tenants
POST /api/xero/webhook         → verify HMAC, persist event, fire SOP trigger

Webhook signature spec
----------------------
Header `x-xero-signature` is base64(HMAC-SHA256(raw_body, XERO_WEBHOOK_KEY)).
On valid sig → 200 (intent-to-receive accepts this).
On invalid sig → 401 (Xero will reject the endpoint registration).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from roost.config import (
    SME_OPS_ENABLED,
    XERO_CLIENT_ID,
    XERO_CLIENT_SECRET,
    XERO_ENABLED,
    XERO_WEBHOOK_KEY,
)
from roost.extras.sme_ops.services import xero_oauth
from roost.extras.sme_ops.services.zapier import record_event
from roost.services.sop_triggers import fire_event_sync

logger = logging.getLogger("roost.web.api_xero")

router = APIRouter(prefix="/api/xero", tags=["sme-ops", "xero"])


# ── OAuth ─────────────────────────────────────────────────────────────


@router.get("/oauth/start")
async def xero_oauth_start():
    if not (SME_OPS_ENABLED and XERO_ENABLED):
        raise HTTPException(status_code=404, detail="Xero disabled")
    if not (XERO_CLIENT_ID and XERO_CLIENT_SECRET):
        raise HTTPException(
            status_code=503,
            detail="XERO_CLIENT_ID / XERO_CLIENT_SECRET not configured",
        )
    if not xero_oauth.redirect_uri():
        raise HTTPException(
            status_code=503,
            detail="No redirect URI — set XERO_REDIRECT_URI or ROOST_DOMAIN",
        )
    state = secrets.token_urlsafe(24)
    url = xero_oauth.build_authorize_url(state)
    # State persistence is light-touch: encoded in cookie; user-facing
    # OAuth flow only — single-user instance, replay risk is low.
    resp = RedirectResponse(url, status_code=302)
    resp.set_cookie("xero_oauth_state", state, max_age=600,
                    httponly=True, samesite="lax")
    return resp


@router.get("/oauth/callback")
async def xero_oauth_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
):
    if not (SME_OPS_ENABLED and XERO_ENABLED):
        raise HTTPException(status_code=404, detail="Xero disabled")
    expected_state = request.cookies.get("xero_oauth_state")
    if not expected_state or not hmac.compare_digest(expected_state, state):
        raise HTTPException(status_code=401, detail="Invalid OAuth state")
    tok = xero_oauth.exchange_code(code)
    if "error" in tok:
        raise HTTPException(status_code=502, detail=tok)
    conns = xero_oauth.list_connections(tok["access_token"])
    if isinstance(conns, dict) and "error" in conns:
        raise HTTPException(status_code=502, detail=conns)
    if not conns:
        raise HTTPException(status_code=502, detail="No Xero tenants connected")
    stored = []
    for c in conns:
        tenant_id = c.get("tenantId")
        if not tenant_id:
            continue
        xero_oauth.store_tokens(
            tenant_id=tenant_id,
            access_token=tok["access_token"],
            refresh_token=tok["refresh_token"],
            expires_in=tok.get("expires_in", 1800),
            scope=tok.get("scope", ""),
        )
        stored.append({"tenant_id": tenant_id,
                       "tenant_name": c.get("tenantName")})
    return {"ok": True, "stored": stored}


# ── Webhook ───────────────────────────────────────────────────────────


def _verify_signature(secret: str, raw_body: bytes, sig_header: str) -> None:
    if not sig_header:
        raise HTTPException(status_code=401, detail="Missing x-xero-signature")
    expected = base64.b64encode(
        hmac.new(secret.encode(), raw_body, hashlib.sha256).digest()
    ).decode()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


@router.post("/webhook")
async def xero_webhook(
    request: Request,
    x_xero_signature: str | None = Header(default=None, alias="x-xero-signature"),
):
    if not (SME_OPS_ENABLED and XERO_ENABLED):
        raise HTTPException(status_code=404, detail="Xero webhook disabled")
    if not XERO_WEBHOOK_KEY:
        raise HTTPException(status_code=503,
                            detail="XERO_WEBHOOK_KEY not configured")
    raw = await request.body()
    _verify_signature(XERO_WEBHOOK_KEY, raw, x_xero_signature or "")

    # Intent-to-receive: empty body still validates if signature matches.
    try:
        envelope = json.loads(raw.decode("utf-8")) if raw else {}
    except Exception:
        raise HTTPException(status_code=400, detail="Body is not JSON")

    events = envelope.get("events") or []
    fired = []
    for ev in events:
        event_name = ev.get("eventCategory") or "unknown"
        event_id = record_event("xero", event_name, ev)
        fire_event_sync(
            "xero_event",
            {
                "xero_event_name": event_name,
                "xero_event_id": event_id,
                "tenant_id": ev.get("tenantId"),
                "resource_id": ev.get("resourceId"),
                "resource_url": ev.get("resourceUrl"),
                "event_type": ev.get("eventType"),
            },
        )
        fired.append(event_id)
    return {"ok": True, "events_processed": len(fired)}
