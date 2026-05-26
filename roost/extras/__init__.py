"""Vertical bundle registry.

Each bundle is a self-contained package under `roost/extras/<name>/`
with its own services, MCP tools, web routers, templates, bot handlers,
and DB schema. Bundles register themselves through `Bundle` objects.

A bundle's code is only imported when its master flag is true — disabled
bundles cost zero import time, zero MCP tools, and zero web routes.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING

from roost.extras._base import Bundle

if TYPE_CHECKING:
    import sqlite3

    from fastapi import FastAPI

logger = logging.getLogger("roost.extras")


# Order matters: bundles may depend on earlier ones (e.g. lead_nurture → crm).
# Each entry is the importable name under roost.extras.
_BUNDLE_NAMES: tuple[str, ...] = (
    "property_agent",
    "sme_ops",
    "crm",
    "rpa",
    "lead_nurture",
    "messaging_external",
)


def _iter_enabled() -> list[Bundle]:
    """Import each bundle's package and yield its Bundle descriptor if the
    master flag is on. Import failures are logged but don't crash the app —
    a missing/broken bundle should never take Roost down."""
    found: list[Bundle] = []
    for name in _BUNDLE_NAMES:
        try:
            mod = importlib.import_module(f"roost.extras.{name}")
        except ModuleNotFoundError:
            continue  # bundle not yet extracted
        except Exception:
            logger.exception("Failed to import bundle %s", name)
            continue
        bundle = getattr(mod, "BUNDLE", None)
        if bundle is None:
            logger.warning("Bundle %s defines no BUNDLE descriptor — skipping", name)
            continue
        if bundle.enabled():
            found.append(bundle)
    return found


def load_enabled(app: "FastAPI", mcp) -> list[str]:
    """Register every enabled bundle's MCP tools and web routers.

    Called once from `roost.web.app` after core routers are mounted and
    from `roost.mcp.server` after core tools are imported. Returns the
    list of bundle names that loaded successfully.
    """
    loaded: list[str] = []
    for bundle in _iter_enabled():
        try:
            bundle.register(app, mcp)
        except Exception:
            logger.exception("Bundle %s failed to register", bundle.name)
            continue
        loaded.append(bundle.name)
    if loaded:
        logger.info("Loaded bundles: %s", ", ".join(loaded))
    return loaded


def run_bundle_schemas(conn: "sqlite3.Connection") -> None:
    """Execute schema SQL for every bundle (regardless of master flag).

    Schemas are `CREATE TABLE IF NOT EXISTS` so creating them when the
    bundle is off costs nothing — but ensures tests, dev tooling, and a
    later flip of the flag find their tables already present.
    """
    for name in _BUNDLE_NAMES:
        try:
            mod = importlib.import_module(f"roost.extras.{name}")
        except ModuleNotFoundError:
            continue
        except Exception:
            logger.exception("Failed to import bundle %s for schema apply", name)
            continue
        bundle = getattr(mod, "BUNDLE", None)
        if bundle is None:
            continue
        sql = bundle.schema_sql()
        if not sql:
            continue
        try:
            conn.executescript(sql)
        except Exception:
            logger.exception("Bundle %s schema apply failed", bundle.name)
