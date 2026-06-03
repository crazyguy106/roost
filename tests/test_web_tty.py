"""Smoke tests for the web tty page + WebSocket endpoint.

The PTY/tmux bridge itself is exercised via the real /ws/tty WebSocket against
a tmux session inside the container; here we only check that the routes are
wired up and that auth gates the WebSocket.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def client_no_auth():
    from fastapi.testclient import TestClient
    from roost.web.app import create_app

    app = create_app()
    app.user_middleware = [
        m for m in app.user_middleware if "UnifiedAuth" not in m.cls.__name__
    ]
    app.middleware_stack = app.build_middleware_stack()
    return TestClient(app)


@pytest.fixture
def client_with_auth():
    """Real auth stack — used to confirm the WebSocket refuses anonymous clients."""
    from fastapi.testclient import TestClient
    from roost.web.app import create_app

    return TestClient(create_app())


def test_tty_page_renders(client_no_auth):
    res = client_no_auth.get("/tty")
    assert res.status_code == 200, res.text
    body = res.text
    # xterm.js bootstrap + the WebSocket path must be in the page.
    assert "xterm" in body
    assert "/ws/tty" in body


def test_tty_ws_rejects_unauthenticated(client_with_auth):
    """Anonymous WS upgrade should be closed with 1008 (policy violation)."""
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client_with_auth.websocket_connect("/ws/tty") as ws:
            ws.receive_bytes()
    assert excinfo.value.code == 1008


def test_tty_ws_route_registered(client_no_auth):
    """The /ws/tty route exists on the app — a plain GET should not 404."""
    # WebSocket routes return 404 on plain HTTP GET in Starlette, but the
    # route is registered. Inspect the router directly.
    from roost.web.app import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/ws/tty" in paths
    assert "/tty" in paths
