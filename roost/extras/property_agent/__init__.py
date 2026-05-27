"""Property-Agent Toolkit (Singapore CEA-registered salespersons).

Maps to named regulatory obligations:
- IRAS stamp duty calculator (Stamp Duties Act, post-Apr-2023 rates)
- PDPC DNC scrub (PDPA s.43 / Spam Control Act)
- CDD screening (CEA PC 01-21 / 02-23 — AML/CFT)

Always-on sub-feature: the stamp-duty calculator (pure-Python, no API).
Gated sub-features: DNC scrub (`DNC_ENABLED`), CDD screen (`CDD_ENABLED`).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request

from roost.extras._base import Bundle

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("roost.extras.property_agent")

_BUNDLE_DIR = Path(__file__).resolve().parent


def _build_pages_router():
    """Build the /property-agent/* page router with the bundle's templates
    added to the core Jinja search path. Called per-app-create so routes
    don't leak between test apps."""
    from fastapi import APIRouter, HTTPException
    from jinja2 import ChoiceLoader, FileSystemLoader

    from roost.config import (
        CDD_ENABLED,
        CDD_REFRESH_DAYS,
        CDD_VENDOR,
        DNC_ENABLED,
        PROPERTY_AGENT_ENABLED,
    )
    from roost.web.pages import _base_context, templates as core_templates

    # Add bundle template dir to the Jinja search path (idempotent — the
    # ChoiceLoader wrapper is only added once even across multiple calls).
    bundle_loader = FileSystemLoader(str(_BUNDLE_DIR / "templates"))
    existing = core_templates.env.loader
    if isinstance(existing, ChoiceLoader):
        if bundle_loader not in existing.loaders:
            existing.loaders = list(existing.loaders) + [bundle_loader]
    else:
        core_templates.env.loader = ChoiceLoader([existing, bundle_loader])

    pages = APIRouter()

    def _require_enabled():
        if not PROPERTY_AGENT_ENABLED:
            raise HTTPException(status_code=404, detail="Property-agent toolkit disabled")

    @pages.get("/property-agent/stamp-duty")
    def _stamp_duty(request: Request):
        _require_enabled()
        return core_templates.TemplateResponse("property_agent/stamp_duty.html", {
            **_base_context(request),
            "active_tab": "",
            "page_title": "Stamp Duty Calculator",
        })

    @pages.get("/property-agent/dnc-scrub")
    def _dnc_scrub(request: Request):
        _require_enabled()
        return core_templates.TemplateResponse("property_agent/dnc_scrub.html", {
            **_base_context(request),
            "enabled": DNC_ENABLED,
            "active_tab": "",
            "page_title": "DNC Scrub",
        })

    @pages.get("/property-agent/cdd-screen")
    def _cdd_screen(request: Request):
        _require_enabled()
        return core_templates.TemplateResponse("property_agent/cdd_screen.html", {
            **_base_context(request),
            "enabled": CDD_ENABLED,
            "vendor": CDD_VENDOR,
            "refresh_days": CDD_REFRESH_DAYS,
            "active_tab": "",
            "page_title": "CDD Screening",
        })

    return pages


def _register(app: "FastAPI", mcp) -> None:  # noqa: ARG001 — mcp picks tools up via decorator
    # Import MCP tools — @mcp.tool() decorators register them on the server.
    from roost.extras.property_agent.mcp import tools_cdd  # noqa: F401
    from roost.extras.property_agent.mcp import tools_iras  # noqa: F401
    from roost.extras.property_agent.mcp import tools_pdpc  # noqa: F401

    # Mount the JSON API router and the page routes.
    from roost.extras.property_agent.web.api import router as api_router
    app.include_router(api_router)
    app.include_router(_build_pages_router())


BUNDLE = Bundle(
    name="property_agent",
    flag_name="PROPERTY_AGENT_ENABLED",
    register=_register,
)
