"""Messaging-external bundle — WhatsApp Cloud API + WeChat Official Account
+ SMS (Twilio) + Chatwoot (self-hosted helpdesk) + AI CDR pipeline.

Mounts:
- Up to 4 API routers: api_whatsapp (gated by WHATSAPP_ENABLED),
  api_wechat (gated by WECHAT_ENABLED), api_sms (gated by SMS_ENABLED),
  api_chatwoot (gated by CHATWOOT_ENABLED)
- Up to 1 MCP tool module: tools_whatsapp (gated by WHATSAPP_ENABLED)
- 0 page routes (channels are webhook-only)
- 0 bundle-owned tables (channel strings are referenced by other bundles
  but persistence belongs to the consumer)

The master flag `MESSAGING_EXTERNAL_ENABLED` toggles the whole bundle;
the channel sub-flags (`WHATSAPP_ENABLED`, `WECHAT_ENABLED`, `SMS_ENABLED`,
`CHATWOOT_ENABLED`) decide which adapters actually mount.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from roost.extras._base import Bundle

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("roost.extras.messaging_external")


def _register(app: "FastAPI", mcp) -> None:  # noqa: ARG001
    from roost.config import (
        CHATWOOT_ENABLED,
        SMS_ENABLED,
        WECHAT_ENABLED,
        WHATSAPP_ENABLED,
    )

    if WHATSAPP_ENABLED:
        from roost.extras.messaging_external.mcp import tools_whatsapp  # noqa: F401
        from roost.extras.messaging_external.web.api_whatsapp import (
            router as whatsapp_router,
        )
        app.include_router(whatsapp_router)

    if WECHAT_ENABLED:
        from roost.extras.messaging_external.web.api_wechat import (
            router as wechat_router,
        )
        app.include_router(wechat_router)

    if SMS_ENABLED:
        from roost.extras.messaging_external.web.api_sms import (
            router as sms_router,
        )
        app.include_router(sms_router)

    if CHATWOOT_ENABLED:
        from roost.extras.messaging_external.web.api_chatwoot import (
            router as chatwoot_router,
        )
        app.include_router(chatwoot_router)


BUNDLE = Bundle(
    name="messaging_external",
    flag_name="MESSAGING_EXTERNAL_ENABLED",
    register=_register,
)
