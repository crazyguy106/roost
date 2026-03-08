"""Notion mirror — push Roost data to Notion for visual dashboards.

SQLite is the source of truth. Notion is a read-friendly mirror
that supports bidirectional edits via polling.
"""

import logging

logger = logging.getLogger("roost.notion")


def get_notion_client():
    """Get the singleton Notion client, or None if unavailable."""
    try:
        from roost.notion.client import get_client
        return get_client()
    except Exception:
        return None


def is_notion_available() -> bool:
    """Check if Notion sync is configured and the client can connect."""
    try:
        from roost.config import NOTION_SYNC_ENABLED, NOTION_API_TOKEN
        if not NOTION_SYNC_ENABLED or not NOTION_API_TOKEN:
            return False
        client = get_notion_client()
        return client is not None
    except Exception:
        return False
