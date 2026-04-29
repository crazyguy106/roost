"""Zoho CRM OAuth flow.

Three-step authorization-code grant. We capture the long-lived refresh
token and persist it via the encrypted credentials service. The Zoho
provider mints a fresh 1-hour access token on demand from the refresh
token (see roost/services/crm/zoho.py).

Routes:
- GET /auth/zoho/start    — redirect user to Zoho consent screen
- GET /auth/zoho/callback — exchange code → tokens, store, redirect to settings

Env config:
- ZOHO_CLIENT_ID / ZOHO_CLIENT_SECRET — your self-client app credentials
- ZOHO_REDIRECT_URI                   — must match the app config exactly
- ZOHO_ACCOUNTS_URL                   — region-specific (.com, .eu, .in, etc.)

Self-client setup: https://api-console.zoho.com — create a "Server-based"
client, register the redirect URI as <YOUR_ROOST_URL>/auth/zoho/callback.
"""

from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

logger = logging.getLogger("roost.web.auth_zoho")

router = APIRouter(prefix="/auth/zoho", tags=["auth"])

DEFAULT_SCOPES = "ZohoCRM.modules.ALL,ZohoCRM.users.READ,ZohoCRM.notification.ALL"


def _accounts_url() -> str:
    return os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")


def _redirect_uri(request: Request) -> str:
    explicit = os.getenv("ZOHO_REDIRECT_URI")
    if explicit:
        return explicit
    return str(request.url_for("zoho_callback"))


@router.get("/start")
def zoho_start(request: Request):
    client_id = os.getenv("ZOHO_CLIENT_ID", "")
    if not client_id:
        raise HTTPException(status_code=400,
                            detail="ZOHO_CLIENT_ID not configured")
    state = secrets.token_urlsafe(24)
    request.session["zoho_oauth_state"] = state
    params = {
        "scope": os.getenv("ZOHO_OAUTH_SCOPES", DEFAULT_SCOPES),
        "client_id": client_id,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "redirect_uri": _redirect_uri(request),
        "state": state,
    }
    return RedirectResponse(f"{_accounts_url()}/oauth/v2/auth?{urlencode(params)}")


@router.get("/callback", name="zoho_callback")
def zoho_callback(request: Request, code: str = "", state: str = "",
                  error: str = "", **_unused: str):
    if error:
        logger.warning("Zoho OAuth error: %s", error)
        return RedirectResponse(f"/settings?crm_error={error}#crm")
    if not code:
        raise HTTPException(status_code=400, detail="missing authorization code")

    expected = request.session.pop("zoho_oauth_state", None)
    if not expected or not secrets.compare_digest(expected, state or ""):
        raise HTTPException(status_code=400, detail="OAuth state mismatch")

    client_id = os.getenv("ZOHO_CLIENT_ID", "")
    client_secret = os.getenv("ZOHO_CLIENT_SECRET", "")
    if not (client_id and client_secret):
        raise HTTPException(status_code=400,
                            detail="Zoho OAuth client not configured")

    try:
        r = httpx.post(f"{_accounts_url()}/oauth/v2/token", params={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": _redirect_uri(request),
            "code": code,
        }, timeout=20.0)
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Zoho exchange failed: {e}") from e

    if r.status_code >= 400:
        logger.warning("Zoho token exchange %s: %s", r.status_code, r.text[:200])
        raise HTTPException(status_code=502,
                            detail=f"Zoho rejected exchange: {r.status_code}")

    data = r.json()
    refresh_token = data.get("refresh_token")
    if not refresh_token:
        # Zoho only returns refresh_token on first consent — repeat consent
        # is required to get one back. Bounce back with a clear message.
        return RedirectResponse(
            "/settings?crm_error=no_refresh_token_revoke_first#crm")

    from roost.services.credentials import store_credential
    user = getattr(request.state, "current_user", None) or {}
    user_id = user.get("user_id") or 1
    store_credential("ZOHO_REFRESH_TOKEN", refresh_token, user_id=user_id)
    if data.get("api_domain"):
        store_credential("ZOHO_API_DOMAIN", data["api_domain"], user_id=user_id)

    # Drop any cached provider so the next call re-reads credentials.
    try:
        from roost.services.crm import reset_provider_cache
        reset_provider_cache()
    except Exception:
        pass

    logger.info("Zoho refresh token stored for user %s", user_id)
    return RedirectResponse("/settings?crm_connected=zoho#crm")
