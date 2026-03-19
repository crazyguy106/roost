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
}


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
