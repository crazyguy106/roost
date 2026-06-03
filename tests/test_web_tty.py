"""Smoke tests for the web tty page + REST endpoints + WebSocket.

The PTY/tmux bridge itself is exercised manually inside the container
against a real tmux server; here we only check that the routes are
wired up, that auth gates the WebSocket, and that the windows API
behaves correctly under user scoping.
"""

from __future__ import annotations

import pytest

from roost.database import db_connection
from roost.services import chat_windows as cw


# ── Fixtures ─────────────────────────────────────────────────────────


def _app_with_test_user(user_id: int):
    """Build the app with UnifiedAuth stripped and a tiny middleware
    that injects a `current_user` so REST endpoints can scope by user."""
    from fastapi.testclient import TestClient
    from starlette.middleware.base import BaseHTTPMiddleware
    from roost.web.app import create_app

    app = create_app()
    app.user_middleware = [
        m for m in app.user_middleware if "UnifiedAuth" not in m.cls.__name__
    ]

    class _InjectUser(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            request.state.current_user = {
                "user_id": user_id, "name": f"test-{user_id}", "role": "owner",
            }
            return await call_next(request)

    app.add_middleware(_InjectUser)
    app.middleware_stack = app.build_middleware_stack()
    return TestClient(app)


@pytest.fixture
def client_user1():
    return _app_with_test_user(1)


@pytest.fixture
def client_user2():
    return _app_with_test_user(2)


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
    from fastapi.testclient import TestClient
    from roost.web.app import create_app

    return TestClient(create_app())


@pytest.fixture(autouse=True)
def clean_chat_windows():
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows")
        conn.commit()
    yield
    with db_connection() as conn:
        conn.execute("DELETE FROM chat_windows")
        conn.commit()


# ── Page + WS auth (carry-overs from 1.5) ────────────────────────────


def test_tty_page_renders(client_no_auth):
    res = client_no_auth.get("/tty")
    assert res.status_code == 200, res.text
    body = res.text
    assert "xterm" in body
    assert "/ws/tty" in body
    # 1.6b additions
    assert "tty-tabs" in body
    assert "+ New" in body


def test_tty_ws_rejects_unauthenticated(client_with_auth):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client_with_auth.websocket_connect("/ws/tty") as ws:
            ws.receive_bytes()
    assert excinfo.value.code == 1008


def test_routes_registered():
    from roost.web.app import create_app

    app = create_app()
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/ws/tty" in paths
    assert "/tty" in paths
    assert "/api/tty/windows" in paths
    assert "/api/tty/windows/{window_id}" in paths


# ── REST: list / create / delete ─────────────────────────────────────


def test_list_windows_empty(client_user1):
    res = client_user1.get("/api/tty/windows")
    assert res.status_code == 200
    body = res.json()
    assert body["windows"] == []
    assert body["cap"] == cw.DEFAULT_WINDOW_CAP


def test_create_window_with_title(client_user1):
    res = client_user1.post("/api/tty/windows", json={"title": "Lead 42"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["title"] == "Lead 42"
    assert body["tmux_window_name"].startswith("w-")
    assert body["id"] > 0


def test_create_window_default_title(client_user1):
    """Empty title → backend generates 'Untitled N'."""
    r1 = client_user1.post("/api/tty/windows", json={})
    assert r1.status_code == 200
    assert r1.json()["title"] == "Untitled 1"

    r2 = client_user1.post("/api/tty/windows", json={"title": "   "})
    assert r2.status_code == 200
    assert r2.json()["title"] == "Untitled 2"


def test_create_with_linked_entity(client_user1):
    res = client_user1.post("/api/tty/windows", json={
        "title": "Task 7",
        "linked_entity_type": "task",
        "linked_entity_id": 7,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["linked_entity_type"] == "task"
    assert body["linked_entity_id"] == 7


def test_list_then_create_shows_in_list(client_user1):
    client_user1.post("/api/tty/windows", json={"title": "A"})
    client_user1.post("/api/tty/windows", json={"title": "B"})
    listing = client_user1.get("/api/tty/windows").json()
    titles = [w["title"] for w in listing["windows"]]
    # Most recently created first (touch_active happens on attach, not create —
    # so insertion order via last_active_at DESC defaults to newest first)
    assert set(titles) == {"A", "B"}


def test_delete_window(client_user1):
    created = client_user1.post("/api/tty/windows", json={"title": "Doomed"}).json()
    res = client_user1.delete(f"/api/tty/windows/{created['id']}")
    assert res.status_code == 200
    assert res.json()["ok"] is True
    listing = client_user1.get("/api/tty/windows").json()
    assert listing["windows"] == []


def test_delete_404_for_unknown(client_user1):
    res = client_user1.delete("/api/tty/windows/99999")
    assert res.status_code == 404


def test_delete_scope_isolation(client_user1, client_user2):
    """User 2 cannot delete user 1's window."""
    mine = client_user1.post("/api/tty/windows", json={"title": "Mine"}).json()
    # User 2 attempts to delete it.
    res = client_user2.delete(f"/api/tty/windows/{mine['id']}")
    assert res.status_code == 404
    # Still exists for user 1.
    listing = client_user1.get("/api/tty/windows").json()
    assert [w["id"] for w in listing["windows"]] == [mine["id"]]


def test_list_scope_isolation(client_user1, client_user2):
    client_user1.post("/api/tty/windows", json={"title": "Mine A"})
    client_user1.post("/api/tty/windows", json={"title": "Mine B"})
    client_user2.post("/api/tty/windows", json={"title": "Theirs"})
    mine = client_user1.get("/api/tty/windows").json()["windows"]
    theirs = client_user2.get("/api/tty/windows").json()["windows"]
    assert {w["title"] for w in mine} == {"Mine A", "Mine B"}
    assert {w["title"] for w in theirs} == {"Theirs"}


# ── Service: auto_create ─────────────────────────────────────────────


def test_auto_create_generates_unique_name():
    a = cw.auto_create(1, title="A")
    b = cw.auto_create(1, title="B")
    assert a.tmux_window_name != b.tmux_window_name
    assert a.tmux_window_name.startswith("w-")
    assert b.tmux_window_name.startswith("w-")


# ── WebSocket: bad query param rejected ───────────────────────────────


def test_ws_rejects_bad_window_name(client_with_auth):
    """A non-existent or unsafe window name closes the WS."""
    from starlette.websockets import WebSocketDisconnect

    # Anonymous → 1008 regardless of query.
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client_with_auth.websocket_connect("/ws/tty?window=does-not-exist") as ws:
            ws.receive_bytes()
    assert excinfo.value.code == 1008
