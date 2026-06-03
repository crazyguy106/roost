"""Web tty — xterm.js front-end bridged through a PTY to a per-user tmux session.

The WebSocket endpoint accepts:
- binary frames: raw keystrokes from the browser, written to the PTY master fd
- text frames (JSON): control messages, currently {"type": "resize", "rows": n, "cols": n}

The endpoint sends:
- binary frames: PTY output bytes (anything tmux/the inner shell writes)

Closing the browser tab detaches the tmux client; the tmux session (and anything
running inside it, including a `claude` interactive shell) survives so the next
visit reattaches with full scrollback.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import pty
import signal
import struct
import termios

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()
_logger = logging.getLogger("roost.web.tty")

_PTY_READ_CHUNK = 4096
_TMUX_SOCKET_NAME = "roost-tty"


def _tmux_session_for(user_id: int) -> str:
    return f"roost-{user_id}"


def _set_winsize(fd: int, rows: int, cols: int) -> None:
    rows = max(1, min(int(rows), 500))
    cols = max(1, min(int(cols), 500))
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


async def _ws_user(websocket: WebSocket) -> dict | None:
    """Resolve the operator's session.

    BaseHTTPMiddleware-based auth (UnifiedAuthMiddleware) does NOT run on
    WebSocket upgrades, but SessionMiddleware is pure-ASGI and does — so we
    re-check the session cookie here.
    """
    try:
        user = websocket.session.get("user")
    except Exception:
        user = None
    return user


@router.websocket("/ws/tty")
async def tty_ws(websocket: WebSocket) -> None:
    user = await _ws_user(websocket)
    if not user or not user.get("user_id"):
        await websocket.close(code=1008)
        return

    await websocket.accept()
    user_id = int(user.get("user_id") or 1)
    session = _tmux_session_for(user_id)

    master_fd, slave_fd = pty.openpty()
    try:
        _set_winsize(master_fd, 24, 80)
    except Exception:
        _logger.debug("Initial winsize set failed", exc_info=True)

    env = os.environ.copy()
    env.setdefault("TERM", "xterm-256color")
    env.setdefault("LANG", "C.UTF-8")

    # `tmux new-session -A -s <name>` attaches if the session exists, creates
    # it otherwise. The dedicated -L socket isolates these sessions from any
    # other tmux on the box (e.g. the operator's own shell).
    cmd = [
        "tmux", "-L", _TMUX_SOCKET_NAME,
        "new-session", "-A", "-s", session,
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

    loop = asyncio.get_event_loop()
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
                    # Optional text input fallback for clients that prefer JSON.
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
