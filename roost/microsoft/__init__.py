"""Microsoft Graph API integration — OAuth-based via MSAL.

Provides Outlook email, Calendar, OneDrive, Excel, Teams, and SharePoint
access via Microsoft Graph. Tokens stored in oauth_tokens table (provider='microsoft').
"""

import logging
from roost.config import MS_ENABLED, MS_CLIENT_ID, MS_CLIENT_SECRET

logger = logging.getLogger("roost.microsoft")

# Microsoft Graph delegated scopes
MS_SCOPES = [
    # Email
    "Mail.Read",
    "Mail.Send",
    # Calendar
    "Calendars.ReadWrite",
    # OneDrive + Excel workbooks
    "Files.ReadWrite",
    # SharePoint sites & document libraries
    "Sites.ReadWrite.All",
    # Teams — channels
    "Team.ReadBasic.All",
    "Channel.ReadBasic.All",
    "ChannelMessage.Read.All",
    "ChannelMessage.Send",
    # Teams — chats
    "Chat.ReadWrite",
    # Identity + user lookup
    "User.Read",
    "User.ReadBasic.All",
    # Note: offline_access is added automatically by MSAL — do not include here
]


def is_microsoft_available(user_id: int | None = None) -> bool:
    """Check whether Microsoft Graph integration is ready to use.

    Args:
        user_id: Check for a specific user's tokens. If None, uses current
                 user context.
    """
    if not MS_ENABLED:
        return False
    if not MS_CLIENT_ID or not MS_CLIENT_SECRET:
        return False
    # Check if we have a stored token cache for this user
    try:
        from roost.microsoft.client import get_stored_token_cache
        return bool(get_stored_token_cache(user_id=user_id))
    except Exception:
        return False


def get_graph_session(user_id: int | None = None):
    """Get an authenticated requests.Session for Microsoft Graph, or None.

    Args:
        user_id: Build session for a specific user. If None, uses current
                 user context.
    """
    if not is_microsoft_available(user_id=user_id):
        return None
    try:
        from roost.microsoft.client import build_graph_session
        return build_graph_session(user_id=user_id)
    except Exception:
        logger.exception("Failed to build Graph session")
        return None
