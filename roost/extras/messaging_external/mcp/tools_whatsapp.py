"""MCP tools for sending WhatsApp messages via the Meta Cloud API.

Receive-side is wired in `roost/web/api_whatsapp.py` (webhook → agent).
This module exposes outbound send so the agent (or a recipe) can decide
to message a contact, and so RPA flows can deliver downloaded artefacts.
"""

from __future__ import annotations

import logging

from roost.config import WHATSAPP_ENABLED
from roost.mcp.server import mcp
from roost.extras.messaging_external.services import whatsapp

logger = logging.getLogger("roost.extras.messaging_external.mcp.tools_whatsapp")


def _check_enabled() -> dict | None:
    if not WHATSAPP_ENABLED:
        return {"error": "WhatsApp adapter disabled (set WHATSAPP_ENABLED=true)"}
    return None


@mcp.tool()
def whatsapp_send_text(to: str, body: str) -> dict:
    """Send a plain text WhatsApp message.

    Args:
        to: Recipient phone number in E.164 format, with or without leading `+`
            (e.g. "+6591234567" or "6591234567").
        body: Message text. Truncated to 4096 characters by the API.

    Returns:
        {"ok": True, "message_id": "wamid..."} on success, {"error": "..."} on failure.

    Notes:
        WhatsApp requires the recipient to have messaged the business
        within the last 24 hours, OR for the message to be a pre-approved
        template (see `whatsapp_send_template`). Free-form text outside
        the 24-hour window will be rejected by Meta.
    """
    if (gate := _check_enabled()):
        return gate
    return whatsapp.send_text_message(to, body)


@mcp.tool()
def whatsapp_send_document(
    to: str,
    path: str = "",
    link: str = "",
    caption: str = "",
    filename: str = "",
) -> dict:
    """Send a PDF / docx / xlsx etc. via WhatsApp.

    Args:
        to: Recipient phone in E.164 format.
        path: Absolute path to a local file. Uploaded to WhatsApp's media
            store automatically and referenced by ID.
        link: Publicly reachable URL. Use INSTEAD of `path` when the file
            already lives somewhere Meta can fetch (e.g. a signed Drive URL).
        caption: Optional caption (≤1024 chars).
        filename: Override the displayed filename (defaults to the file's
            basename when `path` is given).

    Provide exactly one of `path` or `link`.
    """
    if (gate := _check_enabled()):
        return gate
    if path and link:
        return {"error": "pass exactly one of path or link, not both"}
    if not path and not link:
        return {"error": "one of path or link is required"}
    return whatsapp.send_document(
        to,
        path=path or None,
        link=link or None,
        caption=caption or None,
        filename=filename or None,
    )


@mcp.tool()
def whatsapp_send_image(
    to: str,
    path: str = "",
    link: str = "",
    caption: str = "",
) -> dict:
    """Send a JPG/PNG via WhatsApp.

    Same source semantics as `whatsapp_send_document`: pass `path` for a
    local file (auto-uploaded) or `link` for a public URL.
    """
    if (gate := _check_enabled()):
        return gate
    if path and link:
        return {"error": "pass exactly one of path or link, not both"}
    if not path and not link:
        return {"error": "one of path or link is required"}
    return whatsapp.send_image(
        to,
        path=path or None,
        link=link or None,
        caption=caption or None,
    )


@mcp.tool()
def whatsapp_send_template(
    to: str,
    template_name: str,
    language_code: str = "en",
) -> dict:
    """Send a Meta-approved template message (required to start a conversation
    outside the 24-hour customer-service window).

    Args:
        to: Recipient phone in E.164.
        template_name: The exact name of an approved template in the Meta
            Business Manager.
        language_code: Template language tag (default "en").
    """
    if (gate := _check_enabled()):
        return gate
    return whatsapp.send_template_message(to, template_name, language_code)
