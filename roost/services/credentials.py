"""Encrypted credential storage for API keys and secrets.

Credentials are stored in the user_settings table with a 'credential:'
prefix, encrypted using Fernet (AES-128-CBC) derived from SESSION_SECRET.

If SESSION_SECRET changes, all stored credentials become unreadable —
this is by design (secrets are tied to the instance).
"""

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from roost.services.settings import get_setting, set_setting, delete_setting

logger = logging.getLogger("roost.services.credentials")

__all__ = [
    "store_credential",
    "get_credential",
    "get_credential_masked",
    "delete_credential",
    "test_credential",
]

# Credential key prefix in user_settings table
_PREFIX = "credential:"


def _get_fernet() -> Fernet:
    """Derive a Fernet key from SESSION_SECRET."""
    from roost.config import SESSION_SECRET
    key = base64.urlsafe_b64encode(
        hashlib.sha256(SESSION_SECRET.encode()).digest()
    )
    return Fernet(key)


def store_credential(key: str, value: str, user_id: int = 1) -> None:
    """Encrypt and store a credential."""
    encrypted = _get_fernet().encrypt(value.encode()).decode()
    set_setting(f"{_PREFIX}{key}", encrypted, user_id=user_id)
    logger.info("Stored credential: %s", key)


def get_credential(key: str, user_id: int = 1) -> str | None:
    """Retrieve and decrypt a credential. Returns None if not found."""
    encrypted = get_setting(f"{_PREFIX}{key}", user_id=user_id)
    if not encrypted:
        return None
    try:
        return _get_fernet().decrypt(encrypted.encode()).decode()
    except InvalidToken:
        logger.warning("Failed to decrypt credential: %s (SESSION_SECRET changed?)", key)
        return None


def get_credential_masked(key: str, user_id: int = 1) -> str | None:
    """Return a masked version of the credential for display."""
    value = get_credential(key, user_id)
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return value[:4] + "••••" + value[-4:]


def delete_credential(key: str, user_id: int = 1) -> bool:
    """Remove a stored credential."""
    existing = get_setting(f"{_PREFIX}{key}", user_id=user_id)
    if existing:
        delete_setting(f"{_PREFIX}{key}", user_id=user_id)
        logger.info("Deleted credential: %s", key)
        return True
    return False


def test_credential(integration: str, user_id: int = 1) -> dict:
    """Test an integration's credentials. Returns {ok, detail}."""
    import urllib.request
    import json

    try:
        if integration == "telegram":
            token = get_credential("TELEGRAM_BOT_TOKEN", user_id)
            if not token:
                return {"ok": False, "detail": "No bot token stored"}
            url = f"https://api.telegram.org/bot{token}/getMe"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                if data.get("ok"):
                    name = data["result"].get("username", "unknown")
                    return {"ok": True, "detail": f"Connected to @{name}"}
            return {"ok": False, "detail": "API returned error"}

        elif integration == "gemini":
            key = get_credential("GEMINI_API_KEY", user_id)
            if not key:
                return {"ok": False, "detail": "No API key stored"}
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                count = len(data.get("models", []))
                return {"ok": True, "detail": f"{count} models available"}
            return {"ok": False, "detail": "API returned error"}

        elif integration == "claude":
            key = get_credential("CLAUDE_API_KEY", user_id)
            if not key:
                return {"ok": False, "detail": "No API key stored"}
            url = "https://api.anthropic.com/v1/models"
            req = urllib.request.Request(url, headers={
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"ok": True, "detail": "API key valid"}

        elif integration == "openai":
            key = get_credential("OPENAI_API_KEY", user_id)
            if not key:
                return {"ok": False, "detail": "No API key stored"}
            url = "https://api.openai.com/v1/models"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {key}",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                return {"ok": True, "detail": "API key valid"}

        elif integration == "notion":
            token = get_credential("NOTION_API_TOKEN", user_id)
            if not token:
                return {"ok": False, "detail": "No API token stored"}
            url = "https://api.notion.com/v1/users/me"
            req = urllib.request.Request(url, headers={
                "Authorization": f"Bearer {token}",
                "Notion-Version": "2022-06-28",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                name = data.get("name", "unknown")
                return {"ok": True, "detail": f"Connected as {name}"}

        else:
            return {"ok": False, "detail": f"Unknown integration: {integration}"}

    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
