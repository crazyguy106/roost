"""Gmail + Calendar OAuth consent flow for FastAPI.

Separate from the main /auth/login flow (which only does OpenID for login).
This flow requests Gmail + Calendar scopes and stores a refresh token.

Routes:
  GET /auth/gmail       → redirect to Google consent
  GET /auth/gmail/callback → handle code exchange, store tokens
"""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from roost.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from roost.gmail import GMAIL_SCOPES

logger = logging.getLogger("roost.gmail.auth")

router = APIRouter(prefix="/auth")

# Google OAuth endpoints
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


@router.get("/gmail")
async def gmail_consent(request: Request):
    """Redirect to Google OAuth consent for Gmail + Calendar scopes."""
    # Require authenticated session - prevent unauthorized OAuth
    session_user = request.session.get("user")
    if not session_user:
        return RedirectResponse("/auth/login-page", status_code=303)

    if not GOOGLE_CLIENT_ID:
        return HTMLResponse("Google OAuth not configured. Set GOOGLE_CLIENT_ID in .env.", status_code=500)

    callback_url = str(request.url_for("gmail_callback"))
    scope_str = " ".join(GMAIL_SCOPES)

    auth_url = (
        f"{GOOGLE_AUTH_URL}"
        f"?client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={callback_url}"
        f"&response_type=code"
        f"&scope={scope_str}"
        f"&access_type=offline"
        f"&prompt=consent"
        f"&include_granted_scopes=true"
    )
    return RedirectResponse(auth_url)


@router.get("/gmail/callback")
async def gmail_callback(request: Request):
    """Exchange authorization code for tokens and store them."""
    import httpx

    # Require authenticated session - prevent unauthorized token storage
    session_user = request.session.get("user")
    if not session_user:
        return HTMLResponse("Unauthorized. Please log in first.", status_code=401)

    code = request.query_params.get("code")
    error = request.query_params.get("error")

    if error:
        return HTMLResponse(f"Authorization denied: {error}", status_code=403)
    if not code:
        return HTMLResponse("Missing authorization code.", status_code=400)

    callback_url = str(request.url_for("gmail_callback"))

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": callback_url,
                "grant_type": "authorization_code",
            },
        )

    if resp.status_code != 200:
        logger.error("Token exchange failed: %s %s", resp.status_code, resp.text)
        return HTMLResponse(f"Token exchange failed: {resp.text}", status_code=500)

    data = resp.json()
    refresh_token = data.get("refresh_token", "")
    access_token = data.get("access_token", "")
    expires_in = data.get("expires_in", 0)

    if not refresh_token:
        return HTMLResponse(
            "No refresh token received. Try revoking app access at "
            "https://myaccount.google.com/permissions and try again.",
            status_code=400,
        )

    # Fetch the authenticated Google account email via userinfo
    account_email = ""
    try:
        async with httpx.AsyncClient() as client2:
            userinfo = await client2.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if userinfo.status_code == 200:
                account_email = userinfo.json().get("email", "")
    except Exception:
        logger.warning("Failed to fetch Google account email from userinfo")

    # Resolve user_id from web session (if logged in)
    user_id = None
    session_user = request.session.get("user")
    if session_user:
        user_id = session_user.get("user_id")

    # Store tokens tagged with user_id + account for multi-account support
    from roost.gmail.client import store_tokens
    from datetime import datetime, timedelta
    expires_at = ""
    if expires_in:
        expires_at = (datetime.now() + timedelta(seconds=expires_in)).isoformat(timespec="seconds")
    store_tokens(refresh_token=refresh_token, access_token=access_token,
                 expires_at=expires_at, user_id=user_id, account=account_email)

    logger.info("Gmail OAuth tokens stored for user_id=%s account=%s", user_id, account_email)
    acct_display = f" ({account_email})" if account_email else ""
    return HTMLResponse(
        f"<h2>Gmail + Calendar authorized!{acct_display}</h2>"
        "<p>Refresh token stored. You can close this window.</p>"
        "<p>Enable in .env: <code>GMAIL_ENABLED=true</code></p>"
        "<p>To add another Google account, visit <a href='/auth/gmail'>/auth/gmail</a> again and sign in with a different account.</p>"
        '<p><a href="/">Back to dashboard</a></p>'
    )
