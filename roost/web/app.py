"""FastAPI application with unified authentication (OAuth + Basic Auth)."""

import base64
import logging
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from roost.config import (
    GOOGLE_CLIENT_ID, SESSION_SECRET,
    WEB_USERNAME, WEB_PASSWORD, DEV_TOKEN,
    MS_CLIENT_ID,
)
from roost.web.api import router as api_router
from roost.web.api_email import router as email_api_router
from roost.web.pages import router as pages_router
from roost.web.pages_mobile import router as mobile_router
try:
    from roost.web.api_otter import router as otter_router
except ImportError:
    otter_router = None
from roost.web.api_leads import router as leads_router

WEB_DIR = Path(__file__).parent
USE_OAUTH = bool(GOOGLE_CLIENT_ID)

_logger = logging.getLogger("roost.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle for the FastAPI app."""
    from roost.config import validate_config
    validate_config()

    try:
        from roost.config import NOTION_SYNC_ENABLED
        if NOTION_SYNC_ENABLED:
            from roost.notion.subscriber import init_subscriber
            from roost.notion.databases import ensure_databases
            ensure_databases()
            init_subscriber()
            _logger.info("Notion subscriber initialized (web)")
    except ImportError:
        pass
    except Exception:
        _logger.exception("Failed to initialize Notion subscriber (web)")

    try:
        from roost.config import CURRICULUM_ENABLED
        if CURRICULUM_ENABLED:
            from roost.curriculum_scanner import seed_legacy_curriculum
            seed_legacy_curriculum()
    except Exception:
        _logger.debug("Legacy curriculum seed failed", exc_info=True)

    try:
        from roost.config import GMAIL_ENABLED
        if GMAIL_ENABLED:
            from roost.gmail.subscriber import init_subscriber as gmail_init
            gmail_init()
            _logger.info("Gmail subscriber initialized (web)")
    except ImportError:
        pass
    except Exception:
        _logger.exception("Failed to initialize Gmail subscriber (web)")

    yield

import re

_MOBILE_UA_RE = re.compile(r"Mobile|Android|iPhone|iPad|iPod", re.IGNORECASE)

# Desktop paths that have mobile equivalents at /m/...
_MOBILE_REDIRECT_PATHS = {
    "/": "/m/",
    "/tasks": "/m/tasks",
    "/projects": "/m/projects",
    "/calendar": "/m/calendar",
    "/contacts": "/m/contacts",
    "/settings": "/m/settings",
}
# Pattern routes: /tasks/{id} → /m/tasks/{id}, /projects/{id}, /contacts/{id}
_MOBILE_REDIRECT_PATTERNS = [
    (re.compile(r"^/tasks/(\d+)$"), "/m/tasks/"),
    (re.compile(r"^/tasks/new$"), "/m/tasks/new"),
    (re.compile(r"^/projects/(\d+)$"), "/m/projects/"),
    (re.compile(r"^/contacts/(\d+)$"), "/m/contacts/"),
]


class MobileRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect mobile browsers from desktop pages to /m/ equivalents.

    Skips redirect when:
    - Already on /m/ routes
    - API/static/auth/shared paths
    - ?desktop=1 query param (explicit desktop override)
    - Non-GET requests (form submissions, HTMX, etc.)
    """

    async def dispatch(self, request: Request, call_next):
        if request.method != "GET":
            return await call_next(request)

        path = request.url.path
        if (path.startswith(("/m/", "/api/", "/static", "/auth/", "/shared/", "/forms"))
                or path in ("/sw.js", "/favicon.ico", "/api/docs", "/openapi.json")):
            return await call_next(request)

        if request.query_params.get("desktop") == "1":
            return await call_next(request)

        ua = request.headers.get("user-agent", "")
        if not _MOBILE_UA_RE.search(ua):
            return await call_next(request)

        # Exact path match
        mobile_path = _MOBILE_REDIRECT_PATHS.get(path)
        if mobile_path:
            return RedirectResponse(mobile_path, status_code=302)

        # Pattern match (e.g. /tasks/42 → /m/tasks/42)
        for pattern, prefix in _MOBILE_REDIRECT_PATTERNS:
            m = pattern.match(path)
            if m:
                suffix = m.group(1) if m.lastindex else ""
                return RedirectResponse(prefix + suffix, status_code=302)

        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add standard security headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://unpkg.com https://cdn.tailwindcss.com; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdn.jsdelivr.net; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        return response


class UnifiedAuthMiddleware(BaseHTTPMiddleware):
    """Authenticate via session cookie, HTTP Basic, or dev token. Gate admin paths by role."""

    OPEN_PATHS = {
        "/auth/login", "/auth/login-page", "/auth/callback", "/auth/denied",
        "/auth/logout",
        "/auth/gmail/callback",
        "/auth/microsoft", "/auth/microsoft/callback",
        "/api/otter/ingest",
        "/api/leads/capture",
    }

    ADMIN_PATH_PREFIXES = ("/sessions", "/integrations")

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Open paths — always pass through
        if path.startswith("/static") or path.startswith("/shared/") or path.startswith("/forms") or path in ("/sw.js", "/favicon.ico", "/clear-cache") or path in self.OPEN_PATHS:
            return await call_next(request)

        # 2. Dev token bypass (local tools like Playwright — localhost only)
        if DEV_TOKEN and secrets.compare_digest(
            request.cookies.get("_dev_token", ""), DEV_TOKEN
        ):
            client_ip = request.client.host if request.client else ""
            if client_ip in ("127.0.0.1", "::1"):
                return await call_next(request)

        # 3. Session cookie (set by Google or Microsoft OAuth)
        user = request.session.get("user")
        if user:
            # Role gate: admin paths require admin or owner
            if any(path.startswith(p) for p in self.ADMIN_PATH_PREFIXES):
                role = user.get("role", "")
                if role not in ("admin", "owner"):
                    from fastapi.templating import Jinja2Templates
                    _tpl = Jinja2Templates(directory=WEB_DIR / "templates")
                    return _tpl.TemplateResponse(
                        "forbidden.html",
                        {"request": request, "current_user": user},
                        status_code=403,
                    )
            return await call_next(request)

        # 4. HTTP Basic Auth (API clients, curl, backwards compat)
        auth = request.headers.get("Authorization")
        if auth and WEB_USERNAME and WEB_PASSWORD:
            try:
                scheme, credentials = auth.split(" ", 1)
                if scheme.lower() == "basic":
                    decoded = base64.b64decode(credentials).decode("utf-8")
                    username, password = decoded.split(":", 1)
                    if (secrets.compare_digest(username, WEB_USERNAME)
                            and secrets.compare_digest(password, WEB_PASSWORD)):
                        return await call_next(request)
            except Exception:
                _logger.debug("Basic auth header decode failed", exc_info=True)

        # 5. Not authenticated — redirect to login page
        return RedirectResponse("/auth/login-page")


class UserContextMiddleware(BaseHTTPMiddleware):
    """Copy session user to request.state, refreshing role from DB."""

    async def dispatch(self, request: Request, call_next):
        user = getattr(request, "session", {}).get("user")
        if user and user.get("user_id"):
            # Refresh role from DB to pick up changes without re-login
            try:
                from roost.sharing_service import get_user
                db_user = get_user(user["user_id"])
                if db_user and db_user.role != user.get("role"):
                    user["role"] = db_user.role
                    request.session["user"] = user
            except Exception:
                _logger.warning("Failed to refresh user role", exc_info=True)
        request.state.current_user = user
        return await call_next(request)


def _rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded):
    """Return 429 with Retry-After header when rate limit is hit."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded"},
        headers={"Retry-After": str(exc.detail)},
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Roost", docs_url="/api/docs", lifespan=lifespan)

    # Rate limiting
    from roost.web.rate_limit import limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    # Middleware stack (added in reverse order — last added = outermost):
    # 1. SessionMiddleware (outermost) — populates request.session
    # 2. UnifiedAuthMiddleware — checks session / basic auth / dev token + role gates
    # 3. UserContextMiddleware (innermost) — copies user to request.state
    app.add_middleware(UserContextMiddleware)
    app.add_middleware(UnifiedAuthMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=SESSION_SECRET,
        max_age=86400,       # 24 hours
        same_site="lax",
        https_only=USE_OAUTH,  # True when Google OAuth is configured (HTTPS)
    )

    if USE_OAUTH:
        # Google OAuth routes (login/callback)
        from roost.web.auth import router as auth_router
        app.include_router(auth_router)

        # Gmail OAuth consent routes (separate scope flow)
        from roost.gmail.auth import router as gmail_auth_router
        app.include_router(gmail_auth_router)

    else:
        # Fallback login page + logout when Google OAuth is not configured
        from fastapi.templating import Jinja2Templates
        _templates = Jinja2Templates(directory=WEB_DIR / "templates")

        @app.get("/auth/login-page")
        async def login_page(request: Request):
            return _templates.TemplateResponse("login.html", {
                "request": request,
                "show_google": bool(GOOGLE_CLIENT_ID),
                "show_microsoft": bool(MS_CLIENT_ID),
            })

        @app.get("/auth/logout")
        async def logout(request: Request):
            request.session.clear()
            return RedirectResponse("/auth/login-page")

    # Microsoft OAuth consent routes (registered in both OAuth and basic auth modes)
    from roost.microsoft.auth import router as ms_auth_router
    app.include_router(ms_auth_router)

    # CORS — single-origin app, block all cross-origin requests
    from starlette.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=[])

    # Security headers on every response
    app.add_middleware(SecurityHeadersMiddleware)

    # Mobile redirect — outermost, runs before auth (redirect first, auth on /m/ routes)
    app.add_middleware(MobileRedirectMiddleware)

    # Trust proxy headers from nginx (X-Forwarded-For, X-Forwarded-Proto)
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1", "::1"])

    # Static files
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    # PWA: serve service worker from root so it can control all pages
    from fastapi.responses import FileResponse

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        return FileResponse(
            WEB_DIR / "static" / "sw.js",
            media_type="application/javascript",
            headers={
                "Service-Worker-Allowed": "/",
                "Cache-Control": "no-cache, no-store, must-revalidate",
            },
        )

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return FileResponse(
            WEB_DIR / "static" / "favicon.ico",
            media_type="image/x-icon",
        )

    @app.get("/clear-cache", include_in_schema=False)
    async def clear_cache():
        """One-shot page to nuke the service worker and all caches."""
        return HTMLResponse("""<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clearing cache...</title></head><body style="font-family:system-ui;padding:2em;font-size:18px;background:#111;color:#eee">
<h2>Clearing Roost cache...</h2><pre id="log"></pre>
<script>
const log = document.getElementById('log');
function msg(t){log.textContent += t + '\\n';}
(async()=>{
  try{
    const regs = await navigator.serviceWorker.getRegistrations();
    for(const r of regs){await r.unregister(); msg('Unregistered SW: '+r.scope);}
    if(!regs.length) msg('No service workers found.');
    const keys = await caches.keys();
    for(const k of keys){await caches.delete(k); msg('Deleted cache: '+k);}
    if(!keys.length) msg('No caches found.');
    msg('\\nDone! Redirecting in 2s...');
    setTimeout(()=>location.href='/m/',2000);
  }catch(e){msg('Error: '+e);}
})();
</script></body></html>""")

    # Routers
    from roost.web.forms import router as forms_router
    app.include_router(forms_router)
    app.include_router(api_router)
    app.include_router(email_api_router)
    app.include_router(pages_router)
    app.include_router(mobile_router)
    if otter_router is not None:
        app.include_router(otter_router)
    app.include_router(leads_router)

    return app


app = create_app()
