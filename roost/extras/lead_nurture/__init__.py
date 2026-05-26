"""Lead-nurture bundle — multi-channel lead cadences with Telegram approval.

Mounts:
- 1 API router: api_leads
- 2 MCP tool modules: tools_leads, tools_lead_pipeline
- 3 bundle-owned tables: nurture_cadences, nurture_enrollments, cadence_preapprovals
- Bot wiring (handlers/nurture_approval.py + scheduler tick) is imperative
  in roost.bot.*; this bundle owns the file but not the registration.

Depends on `roost.extras.crm` (uses CRM provider for person/deal lookup).
Gated by `LEAD_NURTURE_ENABLED`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from roost.extras._base import Bundle

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("roost.extras.lead_nurture")


def _register(app: "FastAPI", mcp) -> None:  # noqa: ARG001
    from roost.extras.lead_nurture.mcp import tools_lead_pipeline  # noqa: F401
    from roost.extras.lead_nurture.mcp import tools_leads  # noqa: F401

    from roost.extras.lead_nurture.web.api_leads import router as leads_router
    from roost.extras.lead_nurture.web.pages import _build_pages_router

    app.include_router(leads_router)
    app.include_router(_build_pages_router())


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS nurture_cadences (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        slug         TEXT NOT NULL,
        name         TEXT NOT NULL,
        description  TEXT DEFAULT '',
        vertical     TEXT NOT NULL DEFAULT 'generic',
        steps_json   TEXT NOT NULL DEFAULT '[]',
        enabled      INTEGER NOT NULL DEFAULT 1,
        source       TEXT NOT NULL DEFAULT 'library' CHECK (source IN ('library', 'user')),
        user_id      TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(slug, user_id)
    );
    CREATE INDEX IF NOT EXISTS idx_nurture_cadences_vertical ON nurture_cadences(vertical);
    CREATE INDEX IF NOT EXISTS idx_nurture_cadences_enabled ON nurture_cadences(enabled);

    CREATE TABLE IF NOT EXISTS nurture_enrollments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        cadence_id      INTEGER NOT NULL REFERENCES nurture_cadences(id) ON DELETE CASCADE,
        cadence_slug    TEXT NOT NULL DEFAULT '',
        crm_person_id   TEXT NOT NULL DEFAULT '',
        crm_deal_id     TEXT NOT NULL DEFAULT '',
        contact_email   TEXT NOT NULL DEFAULT '',
        contact_phone   TEXT NOT NULL DEFAULT '',
        contact_name    TEXT NOT NULL DEFAULT '',
        channel         TEXT NOT NULL DEFAULT 'email',
        fields_json     TEXT NOT NULL DEFAULT '{}',
        status          TEXT NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'paused', 'completed', 'exited')),
        current_step    INTEGER NOT NULL DEFAULT 0,
        started_at      TEXT NOT NULL DEFAULT (datetime('now')),
        last_step_at    TEXT DEFAULT NULL,
        next_run_at     TEXT DEFAULT NULL,
        pause_reason    TEXT DEFAULT '',
        source          TEXT NOT NULL DEFAULT '',
        user_id         TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_nurture_enrollments_status ON nurture_enrollments(status);
    CREATE INDEX IF NOT EXISTS idx_nurture_enrollments_next_run ON nurture_enrollments(next_run_at);
    CREATE INDEX IF NOT EXISTS idx_nurture_enrollments_person ON nurture_enrollments(crm_person_id);
    CREATE INDEX IF NOT EXISTS idx_nurture_enrollments_cadence ON nurture_enrollments(cadence_id);

    CREATE TABLE IF NOT EXISTS cadence_preapprovals (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        cadence_slug    TEXT NOT NULL DEFAULT '*',
        source          TEXT NOT NULL DEFAULT '*',
        channel         TEXT NOT NULL DEFAULT '*',
        vertical        TEXT NOT NULL DEFAULT '*',
        note            TEXT DEFAULT '',
        user_id         TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_cadence_preapprovals_lookup
        ON cadence_preapprovals(cadence_slug, source, channel, vertical);
    """


BUNDLE = Bundle(
    name="lead_nurture",
    flag_name="LEAD_NURTURE_ENABLED",
    register=_register,
    schema_sql=_schema_sql,
)
