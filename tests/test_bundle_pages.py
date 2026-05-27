"""Smoke tests for every bundle's HTML page routes.

Loads the real FastAPI app (so every enabled bundle's `_register()` runs
and mounts its pages), strips the auth middleware, and asserts each
bundle page returns 200.

Regression guard: caught the 422 produced when `Request` was imported
only inside `_build_pages_router()` while the bundle's `__init__.py`
used `from __future__ import annotations`. FastAPI's `get_type_hints()`
could not resolve the stringified `request: Request` annotation on
route handlers, so every bundle page returned a validation error
instead of rendering. See P0-1 fix (2026-05-21).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app_no_auth(monkeypatch_module):
    """Build the real app once for the module, with auth middleware
    stripped so handlers run without a session cookie."""
    # Patch the live module attributes — env vars are baked into
    # roost.config constants at import time, so setenv is too late.
    # Bundle.enabled() reads via getattr(config, flag) at registration.
    import roost.config as cfg
    for flag in (
        "PROPERTY_AGENT_ENABLED", "RPA_ENABLED", "SME_OPS_ENABLED",
        "CRM_ENABLED", "LEAD_NURTURE_ENABLED", "MESSAGING_EXTERNAL_ENABLED",
        "WHATSAPP_ENABLED",
    ):
        monkeypatch_module.setattr(cfg, flag, True, raising=False)

    from roost.web.app import create_app

    app = create_app()
    # Drop UnifiedAuthMiddleware so the bundle handlers themselves run.
    app.user_middleware = [
        m for m in app.user_middleware if "UnifiedAuth" not in m.cls.__name__
    ]
    app.middleware_stack = app.build_middleware_stack()
    return app


@pytest.fixture(scope="module")
def monkeypatch_module():
    """Module-scoped monkeypatch (pytest's default monkeypatch is function-scoped)."""
    from _pytest.monkeypatch import MonkeyPatch
    mp = MonkeyPatch()
    yield mp
    mp.undo()


# Every bundle-owned HTML page route. New bundle pages MUST be added
# here — otherwise a regression like P0-1 (every page returns 422)
# would slip through CI again.
BUNDLE_PAGES = [
    # property_agent
    "/property-agent/stamp-duty",
    "/property-agent/dnc-scrub",
    "/property-agent/cdd-screen",
    # rpa
    "/rpa",
    # sme_ops
    "/sme/sync-status",
    "/sme/orders",
    "/sme/cashflow",
]


@pytest.mark.parametrize("path", BUNDLE_PAGES)
def test_bundle_page_renders_200(app_no_auth, path):
    """Each enabled bundle page must render without an annotation /
    type-resolution error. 422 here means a route handler's `Request`
    parameter could not be resolved by FastAPI's get_type_hints — the
    exact regression mode that motivated this file."""
    client = TestClient(app_no_auth, raise_server_exceptions=False)
    res = client.get(path)
    assert res.status_code == 200, (
        f"GET {path} returned {res.status_code}; "
        f"body: {res.text[:300]}"
    )
