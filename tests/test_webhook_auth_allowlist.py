"""Inbound webhook paths must bypass UnifiedAuthMiddleware.

Third-party senders (Meta/WhatsApp, WeChat, Twilio SMS, Chatwoot, Attio,
CRM providers) cannot carry a session cookie, so their webhook POSTs must
reach the handler — which verifies its own per-vendor signature — instead
of being 307-redirected to /auth/login-page.

Regression guard: the Chatwoot and SMS adapters shipped with handlers that
verify signatures but whose paths were never added to the middleware
allowlist, so every delivery silently bounced to the login page. This test
drives the full create_app() stack (middleware included) to lock the
allowlist down.
"""

from __future__ import annotations

import importlib

from fastapi.testclient import TestClient


def _build_client(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("WEB_PASSWORD", "test-password")

    import roost.config as cfg
    importlib.reload(cfg)
    import roost.web.app as appmod
    importlib.reload(appmod)
    return TestClient(appmod.create_app(), follow_redirects=False)


# Every path the auth middleware is supposed to wave through unauthenticated.
WEBHOOK_PATHS = [
    "/api/whatsapp/webhook",
    "/api/wechat/webhook",
    "/api/sms/webhook",
    "/api/chatwoot/webhook",
    "/api/attio/webhook",
    "/api/crm/attio/webhook",
]


def _is_login_redirect(resp) -> bool:
    return resp.status_code == 307 and "/auth/login-page" in resp.headers.get(
        "location", ""
    )


def test_webhook_paths_bypass_auth(monkeypatch):
    """No inbound webhook path may be redirected to the login page.

    The handler may answer 401 (bad signature), 400, 404 (bundle disabled /
    route absent), etc. — anything except the auth middleware's redirect.
    """
    client = _build_client(monkeypatch)
    for path in WEBHOOK_PATHS:
        resp = client.post(path, json={})
        assert not _is_login_redirect(resp), (
            f"{path} was redirected to login — missing from the auth allowlist"
        )


def test_protected_path_still_redirects_to_login(monkeypatch):
    """Control: a normal page unauthenticated DOES bounce to login, proving
    the middleware is actually gating (so the test above isn't vacuous)."""
    client = _build_client(monkeypatch)
    resp = client.get("/leads")
    assert _is_login_redirect(resp), (
        f"expected login redirect, got {resp.status_code} {resp.headers.get('location')}"
    )
