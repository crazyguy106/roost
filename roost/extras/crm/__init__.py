"""CRM bundle — multi-provider CRM abstraction (Attio, Zoho, Pipedrive,
HubSpot, Salesforce, local).

Mounts:
- 3 API routers: api_crm (provider ops), api_attio_webhook, auth_zoho (OAuth)
- 1 MCP tool module: tools_crm
- 0 page routes (CRM provider selection lives in core settings)
- 0 bundle tables (CRM is stateless; persistence belongs to the provider)

Gated by `CRM_ENABLED`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from roost.extras._base import Bundle

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("roost.extras.crm")


def _register(app: "FastAPI", mcp) -> None:  # noqa: ARG001
    from roost.extras.crm.mcp import tools_crm  # noqa: F401

    from roost.extras.crm.web.api_attio_webhook import router as attio_webhook_router
    from roost.extras.crm.web.api_crm import router as crm_api_router
    from roost.extras.crm.web.auth_zoho import router as zoho_auth_router

    app.include_router(crm_api_router)
    app.include_router(attio_webhook_router)
    app.include_router(zoho_auth_router)


BUNDLE = Bundle(
    name="crm",
    flag_name="CRM_ENABLED",
    register=_register,
)
