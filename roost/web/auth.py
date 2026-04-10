"""Google OAuth2 authentication routes + one-time password setup."""

import logging
import secrets
from pathlib import Path
from fastapi import APIRouter, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from roost.config import (
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, MS_CLIENT_ID, PROJECT_ROOT,
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


# ── One-time password setup ─────────────────────────────────────

SETUP_TOKEN_FILE = PROJECT_ROOT / "data" / ".setup_token"


@router.get("/setup")
async def setup_password_form(request: Request):
    """Show the password setup form if the token is valid."""
    token = request.query_params.get("token", "")
    if not SETUP_TOKEN_FILE.exists():
        return RedirectResponse("/auth/login-page")
    stored_token = SETUP_TOKEN_FILE.read_text().strip()
    if not stored_token or not secrets.compare_digest(token, stored_token):
        return RedirectResponse("/auth/login-page")
    return templates.TemplateResponse("setup_password.html", {
        "request": request,
        "token": token,
        "error": None,
    })


@router.post("/setup")
async def setup_password_submit(
    request: Request,
    token: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(...),
):
    """Validate and set the web password, then delete the setup token."""
    if not SETUP_TOKEN_FILE.exists():
        return RedirectResponse("/auth/login-page")
    stored_token = SETUP_TOKEN_FILE.read_text().strip()
    if not stored_token or not secrets.compare_digest(token, stored_token):
        return RedirectResponse("/auth/login-page")

    if password != password_confirm:
        return templates.TemplateResponse("setup_password.html", {
            "request": request, "token": token,
            "error": "Passwords do not match.",
        })
    if len(password) < 8:
        return templates.TemplateResponse("setup_password.html", {
            "request": request, "token": token,
            "error": "Password must be at least 8 characters.",
        })

    # Update WEB_PASSWORD in .env
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        new_lines = []
        found = False
        for line in lines:
            if line.startswith("WEB_PASSWORD="):
                new_lines.append(f"WEB_PASSWORD={password}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"WEB_PASSWORD={password}")
        env_file.write_text("\n".join(new_lines) + "\n")
    else:
        env_file.write_text(f"WEB_PASSWORD={password}\n")

    SETUP_TOKEN_FILE.unlink()

    import roost.config as cfg
    cfg.WEB_PASSWORD = password

    logger.info("Password set via setup token — token consumed")
    return templates.TemplateResponse("setup_password.html", {
        "request": request, "token": "",
        "error": None,
        "success": True,
    })
