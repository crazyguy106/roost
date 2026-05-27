"""RPA bundle — durable, pausable browser automation.

Mounts:
- 2 API routers: api_rpa, api_sidecar
- 1 page route: /rpa (run viewer)
- 1 MCP tool module: tools_rpa
- 2 bundle-owned tables: rpa_runs, rpa_flow_configs

Bot integration (handlers/rpa_input.py) is wired imperatively from
`roost.bot.main` gated on `RPA_ENABLED`, since the bot has no
register-yourself hook yet.

Gated by `RPA_ENABLED`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request

from roost.extras._base import Bundle

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("roost.extras.rpa")

_BUNDLE_DIR = Path(__file__).resolve().parent


def _build_pages_router():
    """/rpa run viewer. Per-app-create."""
    from fastapi import APIRouter, HTTPException
    from jinja2 import ChoiceLoader, FileSystemLoader

    from roost.web.pages import _base_context, templates as core_templates

    bundle_loader = FileSystemLoader(str(_BUNDLE_DIR / "templates"))
    existing = core_templates.env.loader
    if isinstance(existing, ChoiceLoader):
        if bundle_loader not in existing.loaders:
            existing.loaders = list(existing.loaders) + [bundle_loader]
    else:
        core_templates.env.loader = ChoiceLoader([existing, bundle_loader])

    pages = APIRouter()

    def _require_enabled():
        from roost.config import RPA_ENABLED
        if not RPA_ENABLED:
            raise HTTPException(status_code=404, detail="RPA bundle disabled")

    @pages.get("/rpa")
    def rpa_page(request: Request):
        _require_enabled()
        return core_templates.TemplateResponse(
            "rpa.html", {**_base_context(request)}
        )

    return pages


def _register(app: "FastAPI", mcp) -> None:  # noqa: ARG001
    from roost.extras.rpa.mcp import tools_rpa  # noqa: F401

    from roost.extras.rpa.web.api_rpa import router as rpa_api_router
    from roost.extras.rpa.web.api_sidecar import router as sidecar_router

    app.include_router(rpa_api_router)
    app.include_router(sidecar_router)
    app.include_router(_build_pages_router())


def _schema_sql() -> str:
    """RPA runs + flow configs."""
    return """
    -- RPA runs: durable, pausable browser-automation runs.
    CREATE TABLE IF NOT EXISTS rpa_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id         TEXT NOT NULL DEFAULT '',
        portal_slug     TEXT NOT NULL,
        recipe_id       INTEGER,
        status          TEXT NOT NULL DEFAULT 'running'
            CHECK (status IN ('running', 'awaiting_input', 'completed', 'failed', 'cancelled')),
        state_json      TEXT NOT NULL DEFAULT '{}',
        prompt_text     TEXT DEFAULT '',
        prompt_kind     TEXT DEFAULT '',
        last_input      TEXT DEFAULT '',
        result_json     TEXT DEFAULT '{}',
        error           TEXT DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_rpa_runs_user_status ON rpa_runs(user_id, status);
    CREATE INDEX IF NOT EXISTS idx_rpa_runs_status ON rpa_runs(status);

    -- RPA flow configs: data-driven step lists per portal.
    CREATE TABLE IF NOT EXISTS rpa_flow_configs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        portal_slug     TEXT NOT NULL,
        name            TEXT DEFAULT '',
        login_url       TEXT DEFAULT '',
        steps_json      TEXT NOT NULL DEFAULT '[]',
        otp_config_json TEXT NOT NULL DEFAULT '{}',
        user_id         TEXT NOT NULL DEFAULT '',
        enabled         INTEGER DEFAULT 1,
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(portal_slug, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_rpa_flow_configs_user ON rpa_flow_configs(user_id);
    """


BUNDLE = Bundle(
    name="rpa",
    flag_name="RPA_ENABLED",
    register=_register,
    schema_sql=_schema_sql,
)
