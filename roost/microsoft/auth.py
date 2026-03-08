"""Microsoft OAuth consent flow for FastAPI.

Routes:
  GET /auth/microsoft          → redirect to Microsoft login via MSAL
  GET /auth/microsoft/callback → exchange code, store MSAL token cache
"""

import logging

import msal
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from roost.config import MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID
from roost.microsoft import MS_SCOPES

logger = logging.getLogger("roost.microsoft.auth")

router = APIRouter(prefix="/auth")

AUTHORITY = f"https://login.microsoftonline.com/{MS_TENANT_ID}"


def _build_msal_app(cache=None):
    """Build an MSAL ConfidentialClientApplication."""
    return msal.ConfidentialClientApplication(
        client_id=MS_CLIENT_ID,
        client_credential=MS_CLIENT_SECRET,
        authority=AUTHORITY,
        token_cache=cache,
    )


@router.get("/microsoft")
async def microsoft_consent(request: Request):
    """Redirect to Microsoft OAuth consent for Graph scopes."""
    if not MS_CLIENT_ID:
        return HTMLResponse(
            "Microsoft OAuth not configured. Set MS_CLIENT_ID in .env.",
            status_code=500,
        )

    callback_url = str(request.url_for("microsoft_callback"))
    app = _build_msal_app()

    auth_url = app.get_authorization_request_url(
        scopes=MS_SCOPES,
        redirect_uri=callback_url,
    )
    return RedirectResponse(auth_url)


@router.get("/microsoft/callback")
async def microsoft_callback(request: Request):
    """Exchange authorization code for tokens and store MSAL cache."""
    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        desc = request.query_params.get("error_description", error)
        return HTMLResponse(f"Authorization denied: {desc}", status_code=403)
    if not code:
        return HTMLResponse("Missing authorization code.", status_code=400)

    callback_url = str(request.url_for("microsoft_callback"))

    cache = msal.SerializableTokenCache()
    app = _build_msal_app(cache=cache)

    result = app.acquire_token_by_authorization_code(
        code=code,
        scopes=MS_SCOPES,
        redirect_uri=callback_url,
    )

    if "error" in result:
        error_desc = result.get("error_description", result["error"])
        logger.error("Token exchange failed: %s", error_desc)
        return HTMLResponse(f"Token exchange failed: {error_desc}", status_code=500)

    # Create/find user from id_token claims before storing tokens
    claims = result.get("id_token_claims", {})
    email = (claims.get("preferred_username") or claims.get("email") or "").strip().lower()
    name = claims.get("name", "")

    from roost.sharing_service import upsert_user_from_oauth
    try:
        from roost.sharing_service import AccessDeniedError
    except ImportError:
        class AccessDeniedError(Exception): pass  # noqa: N818 — fallback for older deploys

    user_id = None
    if email:
        try:
            user = upsert_user_from_oauth(email, name)
            user_id = user.id
            request.session["user"] = {
                "email": email,
                "name": name,
                "user_id": user.id,
                "role": user.role,
            }
            logger.info("Web session created for %s via Microsoft OAuth", email)
        except AccessDeniedError as e:
            logger.warning("Microsoft OAuth denied for %s: %s", email, e)
            return RedirectResponse("/auth/denied")
        except Exception:
            logger.warning("Failed to create web session from MS claims", exc_info=True)

    # Store the entire MSAL cache (contains access + refresh + ID tokens)
    # Tagged with user_id for multi-tenant isolation
    from roost.microsoft.client import store_token_cache

    store_token_cache(cache.serialize(), user_id=user_id)

    logger.info("Microsoft OAuth tokens stored for user_id=%s", user_id)

    if user_id:
        next_url = request.session.pop("login_next", "/")
        return RedirectResponse(next_url or "/", status_code=302)

    # Fallback: no session created (missing email in claims)
    return HTMLResponse(
        "<h2>Microsoft Graph authorized!</h2>"
        "<p>Token cache stored. You can close this window.</p>"
        '<p><a href="/">Back to dashboard</a></p>'
    )
