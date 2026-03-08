"""Google OAuth2 authentication routes."""

import logging
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from roost.config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, MS_CLIENT_ID,
)

logger = logging.getLogger("roost.auth")

router = APIRouter(prefix="/auth")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@router.get("/login")
async def login(request: Request):
    # Clear stale session to avoid CSRF state mismatch on re-login
    request.session.clear()
    redirect_uri = str(request.url_for("callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:
        # CSRF state mismatch or expired session — restart login flow
        logger.warning("OAuth callback failed: %s — restarting login", e)
        request.session.clear()
        return RedirectResponse("/auth/login")

    userinfo = token.get("userinfo")

    email = (userinfo.get("email") or "").strip().lower() if userinfo else ""
    if not email:
        return RedirectResponse("/auth/denied")

    name = userinfo.get("name", "")

    # Upsert user record (checks allowlist + auto-provision)
    from roost.sharing_service import upsert_user_from_oauth, AccessDeniedError
    try:
        user = upsert_user_from_oauth(email, name)
    except AccessDeniedError:
        logger.warning("Google OAuth denied for %s", email)
        return RedirectResponse("/auth/denied")

    next_url = request.session.pop("login_next", "/")
    request.session["user"] = {
        "email": email,
        "name": name,
        "user_id": user.id,
        "role": user.role,
    }
    return RedirectResponse(next_url or "/")


@router.get("/login-page")
async def login_page(request: Request):
    # Preserve ?next= param so we can redirect back after login.
    # Skip static asset paths (favicon.ico etc.) — browsers fetch these
    # in parallel and can overwrite a meaningful login_next value.
    next_url = request.query_params.get("next", "")
    if next_url and "." not in next_url.split("/")[-1]:
        request.session["login_next"] = next_url
    return templates.TemplateResponse("login.html", {
        "request": request,
        "show_google": bool(GOOGLE_CLIENT_ID),
        "show_microsoft": bool(MS_CLIENT_ID),
    })


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/auth/login-page")


@router.get("/me")
async def auth_me(request: Request):
    """Return current authenticated user info, or 401.

    Central endpoint for all apps sharing the Roost session cookie.
    nginx routes /auth/* to Roost, so any satellite app (DeptTools, etc.)
    can call /auth/me to check the logged-in user.
    """
    user = request.session.get("user")
    if user and user.get("email"):
        return {
            "authenticated": True,
            "user": user["email"],
            "name": user.get("name", ""),
            "role": user.get("role", ""),
        }
    return JSONResponse({"authenticated": False}, status_code=401)


@router.get("/denied")
async def denied(request: Request):
    return templates.TemplateResponse("denied.html", {"request": request})
