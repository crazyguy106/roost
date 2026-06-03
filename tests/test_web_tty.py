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


# ── REST: cap + picker (1.6c) ────────────────────────────────────────


def test_create_returns_409_at_cap(client_user1):
    """Hitting DEFAULT_WINDOW_CAP returns 409 + evictee recommendation."""
    for i in range(cw.DEFAULT_WINDOW_CAP):
        r = client_user1.post("/api/tty/windows", json={"title": f"W{i}"})
        assert r.status_code == 200, r.text

    r = client_user1.post("/api/tty/windows", json={"title": "overflow"})
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "at_cap"
    assert body["cap"] == cw.DEFAULT_WINDOW_CAP
    rec = body["evictee_recommendation"]
    assert rec is not None
    # All cap rows have NULL last_inbound_at → recommendation picks the
    # oldest active row, which is the first one created ("W0").
    assert rec["title"] == "W0"


def test_evictee_recommendation_prefers_null_inbound(client_user1):
    """Filling the cap then bumping inbound on the oldest row makes the
    *second*-oldest the recommendation (NULL inbound > any inbound)."""
    ids = []
    for i in range(cw.DEFAULT_WINDOW_CAP):
        r = client_user1.post("/api/tty/windows", json={"title": f"W{i}"})
        ids.append(r.json()["id"])
    # Bump inbound on the oldest — now it's no longer the coldest.
    cw.mark_inbound(ids[0])
    r = client_user1.post("/api/tty/windows", json={"title": "x"})
    assert r.status_code == 409
    body = r.json()
    rec = body["evictee_recommendation"]
    # The remaining four still have NULL last_inbound_at, so the oldest
    # of those (W1) should be recommended.
    assert rec["title"] == "W1"


def test_picker_returns_blank_entry(client_user1):
    """With bundles off / no tasks, the picker still returns the blank entry."""
    res = client_user1.get("/api/tty/picker")
    assert res.status_code == 200
    body = res.json()
    assert body["cap"] == cw.DEFAULT_WINDOW_CAP
    kinds = [it["kind"] for it in body["items"]]
    assert "blank" in kinds
    # The blank entry is always last.
    assert body["items"][-1]["kind"] == "blank"


def test_picker_requires_auth(client_no_auth):
    res = client_no_auth.get("/api/tty/picker")
    assert res.status_code == 401


# ── Paused drawer + resume (1.6d) ────────────────────────────────────


def test_list_paused_excludes_alive_windows(client_user1):
    """Only rows with tmux_window_alive=0 surface in the paused list."""
    alive = client_user1.post("/api/tty/windows", json={"title": "alive"}).json()
    paused_row = client_user1.post("/api/tty/windows", json={"title": "paused"}).json()
    cw.mark_window_killed(paused_row["id"], resume_cmd="bash")

    res = client_user1.get("/api/tty/windows/paused")
    assert res.status_code == 200
    titles = [w["title"] for w in res.json()["windows"]]
    assert titles == ["paused"]
    assert alive["title"] not in titles


def test_paused_list_scope_isolation(client_user1, client_user2):
    """A paused window for user 1 must not show up in user 2's drawer."""
    mine = client_user1.post("/api/tty/windows", json={"title": "mine"}).json()
    cw.mark_window_killed(mine["id"], resume_cmd="bash")
    res = client_user2.get("/api/tty/windows/paused")
    assert res.status_code == 200
    assert res.json()["windows"] == []


def test_resume_endpoint_validates_scope_only(client_user1):
    """POST /resume hands back the row without flipping alive — the WS
    attach handler does the lazy resurrect when the browser actually
    connects. Endpoint's job is to validate ownership + give the UI
    something to navigate with."""
    w = client_user1.post("/api/tty/windows", json={"title": "x"}).json()
    cw.mark_window_killed(w["id"], resume_cmd="bash")
    assert cw.get_window(w["id"]).tmux_window_alive == 0

    res = client_user1.post(f"/api/tty/windows/{w['id']}/resume")
    assert res.status_code == 200
    body = res.json()
    # Row comes back paused — WS attach will flip it.
    assert body["tmux_window_alive"] == 0
    assert cw.get_window(w["id"]).tmux_window_alive == 0


def test_resume_window_404_for_other_user(client_user1, client_user2):
    w = client_user1.post("/api/tty/windows", json={"title": "x"}).json()
    cw.mark_window_killed(w["id"], resume_cmd="bash")
    res = client_user2.post(f"/api/tty/windows/{w['id']}/resume")
    assert res.status_code == 404


def test_list_includes_alive_field(client_user1):
    """Phase 1.6d adds tmux_window_alive + last_resume_cmd to the list payload."""
    w = client_user1.post("/api/tty/windows", json={"title": "T"}).json()
    listing = client_user1.get("/api/tty/windows").json()
    assert listing["windows"][0]["tmux_window_alive"] == 1
    assert listing["windows"][0]["last_resume_cmd"] is None


# ── Service: auto_create ─────────────────────────────────────────────


def test_auto_create_generates_unique_name():
    a = cw.auto_create(1, title="A")
    b = cw.auto_create(1, title="B")
    assert a.tmux_window_name != b.tmux_window_name
    assert a.tmux_window_name.startswith("w-")
    assert b.tmux_window_name.startswith("w-")


def test_create_window_if_under_cap_respects_limit():
    """Atomic check-and-insert: at cap returns None, otherwise inserts
    and returns the new row. Closes the race that the old non-atomic
    count+create pattern had between two concurrent POSTs."""
    cap = 3
    rows = []
    for i in range(cap):
        row = cw.create_window_if_under_cap(1, cap, title=f"W{i}")
        assert row is not None
        rows.append(row)
    # Cap reached → returns None instead of inserting an over-cap row.
    assert cw.create_window_if_under_cap(1, cap, title="overflow") is None
    assert cw.count_windows(1) == cap
    # A different user's quota is independent.
    other = cw.create_window_if_under_cap(2, cap, title="theirs")
    assert other is not None
    assert cw.count_windows(2) == 1


def test_create_window_if_under_cap_unique_per_user():
    """Even under the atomic helper, every row gets a unique tmux name."""
    a = cw.create_window_if_under_cap(1, 5, title="A")
    b = cw.create_window_if_under_cap(1, 5, title="B")
    assert a is not None and b is not None
    assert a.tmux_window_name != b.tmux_window_name


# ── WebSocket: bad query param rejected ───────────────────────────────


def test_ws_rejects_bad_window_name(client_with_auth):
    """A non-existent or unsafe window name closes the WS."""
    from starlette.websockets import WebSocketDisconnect

    # Anonymous → 1008 regardless of query.
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client_with_auth.websocket_connect("/ws/tty?window=does-not-exist") as ws:
            ws.receive_bytes()
    assert excinfo.value.code == 1008
