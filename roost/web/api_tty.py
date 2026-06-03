"""Web tty — xterm.js bridged through a PTY to a per-user tmux session
that can hold multiple named windows (one per conversation).

Architecture
------------
- One tmux **session** per user: ``roost-<user_id>`` on socket ``-L roost-tty``.
- Many **windows** inside that session, each backed by a row in
  ``chat_windows`` (see ``roost.services.chat_windows``). The window's
  tmux name is ``chat_windows.tmux_window_name`` (e.g. ``w-3f1a8c``);
  its human-readable title is ``chat_windows.title``.
- The browser opens ``/ws/tty?window=<name>``; the WS handler ensures
  the tmux session + window exist, then attaches the PTY to that
  specific window via ``tmux attach … \\; select-window …``. Closing
  the WS detaches the tmux client — the window (and anything running
  in it) survives. The window is only destroyed by an explicit
  ``DELETE /api/tty/windows/{id}`` or by the idle / memory-pressure
  sweeper (see ``roost.services.tty_sweeper``).

REST surface
------------
- ``GET    /api/tty/windows``      — list this user's windows (newest active first)
- ``POST   /api/tty/windows``      — create a new window (title required); no tmux side-effect, lazy-created on first attach
- ``DELETE /api/tty/windows/{id}`` — kill the tmux window + delete the row (scoped to caller)

Auth
----
``UnifiedAuthMiddleware`` (BaseHTTPMiddleware) does NOT process WS
upgrades, so the WS handler re-checks ``websocket.session["user"]``.
The REST endpoints sit behind the normal HTTP auth path via
``request.state.current_user``.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import re
import signal
import struct
import subprocess
import termios

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from roost.services import chat_windows as cw

router = APIRouter()
_logger = logging.getLogger("roost.web.tty")

_PTY_READ_CHUNK = 4096
_TMUX_SOCKET_NAME = "roost-tty"

# Defence-in-depth: tmux window names are passed to subprocesses, so
# allow only an alphanumeric + dash + underscore set even though the
# values are normally service-generated.
_SAFE_WINDOW_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


# ── Helpers ──────────────────────────────────────────────────────────


def _tmux_session_for(user_id: int) -> str:
    return f"roost-{user_id}"


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(int(rows), 500))
    cols = max(1, min(int(cols), 500))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def _is_safe_window_name(name: str) -> bool:
    return bool(name and _SAFE_WINDOW_NAME_RE.match(name))


async def _ws_user(websocket: WebSocket) -> dict | None:
    try:
        return websocket.session.get("user")
    except Exception:
        return None


def _current_user(request: Request) -> dict | None:
    return getattr(request.state, "current_user", None)


# ── tmux session/window plumbing ─────────────────────────────────────


def _tmux(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    """Run a one-shot tmux command on the roost-tty socket."""
    return subprocess.run(
        ["tmux", "-L", _TMUX_SOCKET_NAME, *args],
        capture_output=True,
        text=True,
        check=check,
    )


def _ensure_session(user_id: int) -> None:
    """Create the per-user tmux session if missing (idempotent)."""
    session = _tmux_session_for(user_id)
    has = _tmux(["has-session", "-t", session])
    if has.returncode != 0:
        # Detached so we don't try to allocate a tty here — the WS
        # PTY-attached process is the one that needs the tty.
        _tmux(
            ["new-session", "-d", "-s", session,
             "-n", "main"],
            check=False,
        )


def _ensure_window(user_id: int, window_name: str) -> None:
    """Ensure the named window exists inside the user's session."""
    if not _is_safe_window_name(window_name):
        raise ValueError(f"unsafe tmux window name: {window_name!r}")
    _ensure_session(user_id)
    session = _tmux_session_for(user_id)
    listing = _tmux(["list-windows", "-t", session, "-F", "#W"])
    existing = set((listing.stdout or "").split())
    if window_name not in existing:
        _tmux(
            ["new-window", "-d", "-t", f"{session}:", "-n", window_name],
            check=False,
        )


def _kill_window(user_id: int, window_name: str) -> None:
    """Kill the named window. No-op if it doesn't exist."""
    if not _is_safe_window_name(window_name):
        return
    session = _tmux_session_for(user_id)
    _tmux(["kill-window", "-t", f"{session}:{window_name}"], check=False)


def _kill_window_for_sweeper(user_id: int, window_name: str) -> None:
    """Sweeper-facing tmux killer; same as `_kill_window`. Kept as a
    distinct symbol so the scheduler import doesn't reach into the
    leading-underscore private set."""
    _kill_window(user_id, window_name)


# ── REST: list / create / delete ─────────────────────────────────────


@router.get("/api/tty/windows")
async def list_windows_api(request: Request):
    user = _current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="unauthenticated")
    user_id = int(user["user_id"])
    windows = cw.list_windows(user_id)
    return {
        "windows": [_window_dict(w) for w in windows],
        "cap": cw.DEFAULT_WINDOW_CAP,
    }


# ── Picker: candidate entities to open a new window for ───────────────


def _picker_tasks(user_id: int, limit: int) -> list[dict]:
    """Recent in-progress tasks → picker entries.

    Soft-fails to [] on import / runtime errors so the picker is robust
    when this user has no tasks or the tasks subsystem misbehaves.
    """
    try:
        from roost.services import tasks as tasks_svc
        rows = tasks_svc.list_tasks(
            status="in_progress",
            user_id=user_id,
            order_by="updated",
            limit=limit,
        )
    except (ImportError, AttributeError, RuntimeError):
        _logger.warning("picker: tasks lookup failed", exc_info=True)
        return []
    items = []
    for t in rows:
        items.append({
            "kind": "task",
            "title": t.title,
            "linked_entity_type": "task",
            "linked_entity_id": t.id,
            "hint": "in-progress task",
        })
    return items


def _picker_chatwoot(limit: int) -> list[dict]:
    """Open Chatwoot conversations → picker entries.

    Returns [] when the bundle is off or the upstream call fails — the
    picker should never throw because one source is unavailable.
    """
    try:
        from roost.config import CHATWOOT_ENABLED
        if not CHATWOOT_ENABLED:
            return []
        from roost.extras.messaging_external.services import chatwoot
        result = chatwoot.list_open_conversations(limit=limit)
    except (ImportError, AttributeError, RuntimeError):
        _logger.warning("picker: chatwoot lookup failed", exc_info=True)
        return []
    if not isinstance(result, dict) or "conversations" not in result:
        return []
    items = []
    for c in result.get("conversations", []):
        items.append({
            "kind": "chatwoot",
            "title": c.get("contact") or f"Conversation {c.get('id')}",
            "linked_entity_type": "chatwoot_conversation",
            "linked_entity_id": c.get("id"),
            "hint": (c.get("preview") or "")[:80],
        })
    return items


@router.get("/api/tty/picker")
async def picker_api(request: Request):
    """Picker entries: top-N tasks + open Chatwoot conversations + blank.

    No search — the FA-edition design holds 5-8 visible candidates plus
    a "Blank window" escape hatch. The drawer (Phase 1.6d) handles the
    long-tail "resume an older window" path.
    """
    user = _current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="unauthenticated")
    user_id = int(user["user_id"])

    per_source = 4
    items: list[dict] = []
    items.extend(_picker_tasks(user_id, per_source))
    items.extend(_picker_chatwoot(per_source))
    # Always offer the escape hatch.
    items.append({
        "kind": "blank",
        "title": "Blank window",
        "linked_entity_type": "",
        "linked_entity_id": None,
        "hint": "Start with nothing pre-attached",
    })
    return {"items": items, "cap": cw.DEFAULT_WINDOW_CAP}


def _window_dict(w: cw.ChatWindow) -> dict:
    return {
        "id": w.id,
        "tmux_window_name": w.tmux_window_name,
        "title": w.title,
        "linked_entity_type": w.linked_entity_type,
        "linked_entity_id": w.linked_entity_id,
        "last_topic": w.last_topic,
        "last_active_at": w.last_active_at,
        "last_inbound_at": w.last_inbound_at,
        "created_at": w.created_at,
        "tmux_window_alive": w.tmux_window_alive,
        "last_resume_cmd": w.last_resume_cmd,
    }


@router.post("/api/tty/windows")
async def create_window_api(request: Request):
    user = _current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="unauthenticated")
    user_id = int(user["user_id"])

    body = await request.json() if request.headers.get("content-length") else {}
    title = (body.get("title") or "").strip() if isinstance(body, dict) else ""
    if not title:
        # Cheap default — pick "Untitled N" so the operator always has
        # something. The picker normally supplies a real title.
        n = cw.count_windows(user_id) + 1
        title = f"Untitled {n}"

    linked_type = (body.get("linked_entity_type") or "").strip() if isinstance(body, dict) else ""
    linked_id = body.get("linked_entity_id") if isinstance(body, dict) else None

    # 1.6c — cap enforcement. The check-and-insert lives behind a single
    # BEGIN IMMEDIATE so two concurrent POSTs can't both squeak past the
    # cap. At-cap returns 409 + an evictee recommendation so the UI can
    # show the "close one to continue" view.
    cap = cw.DEFAULT_WINDOW_CAP
    try:
        window = cw.create_window_if_under_cap(
            user_id,
            cap,
            title=title,
            linked_entity_type=linked_type,
            linked_entity_id=linked_id,
        )
    except cw.ChatWindowError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    if window is None:
        recommendation = cw.recommend_evictee(user_id, cap=cap)
        return JSONResponse(
            status_code=409,
            content={
                "error": "at_cap",
                "cap": cap,
                "evictee_recommendation": _window_dict(recommendation) if recommendation else None,
            },
        )

    return _window_dict(window)


@router.get("/api/tty/windows/paused")
async def list_paused_windows_api(request: Request):
    """Paused windows (tmux killed, row kept) — feeds the resume drawer."""
    user = _current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="unauthenticated")
    user_id = int(user["user_id"])
    rows = cw.list_paused_windows(user_id, limit=15)
    return {"windows": [_window_dict(w) for w in rows]}


@router.post("/api/tty/windows/{window_id}/resume")
async def resume_window_api(window_id: int, request: Request):
    """Mark intent to resume a paused window. The alive flip + tmux
    window re-creation happen lazily when the browser opens
    ``/ws/tty?window=<name>`` — see ``tty_ws`` below. We just validate
    ownership and hand back the row so the UI can navigate."""
    user = _current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="unauthenticated")
    user_id = int(user["user_id"])
    window = cw.get_window(window_id)
    if not window or window.user_id != user_id:
        raise HTTPException(status_code=404, detail="window not found")
    return _window_dict(window)


@router.delete("/api/tty/windows/{window_id}")
async def delete_window_api(window_id: int, request: Request):
    user = _current_user(request)
    if not user or not user.get("user_id"):
        raise HTTPException(status_code=401, detail="unauthenticated")
    user_id = int(user["user_id"])

    window = cw.get_window(window_id)
    if not window or window.user_id != user_id:
        raise HTTPException(status_code=404, detail="window not found")

    _kill_window(user_id, window.tmux_window_name)
    cw.delete_window(window_id)
    return {"ok": True, "id": window_id}


# ── WebSocket: PTY bridge to a specific window ───────────────────────


@router.websocket("/ws/tty")
async def tty_ws(websocket: WebSocket) -> None:
    user = await _ws_user(websocket)
    if not user or not user.get("user_id"):
        await websocket.close(code=1008)
        return

    user_id = int(user["user_id"])
    session = _tmux_session_for(user_id)

    # Resolve which window to attach to.
    qp_name = websocket.query_params.get("window") or ""
    qp_name = qp_name.strip()
    window_row = None

    if qp_name:
        if not _is_safe_window_name(qp_name):
            await websocket.close(code=1008)
            return
        window_row = cw.get_window_by_tmux_name(user_id, qp_name)
        if window_row is None:
            await websocket.close(code=1008)
            return
    else:
        # No window specified: most-recently-active, or auto-create a
        # "main" window so the operator always has somewhere to land.
        existing = cw.list_windows(user_id)
        if existing:
            window_row = existing[0]
        else:
            try:
                window_row = cw.auto_create(user_id, title="main")
            except cw.ChatWindowError:
                await websocket.close(code=1011)
                return

    await websocket.accept()

    # Ensure tmux server-side state matches the DB.
    try:
        _ensure_window(user_id, window_row.tmux_window_name)
    except FileNotFoundError:
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "tmux is not installed inside the container.",
        }))
        await websocket.close()
        return
    except Exception:
        _logger.exception("ensure_window failed")
        await websocket.close(code=1011)
        return

    # 1.6d: a successful attach implies the tmux window exists again.
    cw.mark_window_alive(window_row.id)
    cw.touch_active(window_row.id)

    master_fd, slave_fd = pty.openpty()
    try:
        _set_winsize(master_fd, 24, 80)
    except Exception:
        _logger.debug("Initial winsize set failed", exc_info=True)

    env = os.environ.copy()
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("LANG", "C.UTF-8")

    # Build the attach command. `;` here is a tmux command separator,
    # not a shell separator — it must be its own argv element.
    target = f"{session}:{window_row.tmux_window_name}"
    cmd = [
        "tmux", "-L", _TMUX_SOCKET_NAME,
        "attach-session", "-t", session,
        ";",
        "select-window", "-t", target,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            start_new_session=True,
        )
    except FileNotFoundError:
        os.close(master_fd)
        os.close(slave_fd)
        await websocket.send_text(json.dumps({
            "type": "error",
            "message": "tmux is not installed inside the container.",
        }))
        await websocket.close()
        return
    finally:
        try:
            os.close(slave_fd)
        except OSError:
            pass

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    async def pump_pty_to_ws() -> None:
        while not stop.is_set():
            try:
                data = await loop.run_in_executor(
                    None, lambda: os.read(master_fd, _PTY_READ_CHUNK)
                )
            except OSError:
                break
            if not data:
                break
            try:
                await websocket.send_bytes(data)
            except Exception:
                break
        stop.set()

    async def pump_ws_to_pty() -> None:
        while not stop.is_set():
            try:
                msg = await websocket.receive()
            except WebSocketDisconnect:
                break
            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                break
            if mtype != "websocket.receive":
                continue
            if "bytes" in msg and msg["bytes"] is not None:
                try:
                    os.write(master_fd, msg["bytes"])
                except OSError:
                    break
            elif "text" in msg and msg["text"]:
                try:
                    payload = json.loads(msg["text"])
                except (TypeError, ValueError):
                    continue
                if payload.get("type") == "resize":
                    try:
                        _set_winsize(
                            master_fd,
                            payload.get("rows", 24),
                            payload.get("cols", 80),
                        )
                    except Exception:
                        _logger.debug("Resize failed", exc_info=True)
                elif payload.get("type") == "input":
                    s = payload.get("data", "")
                    if s:
                        try:
                            os.write(master_fd, s.encode("utf-8"))
                        except OSError:
                            break
        stop.set()

    reader_task = asyncio.create_task(pump_pty_to_ws())
    writer_task = asyncio.create_task(pump_ws_to_pty())

    try:
        await stop.wait()
    finally:
        for task in (reader_task, writer_task):
            if not task.done():
                task.cancel()
        if proc.returncode is None:
            try:
                proc.send_signal(signal.SIGHUP)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
