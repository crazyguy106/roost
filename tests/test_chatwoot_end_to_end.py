"""End-to-end round-trip test for the Chatwoot adapter — FA edition.

The per-layer tests (`test_chatwoot_inbound.py`, `test_chatwoot_outbound.py`)
cover parser/signature/router and outbound dispatch in isolation. This file
exercises the full FA-edition loop in one test:

  1. Sign a real `message_created`/`incoming` envelope.
  2. POST it through the real FastAPI router with a real `CHATWOOT_ENABLED=True`.
  3. Assert lead ingest receives the right kwargs (channel="chatwoot", FA phone,
     FA message text).
  4. Call `whatsapp.send_text_message(...)` — which under `CHATWOOT_ENABLED` is
     supposed to delegate to `chatwoot.route_text_to_whatsapp`.
  5. Stub `httpx.Client` so we can verify the captured HTTP requests match the
     Chatwoot REST contract (correct base URL, `api_access_token` header,
     `/contacts/search` → `/contacts/{id}/conversations` → `/conversations`
     OR `/conversations/{id}/messages` depending on the open-conv branch).

This pins the contract end-to-end — signature, parser, router, outbound — so
a regression in any layer surfaces here even when the per-layer tests still
pass.

The scenario uses a financial-advisor inbound ("retirement planning") rather
than property, since the FA edition is built for advisers.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import time
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ─── fake httpx.Client ────────────────────────────────────────────────────


class _FakeResponse:
    """Minimal stand-in for httpx.Response — raise_for_status + json()."""

    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._body


class _FakeClient:
    """Captures every .get/.post call. Routes responses by URL substring so the
    real chatwoot service can do its real branching logic."""

    calls: list[dict] = []

    def __init__(self, timeout: int | float = 30):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url: str, *, headers: dict | None = None, params: dict | None = None):
        _FakeClient.calls.append(
            {"method": "GET", "url": url, "headers": headers or {}, "params": params or {}}
        )
        # /contacts/search — empty result so chatwoot falls through to create
        if "/contacts/search" in url:
            return _FakeResponse(200, {"payload": []})
        # /contacts/{id}/conversations — return ONE open conversation in inbox 2
        # so `route_text_to_whatsapp` takes the reuse-open-conv branch (the
        # common case after the inbound message has already been processed).
        if url.endswith("/conversations"):
            return _FakeResponse(
                200,
                {
                    "payload": [
                        {"id": 4242, "status": "open", "inbox_id": 2},
                    ]
                },
            )
        return _FakeResponse(404, {"error": "unrouted GET in fake httpx"})

    def post(self, url: str, *, headers: dict | None = None, json: Any = None):
        _FakeClient.calls.append(
            {"method": "POST", "url": url, "headers": headers or {}, "json": json}
        )
        # /conversations/{id}/messages — the outbound reply lands here.
        if "/messages" in url:
            return _FakeResponse(200, {"id": 9001})
        # /contacts (create) — shouldn't fire in this test because search
        # is stubbed to fall through to find a contact below.
        if url.endswith("/contacts"):
            return _FakeResponse(
                200,
                {
                    "payload": {
                        "contact": {"id": 77, "phone_number": "+6591234567"},
                        "contact_inbox": {"source_id": "+6591234567"},
                    }
                },
            )
        # /conversations — cold-start fallback (not expected to fire here).
        if url.endswith("/conversations"):
            return _FakeResponse(200, {"id": 4242})
        return _FakeResponse(404, {"error": "unrouted POST in fake httpx"})


def _wrap_search_with_hit():
    """Patch _FakeClient.get to return a contact match on /contacts/search so we
    skip the create branch (this is the common-case after the inbound message
    has already auto-created the contact server-side). Returns the new method."""
    real_get = _FakeClient.get

    def _get(self, url, *, headers=None, params=None):
        _FakeClient.calls.append(
            {"method": "GET", "url": url, "headers": headers or {}, "params": params or {}}
        )
        if "/contacts/search" in url:
            return _FakeResponse(
                200,
                {
                    "payload": [
                        {
                            "id": 77,
                            "phone_number": "+6591234567",
                            "contact_inboxes": [
                                {
                                    "inbox": {"id": 2},
                                    "source_id": "+6591234567",
                                }
                            ],
                        }
                    ]
                },
            )
        # delegate to the standard fake for everything else
        return real_get(self, url, headers=headers, params=params)

    _FakeClient.get = _get  # type: ignore[assignment]
    return real_get


# ─── the end-to-end test ───────────────────────────────────────────────────


def test_fa_round_trip_inbound_signed_post_then_outbound_routes_to_chatwoot(monkeypatch):
    """Sign an FA-context inbound, POST it through the real router, then send
    an outbound reply and assert it hits Chatwoot REST with the right shape."""

    # 1. Configure the FA edition env on the chatwoot service module.
    import roost.config as cfg
    monkeypatch.setattr(cfg, "CHATWOOT_ENABLED", True)

    import roost.extras.messaging_external.services.chatwoot as cw
    monkeypatch.setattr(cw, "CHATWOOT_ENABLED", True)
    monkeypatch.setattr(cw, "CHATWOOT_URL", "https://chatwoot.test")
    monkeypatch.setattr(cw, "CHATWOOT_API_KEY", "test-fa-token")
    monkeypatch.setattr(cw, "CHATWOOT_ACCOUNT_ID", "1")
    monkeypatch.setattr(cw, "CHATWOOT_INBOX_ID", "2")
    monkeypatch.setattr(cw, "CHATWOOT_WEBHOOK_SECRET", "fa-e2e-secret")

    # 2. Stub the cross-cutting heavy services so the inbound pipeline runs
    #    real router → real parse → real signature check → ingest at the
    #    boundary. ingest_lead itself is captured (matches the per-layer
    #    pattern; CRM bundle coverage lives in its own tests).
    captured_ingest: list[dict] = []
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kwargs: captured_ingest.append(kwargs) or {"id": 1, "is_new": True},
    )
    monkeypatch.setattr(
        "roost.services.recipes.list_recipes", lambda **kwargs: [],
    )

    async def _classify(message, sender):
        return {"intent": "info", "urgency": "warm"}

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.ai_cdr.classify_message",
        _classify,
    )
    # mark_as_read goes through httpx — short-circuit so we don't have to
    # route it in the fake client too.
    monkeypatch.setattr(cw, "mark_as_read", lambda cid: {"ok": True})

    # Inline buffer so the pipeline runs synchronously.
    async def _inline_submit(channel, sender, message, processor):
        await processor(message)

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.inbound_buffer.submit",
        _inline_submit,
    )

    # 3. Reload the router so it picks up CHATWOOT_ENABLED=True.
    import roost.extras.messaging_external.web.api_chatwoot as cwapi
    importlib.reload(cwapi)
    app = FastAPI()
    app.include_router(cwapi.router)
    client = TestClient(app)

    # 4. Build a financial-adviser inbound payload, signed correctly.
    payload = {
        "event": "message_created",
        "message_type": "incoming",
        "content": "hi i'd like to learn about retirement planning options",
        "content_type": "text",
        "account": {"id": 1, "name": "FA Sandbox"},
        "inbox": {"id": 2, "name": "WhatsApp Cloud"},
        "conversation": {
            "id": 555,
            "inbox_id": 2,
            "channel": "Channel::Whatsapp",
            "status": "open",
            "contact_inbox": {
                "contact_id": 77,
                "inbox_id": 2,
                "source_id": "+6591234567",
            },
            "messages": [],
            "meta": {
                "sender": {
                    "id": 77,
                    "name": "FA Lead",
                    "phone_number": "+6591234567",
                    "email": "",
                    "type": "contact",
                }
            },
        },
        "sender": {
            "id": 77,
            "name": "FA Lead",
            "phone_number": "+6591234567",
            "email": "",
            "type": "contact",
        },
    }
    body = json.dumps(payload).encode()
    ts = str(int(time.time()))
    sig = "sha256=" + hmac.new(
        b"fa-e2e-secret", f"{ts}.{body.decode()}".encode(), hashlib.sha256
    ).hexdigest()

    # 5. Hit the webhook.
    resp = client.post(
        "/api/chatwoot/webhook",
        content=body,
        headers={
            "X-Chatwoot-Timestamp": ts,
            "X-Chatwoot-Signature": sig,
            "X-Chatwoot-Delivery": "fa-e2e-delivery-uuid",
            "Content-Type": "application/json",
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    # Lead ingest fired with FA-context kwargs.
    assert len(captured_ingest) == 1
    ingest_kwargs = captured_ingest[0]
    assert ingest_kwargs["channel"] == "chatwoot"
    assert ingest_kwargs["phone"] == "+6591234567"
    assert ingest_kwargs["message_text"] == \
        "hi i'd like to learn about retirement planning options"

    # 6. Outbound reply — stub httpx.Client BEFORE the call, then dispatch.
    _FakeClient.calls = []
    _wrap_search_with_hit()
    monkeypatch.setattr(cw, "httpx", type("X", (), {
        "Client": _FakeClient,
        "HTTPStatusError": RuntimeError,
    }))

    from roost.extras.messaging_external.services import whatsapp
    result = whatsapp.send_text_message(
        "+6591234567",
        "Thanks for reaching out — happy to walk through retirement options.",
    )

    # 7. Verify the redirect happened.
    assert result["ok"] is True
    assert result["via"] == "chatwoot"
    assert result["conversation_id"] == 4242

    # 8. Verify the captured HTTP traffic matches the Chatwoot REST contract.
    methods = [(c["method"], c["url"]) for c in _FakeClient.calls]
    assert (
        "GET",
        "https://chatwoot.test/api/v1/accounts/1/contacts/search",
    ) in methods, methods
    assert (
        "GET",
        "https://chatwoot.test/api/v1/accounts/1/contacts/77/conversations",
    ) in methods, methods
    assert (
        "POST",
        "https://chatwoot.test/api/v1/accounts/1/conversations/4242/messages",
    ) in methods, methods

    # Auth header on every call.
    for c in _FakeClient.calls:
        assert c["headers"].get("api_access_token") == "test-fa-token", c

    # The POST body matches the outbound reply text.
    posts = [c for c in _FakeClient.calls if c["method"] == "POST"]
    assert len(posts) == 1
    assert posts[0]["json"]["content"] == \
        "Thanks for reaching out — happy to walk through retirement options."
    assert posts[0]["json"]["message_type"] == "outgoing"
    assert posts[0]["json"]["private"] is False
