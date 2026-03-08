"""Build authenticated Microsoft Graph sessions from stored MSAL token cache."""

import json
import logging
from datetime import datetime
from pathlib import Path

import msal
import requests

from roost.config import MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID, MSAL_CACHE_PATH
from roost.microsoft import MS_SCOPES

logger = logging.getLogger("roost.microsoft.client")

PROVIDER = "microsoft"
SCOPE_KEY = "graph"
AUTHORITY = f"https://login.microsoftonline.com/{MS_TENANT_ID}"


def _shared_cache_path(user_id: int | None = None) -> Path | None:
    """Return the shared MSAL cache file path, or None if not configured.

    When user_id is provided, uses per-user cache file:
    /tmp/msal_cache_{user_id}.json
    """
    if MSAL_CACHE_PATH:
        base = Path(MSAL_CACHE_PATH)
        if user_id is not None:
            # Per-user cache file alongside the shared one
            return base.parent / f"msal_cache_{user_id}.json"
        return base
    return None


def _resolve_user_id(user_id: int | None) -> int | None:
    """Resolve user_id: use provided value, or get from user context."""
    if user_id is not None:
        return user_id
    try:
        from roost.user_context import get_current_user_id
        return get_current_user_id()
    except Exception:
        return None


def get_stored_token_cache(user_id: int | None = None) -> str | None:
    """Retrieve the stored MSAL token cache JSON.

    Args:
        user_id: Specific user to fetch tokens for. If None, uses current
                 user context (multi-tenant) or falls back to any available token.
    """
    uid = _resolve_user_id(user_id)

    # Per-user shared file takes precedence
    shared = _shared_cache_path(uid)
    if shared and shared.exists():
        cache_json = shared.read_text().strip()
        if cache_json:
            return cache_json

    # Legacy shared file (no user_id suffix) as fallback
    if uid is not None:
        legacy_shared = _shared_cache_path(None)
        if legacy_shared and legacy_shared.exists():
            cache_json = legacy_shared.read_text().strip()
            if cache_json:
                return cache_json

    from roost.database import get_connection

    conn = get_connection()
    if uid is not None:
        row = conn.execute(
            "SELECT refresh_token FROM oauth_tokens WHERE user_id = ? AND provider = ? AND scope = ?",
            (uid, PROVIDER, SCOPE_KEY),
        ).fetchone()
    else:
        # Fallback: any token for this provider (backward compat)
        row = conn.execute(
            "SELECT refresh_token FROM oauth_tokens WHERE provider = ? AND scope = ?",
            (PROVIDER, SCOPE_KEY),
        ).fetchone()
    conn.close()
    return row["refresh_token"] if row else None


def store_token_cache(cache_json: str, user_id: int | None = None) -> None:
    """Upsert the MSAL token cache JSON.

    Args:
        cache_json: Serialized MSAL token cache.
        user_id: User to store tokens for. If None, uses current user context.
    """
    uid = _resolve_user_id(user_id)

    # Write to per-user shared file if configured
    shared = _shared_cache_path(uid)
    if shared:
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text(cache_json)
        shared.chmod(0o600)
        logger.info("Stored MSAL token cache to shared file %s", shared)

    # Also write to DB
    from roost.database import get_connection

    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO oauth_tokens (user_id, provider, scope, refresh_token, access_token, expires_at, updated_at)
           VALUES (?, ?, ?, ?, '', '', ?)
           ON CONFLICT(user_id, provider, scope)
           DO UPDATE SET refresh_token = excluded.refresh_token,
                         updated_at = excluded.updated_at""",
        (uid, PROVIDER, SCOPE_KEY, cache_json, now),
    )
    conn.commit()
    conn.close()
    logger.info("Stored MSAL token cache for user_id=%s %s/%s", uid, PROVIDER, SCOPE_KEY)


def _get_msal_app(user_id: int | None = None) -> msal.ConfidentialClientApplication:
    """Build an MSAL ConfidentialClientApplication with persisted cache."""
    cache = msal.SerializableTokenCache()
    cache_json = get_stored_token_cache(user_id=user_id)
    if cache_json:
        cache.deserialize(cache_json)

    app = msal.ConfidentialClientApplication(
        client_id=MS_CLIENT_ID,
        client_credential=MS_CLIENT_SECRET,
        authority=AUTHORITY,
        token_cache=cache,
    )
    return app


def get_access_token(user_id: int | None = None) -> str:
    """Get a valid access token, refreshing silently if needed.

    Proactively refreshes if token expires within 5 minutes.

    Args:
        user_id: Specific user. If None, uses current user context.

    Returns:
        Access token string.

    Raises:
        RuntimeError: If no accounts in cache or token acquisition fails.
    """
    uid = _resolve_user_id(user_id)
    app = _get_msal_app(user_id=uid)
    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(
            "No Microsoft accounts in token cache. Visit /auth/microsoft to authorize."
        )

    result = app.acquire_token_silent(MS_SCOPES, account=accounts[0])
    if not result:
        raise RuntimeError(
            "Token refresh failed. Re-authorize at /auth/microsoft."
        )

    if "error" in result:
        raise RuntimeError(
            f"Token error: {result.get('error_description', result['error'])}"
        )

    # Proactive refresh: if token expires within 5 minutes, force a new one
    expires_in = result.get("expires_in", 3600)
    if expires_in < 300:
        logger.info("Token expires in %ds, forcing refresh", expires_in)
        result = app.acquire_token_silent(
            MS_SCOPES, account=accounts[0], force_refresh=True,
        )
        if not result or "error" in result:
            raise RuntimeError(
                "Proactive token refresh failed. Re-authorize at /auth/microsoft."
            )

    # Persist cache if it changed (e.g. new access token from refresh)
    cache = app.token_cache
    if cache.has_state_changed:
        store_token_cache(cache.serialize(), user_id=uid)

    return result["access_token"]


def build_graph_session(user_id: int | None = None) -> requests.Session:
    """Create a requests.Session with Authorization header for Graph API.

    Args:
        user_id: Specific user. If None, uses current user context.
    """
    token = get_access_token(user_id=user_id)
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    })
    return session
