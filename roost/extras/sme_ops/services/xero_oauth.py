"""Xero OAuth2 helper (Phase 1B).

Implements the Authorization Code flow:
  1. `build_authorize_url(state)` — redirect target for the user's browser.
  2. `exchange_code(code)` — swap auth code for access + refresh tokens.
  3. `refresh(refresh_token)` — rotate access token (Xero rotates the
     refresh token too — always persist the new pair).
  4. `store_tokens` / `load_tokens` — persist per-tenant in `xero_oauth_tokens`.

Tokens live in the local SQLite. Xero rotates refresh tokens on each
exchange and they expire after 60 days of inactivity — refreshing within
that window keeps the connection alive indefinitely.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from roost.config import (
    XERO_CLIENT_ID,
    XERO_CLIENT_SECRET,
    XERO_REDIRECT_URI,
    ROOST_DOMAIN,
)
from roost.database import get_connection

logger = logging.getLogger("roost.extras.sme_ops.services.xero_oauth")

AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
CONNECTIONS_URL = "https://api.xero.com/connections"

DEFAULT_SCOPES = (
    "offline_access "
    "accounting.transactions "
    "accounting.contacts "
    "accounting.settings"
)


def redirect_uri() -> str:
    """Resolve the OAuth callback URL.

    Order of precedence:
      1. Explicit `XERO_REDIRECT_URI`.
      2. `https://${ROOST_DOMAIN}/api/xero/oauth/callback` (Caddy overlay).
    """
    if XERO_REDIRECT_URI:
        return XERO_REDIRECT_URI
    if ROOST_DOMAIN:
        return f"https://{ROOST_DOMAIN}/api/xero/oauth/callback"
    return ""


def build_authorize_url(state: str, scopes: str = DEFAULT_SCOPES) -> str:
    params = {
        "response_type": "code",
        "client_id": XERO_CLIENT_ID,
        "redirect_uri": redirect_uri(),
        "scope": scopes,
        "state": state,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def _basic_auth_header() -> str:
    raw = f"{XERO_CLIENT_ID}:{XERO_CLIENT_SECRET}".encode()
    return "Basic " + base64.b64encode(raw).decode()


def exchange_code(code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri(),
                },
                headers={"Authorization": _basic_auth_header()},
            )
        if resp.status_code >= 400:
            return {"error": f"xero_token_http_{resp.status_code}",
                    "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": "xero_token_request_failed", "detail": str(e)}


def refresh(refresh_token: str) -> dict:
    """Rotate the access token. Xero issues a new refresh token too."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                },
                headers={"Authorization": _basic_auth_header()},
            )
        if resp.status_code >= 400:
            return {"error": f"xero_refresh_http_{resp.status_code}",
                    "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": "xero_refresh_request_failed", "detail": str(e)}


def list_connections(access_token: str) -> list[dict] | dict:
    """Return tenants connected to this access token."""
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(
                CONNECTIONS_URL,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if resp.status_code >= 400:
            return {"error": f"xero_connections_http_{resp.status_code}",
                    "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": "xero_connections_failed", "detail": str(e)}


def store_tokens(
    tenant_id: str,
    access_token: str,
    refresh_token: str,
    expires_in: int,
    scope: str = "",
) -> None:
    expires_at = (datetime.now(timezone.utc)
                  + timedelta(seconds=int(expires_in))).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO xero_oauth_tokens
                (tenant_id, access_token, refresh_token, expires_at, scope, updated_at)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(tenant_id) DO UPDATE SET
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at    = excluded.expires_at,
                scope         = excluded.scope,
                updated_at    = datetime('now')
            """,
            (tenant_id, access_token, refresh_token, expires_at, scope),
        )
        conn.commit()
    finally:
        conn.close()


def load_tokens(tenant_id: str | None = None) -> dict | None:
    conn = get_connection()
    try:
        if tenant_id:
            row = conn.execute(
                "SELECT * FROM xero_oauth_tokens WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM xero_oauth_tokens ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def access_token_for(tenant_id: str | None = None) -> tuple[str, str] | None:
    """Return (access_token, tenant_id), refreshing if expired.

    Returns None if no OAuth tokens are stored or refresh fails.
    """
    row = load_tokens(tenant_id)
    if not row:
        return None
    try:
        expires_at = datetime.fromisoformat(row["expires_at"])
    except ValueError:
        return None
    now = datetime.now(timezone.utc)
    if expires_at <= now + timedelta(seconds=60):
        new = refresh(row["refresh_token"])
        if "error" in new:
            logger.warning("Xero refresh failed: %s", new)
            return None
        store_tokens(
            tenant_id=row["tenant_id"],
            access_token=new["access_token"],
            refresh_token=new.get("refresh_token", row["refresh_token"]),
            expires_in=new.get("expires_in", 1800),
            scope=new.get("scope", row["scope"]),
        )
        return new["access_token"], row["tenant_id"]
    return row["access_token"], row["tenant_id"]
