"""Reverse proxy for the chromium sidecar's devtools UI.

Mounts at `/sidecar/*` so a Roost user (especially on a cloud deploy)
can open the live browser session in their own browser without setting
up nginx, SSH tunnels, or a separate domain. Auth-gated by the same
session cookie that protects the rest of the app — anyone who can
reach `/sidecar` is already a logged-in Roost user.

Two route classes:
  - HTTP wildcard at `/sidecar/{path:path}` for the devtools UI assets
    (HTML, JS, CSS, JSON discovery).
  - WebSocket wildcard at `/sidecar/devtools/page/{target_id}` and
    `/sidecar/devtools/browser/{target_id}` to bridge the CDP stream.

The devtools front-end constructs `ws://` or `wss://` based on the
page scheme, so emitting `ws=<host>/sidecar/devtools/page/<id>` (no
scheme) in `live_debug_url` works under both http and https.
"""

from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response

from roost.config import SIDECAR_INTERNAL_HTTP_URL

logger = logging.getLogger("roost.extras.rpa.web.api_sidecar")

router = APIRouter()

# Hop-by-hop headers that must not be forwarded across a proxy (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}


def _internal_http_base() -> str:
    return SIDECAR_INTERNAL_HTTP_URL.rstrip("/")


def _internal_ws_base() -> str:
    parts = urlsplit(SIDECAR_INTERNAL_HTTP_URL)
    scheme = "wss" if parts.scheme == "https" else "ws"
    return f"{scheme}://{parts.netloc}"


@router.api_route("/sidecar/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
async def proxy_http(path: str, request: Request) -> Response:
    """Forward HTTP requests to the chromium sidecar."""
    # Auth is enforced upstream by UnifiedAuthMiddleware — by the time
    # we get here, the user has a valid session or basic-auth credential.
    upstream = f"{_internal_http_base()}/{path}"
    if request.url.query:
        upstream = f"{upstream}?{request.url.query}"

    fwd_headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP and k.lower() != "host"
    }
    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            r = await client.request(
                request.method,
                upstream,
                content=body if body else None,
                headers=fwd_headers,
            )
    except httpx.RequestError as e:
        logger.warning("sidecar proxy HTTP failed: %s", e)
        return Response(content=b"sidecar unreachable", status_code=502)

    resp_headers = {
        k: v for k, v in r.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)


async def _ws_bridge(client_ws: WebSocket, upstream_path: str) -> None:
    """Bridge an authenticated client WebSocket to the sidecar's CDP socket."""
    user = None
    try:
        user = client_ws.session.get("user")
    except Exception:
        user = None
    if not user:
        await client_ws.close(code=4401)  # close before accept = 403-ish
        return

    upstream_url = f"{_internal_ws_base()}{upstream_path}"
    try:
        import websockets
    except ImportError:
        logger.error("websockets package missing — cannot proxy CDP")
        await client_ws.close(code=1011)
        return

    await client_ws.accept()
    try:
        async with websockets.connect(upstream_url, max_size=None,
                                       ping_interval=None) as upstream:
            async def c2u():
                try:
                    while True:
                        msg = await client_ws.receive()
                        if msg.get("type") == "websocket.disconnect":
                            return
                        if "text" in msg and msg["text"] is not None:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    return
                except Exception:
                    logger.debug("c2u bridge ended", exc_info=True)

            async def u2c():
                try:
                    async for msg in upstream:
                        if isinstance(msg, bytes):
                            await client_ws.send_bytes(msg)
                        else:
                            await client_ws.send_text(msg)
                except Exception:
                    logger.debug("u2c bridge ended", exc_info=True)

            await asyncio.gather(c2u(), u2c())
    except Exception as e:
        logger.warning("sidecar WS proxy failed: %s", e)
    finally:
        try:
            await client_ws.close()
        except Exception:
            pass


@router.websocket("/sidecar/devtools/page/{target_id}")
async def proxy_ws_page(ws: WebSocket, target_id: str):
    await _ws_bridge(ws, f"/devtools/page/{target_id}")


@router.websocket("/sidecar/devtools/browser/{target_id}")
async def proxy_ws_browser(ws: WebSocket, target_id: str):
    await _ws_bridge(ws, f"/devtools/browser/{target_id}")
