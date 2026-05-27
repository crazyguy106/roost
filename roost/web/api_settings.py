"""Settings API — credential CRUD, connection testing, personality editor."""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("roost.web.api_settings")

router = APIRouter(prefix="/api/settings", tags=["settings"])

# Integration definitions: what credentials each integration needs
INTEGRATIONS = {
    "gemini": {
        "name": "Gemini AI",
        "credentials": ["GEMINI_API_KEY"],
        "labels": {"GEMINI_API_KEY": "API Key"},
    },
    "claude": {
        "name": "Claude AI",
        "credentials": ["CLAUDE_API_KEY"],
        "labels": {"CLAUDE_API_KEY": "API Key"},
    },
    "openai": {
        "name": "OpenAI",
        "credentials": ["OPENAI_API_KEY"],
        "labels": {"OPENAI_API_KEY": "API Key"},
    },
    "telegram": {
        "name": "Telegram Bot",
        "credentials": ["TELEGRAM_BOT_TOKEN"],
        "labels": {"TELEGRAM_BOT_TOKEN": "Bot Token"},
    },
    "google": {
        "name": "Google Workspace",
        "credentials": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "labels": {
            "GOOGLE_CLIENT_ID": "Client ID",
            "GOOGLE_CLIENT_SECRET": "Client Secret",
        },
    },
    "microsoft": {
        "name": "Microsoft 365",
        "credentials": ["MS_CLIENT_ID", "MS_CLIENT_SECRET", "MS_TENANT_ID"],
        "labels": {
            "MS_CLIENT_ID": "Application (Client) ID",
            "MS_CLIENT_SECRET": "Client Secret",
            "MS_TENANT_ID": "Tenant ID",
        },
    },
    "notion": {
        "name": "Notion",
        "credentials": ["NOTION_API_TOKEN"],
        "labels": {"NOTION_API_TOKEN": "Internal Integration Token"},
    },
    "discord": {
        "name": "Discord Bot",
        "credentials": ["DISCORD_BOT_TOKEN"],
        "labels": {"DISCORD_BOT_TOKEN": "Bot Token"},
    },
    "slack": {
        "name": "Slack Bot",
        "credentials": ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"],
        "labels": {
            "SLACK_BOT_TOKEN": "Bot Token (xoxb-...)",
            "SLACK_APP_TOKEN": "App Token (xapp-...)",
        },
    },
    "signal": {
        "name": "Signal Bot",
        "credentials": ["SIGNAL_API_URL", "SIGNAL_PHONE_NUMBER"],
        "labels": {
            "SIGNAL_API_URL": "signal-cli API URL",
            "SIGNAL_PHONE_NUMBER": "Bot Phone Number",
        },
    },
    "matrix": {
        "name": "Matrix Bot",
        "credentials": ["MATRIX_HOMESERVER", "MATRIX_USER_ID", "MATRIX_ACCESS_TOKEN"],
        "labels": {
            "MATRIX_HOMESERVER": "Homeserver URL",
            "MATRIX_USER_ID": "Bot User ID (@bot:server)",
            "MATRIX_ACCESS_TOKEN": "Access Token",
        },
    },
    "attio": {
        "name": "Attio CRM",
        "credentials": ["ATTIO_API_KEY", "ATTIO_WEBHOOK_SECRET"],
        "labels": {
            "ATTIO_API_KEY": "API Key",
            "ATTIO_WEBHOOK_SECRET": "Webhook Secret (optional)",
        },
    },
    "hubspot": {
        "name": "HubSpot CRM",
        "credentials": ["HUBSPOT_ACCESS_TOKEN", "HUBSPOT_APP_SECRET"],
        "labels": {
            "HUBSPOT_ACCESS_TOKEN": "Private App Access Token",
            "HUBSPOT_APP_SECRET": "App Secret (for webhook verification)",
        },
    },
    "zoho": {
        "name": "Zoho CRM",
        "credentials": ["ZOHO_CLIENT_ID", "ZOHO_CLIENT_SECRET", "ZOHO_REFRESH_TOKEN"],
        "labels": {
            "ZOHO_CLIENT_ID": "Client ID",
            "ZOHO_CLIENT_SECRET": "Client Secret",
            "ZOHO_REFRESH_TOKEN": "Refresh Token (set via OAuth)",
        },
        "oauth_start": "/auth/zoho/start",
    },
    "salesforce": {
        "name": "Salesforce",
        "credentials": ["SALESFORCE_INSTANCE_URL", "SALESFORCE_ACCESS_TOKEN"],
        "labels": {
            "SALESFORCE_INSTANCE_URL": "Instance URL (https://yourorg.my.salesforce.com)",
            "SALESFORCE_ACCESS_TOKEN": "Access Token",
        },
    },
    "pipedrive": {
        "name": "Pipedrive",
        "credentials": ["PIPEDRIVE_API_TOKEN"],
        "labels": {"PIPEDRIVE_API_TOKEN": "API Token"},
    },
}

CRM_PROVIDERS = ["local", "attio", "hubspot", "zoho", "salesforce", "pipedrive"]


def _require_admin(request: Request) -> dict | None:
    """Check admin/owner role. Returns error dict if not authorised."""
    user = getattr(request.state, "current_user", None)
    if not user or user.get("role") not in ("admin", "owner"):
        return {"error": "Admin access required"}
    return None


def _get_user_id(request: Request) -> int:
    """Get user ID from session."""
    user = getattr(request.state, "current_user", None)
    if user:
        return user.get("user_id", 1)
    return 1


@router.get("/integrations")
def get_integrations(request: Request):
    """Return all integrations with their credential status."""
    from roost.services.credentials import get_credential_masked

    user_id = _get_user_id(request)
    result = []

    for key, info in INTEGRATIONS.items():
        creds = {}
        all_set = True
        for cred_key in info["credentials"]:
            masked = get_credential_masked(cred_key, user_id)
            creds[cred_key] = {
                "label": info["labels"][cred_key],
                "masked": masked,
                "is_set": masked is not None,
            }
            if masked is None:
                all_set = False

        result.append({
            "key": key,
            "name": info["name"],
            "credentials": creds,
            "status": "configured" if all_set else "not_configured",
        })

    return JSONResponse(result)


@router.post("/credential/{cred_key}")
async def save_credential(request: Request, cred_key: str):
    """Store an encrypted credential."""
    auth_error = _require_admin(request)
    if auth_error:
        return JSONResponse(auth_error, status_code=403)

    body = await request.json()
    value = body.get("value", "").strip()
    if not value:
        return JSONResponse({"error": "Value is required"}, status_code=400)

    from roost.services.credentials import store_credential, get_credential_masked
    user_id = _get_user_id(request)

    store_credential(cred_key, value, user_id)
    masked = get_credential_masked(cred_key, user_id)

    return JSONResponse({
        "ok": True,
        "key": cred_key,
        "masked": masked,
        "restart_needed": True,
    })


@router.delete("/credential/{cred_key}")
def remove_credential(request: Request, cred_key: str):
    """Remove a stored credential."""
    auth_error = _require_admin(request)
    if auth_error:
        return JSONResponse(auth_error, status_code=403)

    from roost.services.credentials import delete_credential
    user_id = _get_user_id(request)

    deleted = delete_credential(cred_key, user_id)
    return JSONResponse({"ok": deleted, "key": cred_key})


@router.post("/test/{integration}")
def test_connection(request: Request, integration: str):
    """Test an integration's credentials."""
    from roost.services.credentials import test_credential
    user_id = _get_user_id(request)

    result = test_credential(integration, user_id)
    status = 200 if result["ok"] else 422
    return JSONResponse(result, status_code=status)


@router.post("/personality")
async def save_personality(request: Request):
    """Save agent personality text to CAGE preferences."""
    body = await request.json()
    text = body.get("text", "").strip()

    if len(text) > 1000:
        return JSONResponse(
            {"error": "Personality text must be under 1000 characters"},
            status_code=400,
        )

    user = getattr(request.state, "current_user", None)
    user_id = str(user.get("user_id", 1)) if user else "1"

    from roost.context import set_preference, delete_preference
    if text:
        set_preference(user_id, "personality", text)
    else:
        delete_preference(user_id, "personality")

    return JSONResponse({"ok": True, "length": len(text)})


@router.get("/personality")
def get_personality(request: Request):
    """Get current personality text."""
    user = getattr(request.state, "current_user", None)
    user_id = str(user.get("user_id", 1)) if user else "1"

    from roost.context import get_preferences
    prefs = get_preferences(user_id)
    text = prefs.get("personality", "")

    return JSONResponse({"text": text})


@router.post("/flag/{flag_name}")
def toggle_flag(request: Request, flag_name: str):
    """Toggle a feature flag on/off."""
    auth_error = _require_admin(request)
    if auth_error:
        return JSONResponse(auth_error, status_code=403)

    from roost.config_service import get_flag_value, set_flag_override
    current = get_flag_value(flag_name)
    result = set_flag_override(flag_name, not current)
    if "error" in result:
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


# ── Charter endpoints ────────────────────────────────────────────

@router.get("/charter")
def get_charter_endpoint(request: Request):
    """Get the core charter and all provider files."""
    from roost.charter import get_charter_raw, get_provider_charter_raw, list_charter_files

    return JSONResponse({
        "charter": get_charter_raw(),
        "providers": {
            p: get_provider_charter_raw(p)
            for p in ("gemini", "claude", "openai")
        },
        "files": list_charter_files(),
    })


@router.post("/charter")
async def save_charter_endpoint(request: Request):
    """Save the core charter.md file."""
    auth_error = _require_admin(request)
    if auth_error:
        return JSONResponse(auth_error, status_code=403)

    body = await request.json()
    text = body.get("text", "")

    from roost.charter import save_charter
    save_charter(text)

    return JSONResponse({"ok": True, "length": len(text)})


@router.post("/charter/{provider}")
async def save_provider_charter_endpoint(request: Request, provider: str):
    """Save a provider-specific charter file."""
    auth_error = _require_admin(request)
    if auth_error:
        return JSONResponse(auth_error, status_code=403)

    body = await request.json()
    text = body.get("text", "")

    from roost.charter import save_provider_charter
    try:
        save_provider_charter(provider, text)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    return JSONResponse({"ok": True, "provider": provider, "length": len(text)})


# ── Telegram Linking ────────────────────────────────────────────

@router.post("/telegram/link")
def generate_telegram_link(request: Request):
    """Generate a link code for Telegram account linking."""
    user = getattr(request.state, "current_user", None)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    user_id = user.get("user_id", 1)

    from roost.services.telegram_linking import generate_link_code
    code = generate_link_code(user_id)

    return JSONResponse({
        "ok": True,
        "code": code,
        "expires_in": 600,
        "instruction": f"Send this message to your Telegram bot:\n\nLINK {code}",
    })


@router.get("/telegram/status")
def telegram_link_status(request: Request):
    """Check if current user has a linked Telegram account."""
    user = getattr(request.state, "current_user", None)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    user_id = user.get("user_id", 1)

    from roost.services.telegram_linking import get_link_status
    status = get_link_status(user_id)

    return JSONResponse(status)


@router.post("/telegram/unlink")
def unlink_telegram(request: Request):
    """Remove Telegram link from current user."""
    user = getattr(request.state, "current_user", None)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    user_id = user.get("user_id", 1)

    from roost.sharing_service import update_user
    update_user(user_id, telegram_id=0)  # 0 = unlinked (NULL would need different handling)

    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET telegram_id = NULL WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    return JSONResponse({"ok": True})


# ── CRM provider selection & status ────────────────────────────────────


def _active_crm_provider() -> str:
    """Resolve the active CRM provider — DB override beats env."""
    from roost.services.settings import get_setting
    import os
    override = get_setting("crm_provider")
    if override:
        return override.strip().lower()
    return os.getenv("CRM_PROVIDER", "local").strip().lower()


@router.get("/crm")
def get_crm_status(request: Request):
    """Return active CRM provider, available choices, and per-provider config status."""
    from roost.services.credentials import get_credential_masked
    user_id = _get_user_id(request)
    active = _active_crm_provider()
    out: dict = {"active": active, "providers": []}

    for p in CRM_PROVIDERS:
        if p == "local":
            out["providers"].append({
                "key": "local",
                "name": "Roost Local Contacts",
                "configured": True,
                "credentials": {},
                "oauth_start": None,
            })
            continue
        info = INTEGRATIONS.get(p, {})
        creds = {}
        all_set = True
        for cred_key in info.get("credentials", []):
            masked = get_credential_masked(cred_key, user_id)
            creds[cred_key] = {
                "label": info.get("labels", {}).get(cred_key, cred_key),
                "masked": masked,
                "is_set": masked is not None,
            }
            if masked is None:
                all_set = False
        out["providers"].append({
            "key": p,
            "name": info.get("name", p.title()),
            "configured": all_set,
            "credentials": creds,
            "oauth_start": info.get("oauth_start"),
        })
    return JSONResponse(out)


@router.post("/crm/provider")
async def set_crm_provider(request: Request):
    """Set the active CRM provider. Persists as a DB override."""
    auth_error = _require_admin(request)
    if auth_error:
        return JSONResponse(auth_error, status_code=403)
    body = await request.json()
    provider = (body.get("provider") or "").strip().lower()
    if provider not in CRM_PROVIDERS:
        return JSONResponse(
            {"error": f"unknown provider: {provider}",
             "valid": CRM_PROVIDERS}, status_code=400)
    from roost.services.settings import set_setting
    set_setting("crm_provider", provider)
    try:
        from roost.extras.crm.services import reset_provider_cache
        reset_provider_cache()
    except Exception:
        pass
    return JSONResponse({"ok": True, "active": provider, "restart_needed": False})


@router.post("/crm/test")
def test_crm(request: Request):
    """Ping the active CRM provider."""
    try:
        from roost.extras.crm.services import get_provider, reset_provider_cache
        reset_provider_cache()
        crm = get_provider(_active_crm_provider())
        result = crm.test_connection()
        result.setdefault("provider", crm.name)
        status = 200 if result.get("ok") else 422
        return JSONResponse(result, status_code=status)
    except Exception as e:
        logger.exception("crm test failed")
        return JSONResponse({"ok": False, "detail": str(e)}, status_code=500)


# ── Deployment status ───────────────────────────────────────────────


@router.get("/deployment")
def deployment_status(request: Request):
    """Read-only summary of where Roost thinks it's deployed.

    Detects shape from SIDECAR_PUBLIC_URL + ROOST_DOMAIN env, pings the
    chromium sidecar's CDP discovery to confirm reachability.
    """
    import os
    from urllib.parse import urlsplit
    from roost.config import SIDECAR_PUBLIC_URL, SIDECAR_INTERNAL_HTTP_URL

    public = SIDECAR_PUBLIC_URL or ""
    domain = os.getenv("ROOST_DOMAIN", "")
    parts = urlsplit(public)
    scheme = parts.scheme or ""
    host = parts.netloc or ""

    if domain and scheme == "https":
        shape = "vps_domain"
        shape_label = "VPS + domain (Caddy auto-TLS)"
    elif scheme == "https":
        shape = "hosted"
        shape_label = "Hosted (TLS, no ROOST_DOMAIN set)"
    elif host.startswith("localhost") or host.startswith("127.") or host == "":
        shape = "laptop"
        shape_label = "Laptop (localhost)"
    else:
        shape = "custom"
        shape_label = f"Custom ({host})"

    sidecar_ok = False
    sidecar_detail = ""
    try:
        import httpx
        with httpx.Client(timeout=3) as client:
            r = client.get(f"{SIDECAR_INTERNAL_HTTP_URL.rstrip('/')}/json/version")
            sidecar_ok = r.status_code == 200
            if sidecar_ok:
                v = r.json()
                sidecar_detail = v.get("Browser", "") or v.get("browserVersion", "")
            else:
                sidecar_detail = f"HTTP {r.status_code}"
    except Exception as e:
        sidecar_detail = str(e)

    return JSONResponse({
        "shape": shape,
        "shape_label": shape_label,
        "sidecar_public_url": public,
        "sidecar_internal_url": SIDECAR_INTERNAL_HTTP_URL,
        "roost_domain": domain,
        "sidecar_reachable": sidecar_ok,
        "sidecar_detail": sidecar_detail,
    })
