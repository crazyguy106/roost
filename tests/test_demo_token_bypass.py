"""Magic-link demo-token bypass for UnifiedAuthMiddleware.

When DEMO_ACCESS_TOKEN is set, visiting any URL with ?demo=<token> mints
a session cookie as owner and 303-redirects to a clean URL. Otherwise
the middleware falls through to its existing redirect-to-login.
"""

from __future__ import annotations

import importlib
import pytest
from fastapi.testclient import TestClient


DEMO_TOKEN = "test-demo-token-1234567890abcdef"


def _build_client(monkeypatch, token: str = DEMO_TOKEN):
    """Reload the web.app module with the requested DEMO_ACCESS_TOKEN.

    The middleware reads the symbol at import-time, so we patch in place
    and use the same module across the test.
    """
    monkeypatch.setenv("DEMO_ACCESS_TOKEN", token)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("WEB_PASSWORD", "test-password")

    import roost.config as cfg
    importlib.reload(cfg)
    import roost.web.app as appmod
    importlib.reload(appmod)
    app = appmod.create_app()
    return TestClient(app, follow_redirects=False)


def test_matching_token_303s_with_session_cookie(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.get(f"/leads?demo={DEMO_TOKEN}")
    assert r.status_code == 303, r.text
    assert r.headers["location"] == "/leads"
    # Starlette signs the session cookie; presence proves the user dict landed.
    assert "session" in r.cookies


def test_clean_redirect_preserves_other_query_params(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.get(f"/chat?demo={DEMO_TOKEN}&provider=codex_cli")
    assert r.status_code == 303
    assert r.headers["location"] == "/chat?provider=codex_cli"


def test_wrong_token_falls_through_to_login(monkeypatch):
    client = _build_client(monkeypatch)
    r = client.get("/leads?demo=wrong-token")
    # Falls into the existing redirect-to-login path.
    assert r.status_code == 307
    assert "/auth/login-page" in r.headers["location"]


def test_feature_off_when_env_var_empty(monkeypatch):
    client = _build_client(monkeypatch, token="")
    r = client.get(f"/leads?demo={DEMO_TOKEN}")
    # With no DEMO_ACCESS_TOKEN set, the bypass is disabled.
    assert r.status_code == 307
    assert "/auth/login-page" in r.headers["location"]


def test_cookie_grants_subsequent_access(monkeypatch):
    """After the 303 lands, the session cookie alone should authenticate."""
    client = _build_client(monkeypatch)
    r1 = client.get(f"/leads?demo={DEMO_TOKEN}")
    assert r1.status_code == 303
    # Follow the redirect — TestClient reuses cookies across requests.
    r2 = client.get(r1.headers["location"])
    # Now we should NOT be redirected back to login.
    assert r2.status_code != 307 or "/auth/login-page" not in r2.headers.get("location", "")
