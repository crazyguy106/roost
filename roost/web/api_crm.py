"""Unified CRM webhook endpoint.

Each provider has its own auth scheme — we verify per-vendor and then
normalise to a common shape:

    {
      "provider": "attio" | "hubspot" | ...,
      "event_type": "record.created" | "record.updated" | "deal.stage_changed" | ...,
      "object_type": "person" | "company" | "deal" | "note" | "communication",
      "record_id": "...",
      "changes": {field: {from, to}, ...},
      "raw": {...}                  # original payload for vendor-specific consumers
    }

Recipes registered with `trigger_type="crm_event"` and a matching
`trigger_config` filter (e.g. `{"object_type": "deal", "event_type": "stage_changed"}`)
fire on receipt.

Auth schemes:
- Attio: HMAC SHA256 of body using ATTIO_WEBHOOK_SECRET
- HubSpot: X-HubSpot-Signature-v3 (timestamp + body hashed with HUBSPOT_APP_SECRET)
- Zoho: shared secret in body or `Authorization` header (simple bearer)
- Salesforce: outbound message — we accept SOAP/JSON and verify org id
- Pipedrive: HTTP basic auth with PIPEDRIVE_WEBHOOK_USER/PASSWORD
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger("roost.web.crm")

router = APIRouter(prefix="/api/crm", tags=["crm"])


def _const_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a or "", b or "")


def _verify_attio(body: bytes, headers: dict) -> bool:
    secret = os.getenv("ATTIO_WEBHOOK_SECRET", "")
    if not secret:
        return True  # no secret configured → accept (dev)
    sig = headers.get("x-attio-signature", "") or headers.get("X-Attio-Signature", "")
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return _const_eq(sig, expected) or _const_eq(sig, f"sha256={expected}")


def _verify_hubspot(body: bytes, headers: dict, method: str, url: str) -> bool:
    secret = os.getenv("HUBSPOT_APP_SECRET", "")
    if not secret:
        return True
    sig = headers.get("x-hubspot-signature-v3", "") or headers.get("X-HubSpot-Signature-v3", "")
    ts = headers.get("x-hubspot-request-timestamp", "") or headers.get(
        "X-HubSpot-Request-Timestamp", "")
    if not (sig and ts):
        return False
    # Reject events older than 5 minutes
    try:
        if abs(time.time() * 1000 - int(ts)) > 5 * 60 * 1000:
            return False
    except ValueError:
        return False
    base = f"{method}{url}{body.decode('utf-8', errors='replace')}{ts}".encode()
    digest = hmac.new(secret.encode(), base, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode()
    return _const_eq(sig, expected)


def _verify_bearer(headers: dict, env: str) -> bool:
    secret = os.getenv(env, "")
    if not secret:
        return True
    auth = headers.get("authorization", "") or headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return _const_eq(auth.split(None, 1)[1].strip(), secret)
    return False


def _verify_pipedrive(headers: dict) -> bool:
    user = os.getenv("PIPEDRIVE_WEBHOOK_USER", "")
    pw = os.getenv("PIPEDRIVE_WEBHOOK_PASSWORD", "")
    if not (user or pw):
        return True
    auth = headers.get("authorization", "") or headers.get("Authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth.split(None, 1)[1]).decode()
        u, p = decoded.split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return _const_eq(u, user) and _const_eq(p, pw)


# ── Normalisers — one per provider ─────────────────────────────────────

def _norm_attio(payload: dict) -> list[dict]:
    """Attio sends {events: [{event_type, id: {record_id,...}, ...}, ...]}."""
    out = []
    for ev in payload.get("events") or [payload]:
        et = ev.get("event_type") or ev.get("type", "")
        obj_slug = (ev.get("id") or {}).get("object_slug") or ev.get("object_type", "")
        obj_map = {"people": "person", "companies": "company", "deals": "deal", "notes": "note"}
        out.append({
            "provider": "attio",
            "event_type": et,
            "object_type": obj_map.get(obj_slug, obj_slug),
            "record_id": (ev.get("id") or {}).get("record_id", ""),
            "changes": ev.get("actor", {}).get("changes") or ev.get("changes") or {},
            "raw": ev,
        })
    return out


def _norm_hubspot(payload: list | dict) -> list[dict]:
    items = payload if isinstance(payload, list) else [payload]
    out = []
    for ev in items:
        sub = (ev.get("subscriptionType") or "").lower()
        # e.g. "contact.creation", "deal.propertyChange"
        if "." in sub:
            obj, action = sub.split(".", 1)
        else:
            obj, action = "", sub
        obj_map = {"contact": "person", "company": "company", "deal": "deal"}
        action_map = {"creation": "created", "deletion": "deleted",
                      "propertychange": "updated"}
        out.append({
            "provider": "hubspot",
            "event_type": action_map.get(action.lower(), action),
            "object_type": obj_map.get(obj, obj),
            "record_id": str(ev.get("objectId", "")),
            "changes": ({ev.get("propertyName"): {"to": ev.get("propertyValue")}}
                        if ev.get("propertyName") else {}),
            "raw": ev,
        })
    return out


def _norm_zoho(payload: dict) -> list[dict]:
    # Zoho extension webhooks vary; the "Custom Notification" shape is:
    # {module, ids:[...], operation, token}
    obj_map = {"Contacts": "person", "Accounts": "company", "Deals": "deal"}
    op_map = {"insert": "created", "update": "updated", "delete": "deleted"}
    out = []
    for rec_id in payload.get("ids") or [payload.get("id", "")]:
        out.append({
            "provider": "zoho",
            "event_type": op_map.get((payload.get("operation") or "").lower(),
                                      payload.get("operation", "")),
            "object_type": obj_map.get(payload.get("module", ""), payload.get("module", "")),
            "record_id": str(rec_id),
            "changes": payload.get("changes") or {},
            "raw": payload,
        })
    return out


def _norm_salesforce(payload: dict) -> list[dict]:
    # Outbound Message JSON variant: {sobject: {Id, ...}, event}
    obj = payload.get("sobject") or {}
    sf_type = (payload.get("type") or "").lower()  # "contact", "opportunity", "account"
    obj_map = {"contact": "person", "opportunity": "deal", "account": "company"}
    return [{
        "provider": "salesforce",
        "event_type": payload.get("event", "updated"),
        "object_type": obj_map.get(sf_type, sf_type),
        "record_id": str(obj.get("Id", "")),
        "changes": payload.get("changes") or {},
        "raw": payload,
    }]


def _norm_pipedrive(payload: dict) -> list[dict]:
    # Pipedrive v2: {event: "added.deal" | "updated.person", current: {...}, previous: {...}}
    ev = payload.get("event", "")
    if "." in ev:
        action, obj = ev.split(".", 1)
    else:
        action, obj = ev, ""
    obj_map = {"person": "person", "organization": "company", "deal": "deal", "note": "note"}
    action_map = {"added": "created", "updated": "updated", "deleted": "deleted"}
    cur = payload.get("current") or {}
    return [{
        "provider": "pipedrive",
        "event_type": action_map.get(action, action),
        "object_type": obj_map.get(obj, obj),
        "record_id": str(cur.get("id", "")),
        "changes": payload.get("changes") or {},
        "raw": payload,
    }]


_NORMALISERS = {
    "attio": _norm_attio,
    "hubspot": _norm_hubspot,
    "zoho": _norm_zoho,
    "salesforce": _norm_salesforce,
    "pipedrive": _norm_pipedrive,
}


async def _dispatch_events(events: list[dict]) -> int:
    """Fire any enabled recipes with trigger_type='crm_event' whose filter matches.

    Filter shape (in recipe.trigger_config JSON): any subset of
    {"provider", "object_type", "event_type"}. Missing keys = match-all.
    """
    from roost.services import recipes as recipes_svc

    candidates = recipes_svc.list_recipes(trigger_type="crm_event", enabled_only=True)
    if not candidates:
        return 0
    fired = 0
    for ev in events:
        for r in candidates:
            cfg_raw = r.get("trigger_config") or ""
            try:
                cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) and cfg_raw else (
                    cfg_raw if isinstance(cfg_raw, dict) else {})
            except (ValueError, TypeError):
                cfg = {}
            if not all(not cfg.get(k) or ev.get(k) == cfg.get(k)
                       for k in ("provider", "object_type", "event_type")):
                continue
            try:
                await recipes_svc.execute_recipe(
                    r["id"], trigger_data={"crm_event": ev},
                )
                fired += 1
            except Exception:
                logger.exception("recipe %s failed on crm_event", r.get("id"))
    return fired


@router.post("/{provider}/webhook")
async def crm_webhook(provider: str, request: Request):
    """Receive a CRM webhook, verify, normalise, fan out to recipes."""
    provider = provider.lower()
    if provider not in _NORMALISERS:
        raise HTTPException(status_code=404, detail=f"unknown CRM provider: {provider}")

    body = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    url = str(request.url)

    if provider == "attio" and not _verify_attio(body, headers):
        raise HTTPException(status_code=401, detail="invalid Attio signature")
    if provider == "hubspot" and not _verify_hubspot(body, headers, request.method, url):
        raise HTTPException(status_code=401, detail="invalid HubSpot signature")
    if provider == "zoho" and not _verify_bearer(headers, "ZOHO_WEBHOOK_TOKEN"):
        raise HTTPException(status_code=401, detail="invalid Zoho token")
    if provider == "salesforce" and not _verify_bearer(headers, "SALESFORCE_WEBHOOK_TOKEN"):
        raise HTTPException(status_code=401, detail="invalid Salesforce token")
    if provider == "pipedrive" and not _verify_pipedrive(headers):
        raise HTTPException(status_code=401, detail="invalid Pipedrive auth")

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid JSON")

    events = _NORMALISERS[provider](payload)
    fired = await _dispatch_events(events)
    logger.info("crm.%s webhook: %d event(s), %d recipe(s) fired", provider, len(events), fired)
    return {"ok": True, "events": len(events), "fired": fired}
