"""MCP tools for the self-hosted Chatwoot inbox (FA edition).

Outbound message sending in FA edition is already exposed via the
`whatsapp_*` tools — those delegate to Chatwoot under the hood when
`CHATWOOT_ENABLED=true`. This module surfaces *introspection* helpers
that don't have a WhatsApp analogue, starting with template discovery.

Tools here are gated by `CHATWOOT_ENABLED`. The module is only imported
when the flag is on (see `roost.extras.messaging_external._register`),
so `@mcp.tool()` won't fire for disabled installs.
"""

from __future__ import annotations

import logging

from roost.config import CHATWOOT_ENABLED
from roost.extras.messaging_external.services import chatwoot
from roost.mcp.server import mcp

logger = logging.getLogger("roost.extras.messaging_external.mcp.tools_chatwoot")


def _check_enabled() -> dict | None:
    if not CHATWOOT_ENABLED:
        return {"error": "Chatwoot adapter disabled (set CHATWOOT_ENABLED=true)"}
    return None


@mcp.tool()
def chatwoot_list_templates(inbox_id: int = 0) -> dict:
    """List WhatsApp templates synced to a Chatwoot inbox.

    Returns the approved templates pulled from Meta's WABA, as Chatwoot
    sees them after the periodic sync. Use this before calling
    `whatsapp_send_template` so you don't hard-code names that may have
    been retired or renamed.

    Args:
        inbox_id: The Chatwoot inbox id (numeric). Pass `0` (or omit) to
            use the default from `CHATWOOT_INBOX_ID`.

    Returns:
        {"ok": True, "templates": [{"name": str, "language": str,
        "category": str, ...}, ...]} on success, {"error": "..."} on
        failure (adapter disabled, missing inbox id, HTTP error).
    """
    if (gate := _check_enabled()):
        return gate
    return chatwoot.list_templates(inbox_id=inbox_id or None)
