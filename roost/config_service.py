"""Settings service layer — bridges .env feature flags with DB overrides."""

import os
import logging

logger = logging.getLogger("roost.config_service")

# Feature flags with metadata (label, description, env default, restart required)
FEATURE_FLAGS = {
    "MS_ENABLED": {
        "label": "Microsoft 365",
        "description": "Email, Calendar, OneDrive, Teams via Graph API",
        "env_default": "false",
        "restart": True,
    },
    "GMAIL_ENABLED": {
        "label": "Gmail & Google",
        "description": "Gmail, Calendar, Drive, Slides, Sheets, Docs",
        "env_default": "false",
        "restart": True,
    },
    "GOOGLE_ENABLED": {
        "label": "Google Services",
        "description": "Master toggle for all Google services (Calendar, Gmail, Drive, Workspace)",
        "env_default": "true",
        "restart": True,
    },
    "CURRICULUM_ENABLED": {
        "label": "Curriculum Scanner",
        "description": "Programme seeding and curriculum document sync",
        "env_default": "true",
        "restart": True,
    },
    "NOTION_SYNC_ENABLED": {
        "label": "Notion Sync",
        "description": "Bidirectional Notion database synchronisation",
        "env_default": "false",
        "restart": True,
    },
    "GEMINI_AGENTIC": {
        "label": "Gemini AI",
        "description": "Agentic Gemini capabilities (research, document, agent)",
        "env_default": "true",
        "restart": True,
    },
}

# Integration checks — each returns (name, status_bool, detail_str)
_INTEGRATION_CHECKS = [
    {
        "name": "Microsoft Graph",
        "icon": "microsoft",
        "check_env": ["MS_CLIENT_ID", "MS_CLIENT_SECRET"],
        "check_token": ("microsoft", "graph"),
    },
    {
        "name": "Google OAuth",
        "icon": "google",
        "check_env": ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"],
        "check_token": None,
    },
    {
        "name": "Gmail",
        "icon": "gmail",
        "check_env": ["GOOGLE_CLIENT_ID"],
        "check_token": ("google", "gmail"),
    },
    {
        "name": "Gemini AI",
        "icon": "gemini",
        "check_env": ["GEMINI_API_KEY"],
        "check_token": None,
    },
    {
        "name": "Notion",
        "icon": "notion",
        "check_env": ["NOTION_API_TOKEN"],
        "check_token": None,
    },
    {
        "name": "Telegram Bot",
        "icon": "telegram",
        "check_env": ["TELEGRAM_BOT_TOKEN"],
        "check_token": None,
    },
]


def get_flag_value(flag: str) -> bool:
    """Get effective value: check DB override first, then env, then default."""
    from roost.services.settings import get_setting

    meta = FEATURE_FLAGS.get(flag)
    if not meta:
        return False

    # Check DB override
    override = get_setting(f"flag_override:{flag}")
    if override is not None:
        return override.lower() == "true"

    # Fall back to env then default
    env_val = os.getenv(flag)
    if env_val is not None:
        return env_val.lower() == "true"

    return meta["env_default"].lower() == "true"


def get_env_value(flag: str) -> bool:
    """Get value from env/.env only (no DB override). Used to detect divergence."""
    meta = FEATURE_FLAGS.get(flag)
    if not meta:
        return False
    env_val = os.getenv(flag)
    if env_val is not None:
        return env_val.lower() == "true"
    return meta["env_default"].lower() == "true"


def get_running_value(flag: str) -> bool:
    """Get the value currently loaded in config.py (running process)."""
    from roost import config
    return getattr(config, flag, False)


def get_flags_status() -> list[dict]:
    """Return all flags with current effective value and metadata."""
    results = []
    for flag, meta in FEATURE_FLAGS.items():
        effective = get_flag_value(flag)
        running = get_running_value(flag)
        env_val = get_env_value(flag)
        results.append({
            "flag": flag,
            "label": meta["label"],
            "description": meta["description"],
            "effective": effective,
            "running": running,
            "env_value": env_val,
            "has_override": effective != env_val,
            "restart_needed": effective != running,
            "restart_required": meta["restart"],
        })
    return results


def set_flag_override(flag: str, value: bool) -> dict:
    """Store a DB override. Returns updated flag status."""
    from roost.services.settings import set_setting

    if flag not in FEATURE_FLAGS:
        return {"error": f"Unknown flag: {flag}"}

    set_setting(f"flag_override:{flag}", str(value).lower())
    logger.info("Flag override set: %s = %s", flag, value)

    # Return updated status
    running = get_running_value(flag)
    return {
        "flag": flag,
        "effective": value,
        "running": running,
        "restart_needed": value != running,
    }


def clear_flag_override(flag: str) -> dict:
    """Remove DB override, reverting to env/.env value."""
    from roost.services.settings import delete_setting

    if flag not in FEATURE_FLAGS:
        return {"error": f"Unknown flag: {flag}"}

    delete_setting(f"flag_override:{flag}")
    logger.info("Flag override cleared: %s", flag)

    effective = get_env_value(flag)
    running = get_running_value(flag)
    return {
        "flag": flag,
        "effective": effective,
        "running": running,
        "restart_needed": effective != running,
    }


def get_integrations_status() -> list[dict]:
    """Return read-only status of integrations."""
    results = []
    for integration in _INTEGRATION_CHECKS:
        # Check if env vars are set
        env_configured = all(
            bool(os.getenv(var))
            for var in integration["check_env"]
        )

        # Check if OAuth token exists (if applicable)
        token_valid = False
        token_detail = ""
        if integration["check_token"] and env_configured:
            provider, scope = integration["check_token"]
            try:
                from roost.database import get_connection
                conn = get_connection()
                row = conn.execute(
                    "SELECT updated_at FROM oauth_tokens WHERE provider = ? AND scope = ?",
                    (provider, scope),
                ).fetchone()
                conn.close()
                if row:
                    token_valid = True
                    token_detail = f"Token last refreshed: {row['updated_at']}"
            except Exception:
                pass

        if not env_configured:
            status = "not_configured"
            detail = "API credentials not set"
        elif integration["check_token"] and not token_valid:
            status = "needs_auth"
            detail = "Credentials set but OAuth not completed"
        else:
            status = "connected"
            detail = token_detail or "Configured"

        results.append({
            "name": integration["name"],
            "icon": integration["icon"],
            "status": status,
            "detail": detail,
        })
    return results
