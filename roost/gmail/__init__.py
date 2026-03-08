"""Gmail + Calendar Write integration - OAuth-based.

Reuses the existing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET from config.
Refresh tokens are stored in the oauth_tokens table.
Supports multiple Google accounts via the `account` parameter.
"""

import logging
from roost.config import GMAIL_ENABLED, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_ENABLED

logger = logging.getLogger("roost.gmail")

# Scopes requested during Gmail OAuth consent
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/gmail.settings.basic",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/contacts.readonly",
]


def is_gmail_available(account: str | None = None) -> bool:
    """Check whether Gmail integration is ready to use."""
    if not GOOGLE_ENABLED:
        return False
    if not GMAIL_ENABLED:
        return False
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return False
    try:
        from roost.gmail.client import get_stored_refresh_token
        return bool(get_stored_refresh_token(account=account))
    except Exception:
        return False


def get_gmail_service(account: str | None = None):
    """Get an authenticated Gmail API service instance, or None."""
    if not is_gmail_available(account=account):
        return None
    try:
        from roost.gmail.client import build_gmail_service
        return build_gmail_service(account=account)
    except Exception:
        logger.exception("Failed to build Gmail service")
        return None


def get_calendar_service(account: str | None = None):
    """Get an authenticated Calendar API service instance, or None."""
    if not is_gmail_available(account=account):
        return None
    try:
        from roost.gmail.client import build_calendar_service
        return build_calendar_service(account=account)
    except Exception:
        logger.exception("Failed to build Calendar service")
        return None


def get_slides_service(account: str | None = None):
    """Get an authenticated Google Slides API service instance, or None."""
    if not is_gmail_available(account=account):
        return None
    try:
        from roost.gmail.client import build_slides_service
        return build_slides_service(account=account)
    except Exception:
        logger.exception("Failed to build Slides service")
        return None


def get_sheets_service(account: str | None = None):
    """Get an authenticated Google Sheets API service instance, or None."""
    if not is_gmail_available(account=account):
        return None
    try:
        from roost.gmail.client import build_sheets_service
        return build_sheets_service(account=account)
    except Exception:
        logger.exception("Failed to build Sheets service")
        return None


def get_docs_service(account: str | None = None):
    """Get an authenticated Google Docs API service instance, or None."""
    if not is_gmail_available(account=account):
        return None
    try:
        from roost.gmail.client import build_docs_service
        return build_docs_service(account=account)
    except Exception:
        logger.exception("Failed to build Docs service")
        return None
