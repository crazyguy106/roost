"""Build authenticated Gmail / Calendar API service objects from stored tokens.

Supports multiple Google accounts via the `account` parameter (email address).
When account is empty/None, uses DEFAULT_GOOGLE_ACCOUNT or first available token.
"""

import logging
from datetime import datetime
from roost.config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, DEFAULT_GOOGLE_ACCOUNT

logger = logging.getLogger("roost.gmail.client")

PROVIDER = "google"
SCOPE_KEY = "gmail+calendar"


def _resolve_user_id(user_id: int | None) -> int | None:
    """Resolve user_id: use provided value, or get from user context."""
    if user_id is not None:
        return user_id
    try:
        from roost.user_context import get_current_user_id
        return get_current_user_id()
    except Exception:
        return None


def _resolve_account(account: str | None) -> str:
    """Resolve account: use provided value, or DEFAULT_GOOGLE_ACCOUNT, or empty."""
    if account:
        return account.strip().lower()
    return DEFAULT_GOOGLE_ACCOUNT.strip().lower()


def get_stored_refresh_token(user_id: int | None = None, account: str | None = None) -> str | None:
    """Retrieve the stored refresh token from the DB.

    Args:
        user_id: Specific user. If None, uses current user context or
                 falls back to any available token.
        account: Google account email. If a specific account is requested
                 and not found, returns None (no silent fallback).
                 If empty/None, uses DEFAULT_GOOGLE_ACCOUNT or first available.
    """
    uid = _resolve_user_id(user_id)
    acct = _resolve_account(account)
    # Track whether caller explicitly requested a specific account
    explicit_account = bool(account and account.strip())
    from roost.database import get_connection

    conn = get_connection()

    # Try exact account match first
    if uid is not None and acct:
        row = conn.execute(
            "SELECT refresh_token FROM oauth_tokens WHERE user_id = ? AND provider = ? AND scope = ? AND account = ?",
            (uid, PROVIDER, SCOPE_KEY, acct),
        ).fetchone()
        if row:
            conn.close()
            return row["refresh_token"]

    # Explicit account requested: try without user_id constraint (account may be
    # stored under a different web login user_id on the same VPS)
    if acct:
        row = conn.execute(
            "SELECT refresh_token FROM oauth_tokens WHERE provider = ? AND scope = ? AND account = ? ORDER BY updated_at DESC LIMIT 1",
            (PROVIDER, SCOPE_KEY, acct),
        ).fetchone()
        if row:
            conn.close()
            return row["refresh_token"]

    # If caller explicitly asked for a specific account and it wasn't found, don't fall back
    if explicit_account:
        conn.close()
        return None

    # Fall back: user_id match, any account (or empty account for legacy)
    if uid is not None:
        row = conn.execute(
            "SELECT refresh_token FROM oauth_tokens WHERE user_id = ? AND provider = ? AND scope = ? ORDER BY account = '' ASC, updated_at DESC LIMIT 1",
            (uid, PROVIDER, SCOPE_KEY),
        ).fetchone()
        if row:
            conn.close()
            return row["refresh_token"]

    # Final fallback: any token for this provider
    row = conn.execute(
        "SELECT refresh_token FROM oauth_tokens WHERE provider = ? AND scope = ? ORDER BY updated_at DESC LIMIT 1",
        (PROVIDER, SCOPE_KEY),
    ).fetchone()
    conn.close()
    return row["refresh_token"] if row else None


def store_tokens(refresh_token: str, access_token: str = "",
                 expires_at: str = "", user_id: int | None = None,
                 account: str = "") -> None:
    """Upsert OAuth tokens in the DB.

    Args:
        user_id: User to store tokens for. If None, uses current user context.
        account: Google account email (for multi-account support).
    """
    uid = _resolve_user_id(user_id)
    acct = account.strip().lower()
    from roost.database import get_connection

    conn = get_connection()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute(
        """INSERT INTO oauth_tokens (user_id, provider, scope, account, refresh_token, access_token, expires_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, provider, scope, account)
           DO UPDATE SET refresh_token = excluded.refresh_token,
                         access_token = excluded.access_token,
                         expires_at = excluded.expires_at,
                         updated_at = excluded.updated_at""",
        (uid, PROVIDER, SCOPE_KEY, acct, refresh_token, access_token, expires_at, now),
    )
    conn.commit()
    conn.close()
    logger.info("Stored OAuth tokens for user_id=%s account=%s", uid, acct or "(default)")


def list_google_accounts(user_id: int | None = None) -> list[dict]:
    """List all connected Google accounts on this instance.

    Returns list of dicts with account (email), updated_at.
    Searches all user_ids since accounts may be authorized under different web logins.
    """
    from roost.database import get_connection

    conn = get_connection()
    rows = conn.execute(
        "SELECT account, updated_at FROM oauth_tokens WHERE provider = ? AND scope = ? ORDER BY updated_at DESC",
        (PROVIDER, SCOPE_KEY),
    ).fetchall()
    conn.close()
    return [{"account": r["account"] or "(unknown)", "updated_at": r["updated_at"]} for r in rows]


def _build_credentials(account: str | None = None):
    """Build google.oauth2.credentials.Credentials from stored refresh token."""
    from google.oauth2.credentials import Credentials

    refresh_token = get_stored_refresh_token(account=account)
    if not refresh_token:
        acct_str = f" for account '{account}'" if account else ""
        raise RuntimeError(f"No Gmail refresh token stored{acct_str}. Visit /auth/gmail to authorize.")

    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
    )


def build_gmail_service(account: str | None = None):
    """Build an authenticated Gmail API v1 service."""
    from googleapiclient.discovery import build

    creds = _build_credentials(account=account)
    return build("gmail", "v1", credentials=creds)


def build_calendar_service(account: str | None = None):
    """Build an authenticated Google Calendar API v3 service."""
    from googleapiclient.discovery import build

    creds = _build_credentials(account=account)
    return build("calendar", "v3", credentials=creds)


def build_slides_service(account: str | None = None):
    """Build an authenticated Google Slides API v1 service."""
    from googleapiclient.discovery import build

    creds = _build_credentials(account=account)
    return build("slides", "v1", credentials=creds)


def build_sheets_service(account: str | None = None):
    """Build an authenticated Google Sheets API v4 service."""
    from googleapiclient.discovery import build

    creds = _build_credentials(account=account)
    return build("sheets", "v4", credentials=creds)


def build_docs_service(account: str | None = None):
    """Build an authenticated Google Docs API v1 service."""
    from googleapiclient.discovery import build

    creds = _build_credentials(account=account)
    return build("docs", "v1", credentials=creds)


def build_people_service(account: str | None = None):
    """Build an authenticated Google People API v1 service."""
    from googleapiclient.discovery import build

    creds = _build_credentials(account=account)
    return build("people", "v1", credentials=creds)
