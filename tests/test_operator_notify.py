"""Operator Telegram heads-ups for gated inbound messages."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from roost.extras.messaging_external.services import operator_notify as on


class _CaptureClient:
    posts: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def post(self, url, json=None):
        _CaptureClient.posts.append(json)
        return SimpleNamespace(status_code=200)


@pytest.fixture
def cap(monkeypatch):
    monkeypatch.setattr("roost.config.TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr("roost.config.TELEGRAM_ALLOWED_USERS", [42])
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _CaptureClient)
    _CaptureClient.posts = []
    return _CaptureClient


@pytest.mark.asyncio
async def test_notify_gated_dormant(cap):
    await on.notify_gated(
        channel="chatwoot", who="Ethan Seow +6597531358", text="hello again",
        gate={"proceed": False, "reason": "dormant", "dormant_hours": 30.0},
    )
    assert len(cap.posts) == 1
    p = cap.posts[0]
    assert p["chat_id"] == 42
    assert "resurfaced after 30.0h" in p["text"]
    assert "Ethan Seow" in p["text"]
    assert "hello again" in p["text"]


@pytest.mark.asyncio
async def test_notify_gated_paused(cap):
    await on.notify_gated(
        channel="whatsapp", who="+6591234567", text="hi",
        gate={"proceed": False, "reason": "paused"},
    )
    assert len(cap.posts) == 1
    assert "Automations paused" in cap.posts[0]["text"]


@pytest.mark.asyncio
async def test_notify_gated_ok_is_noop(cap):
    await on.notify_gated(channel="chatwoot", who="x", text="hi",
                          gate={"reason": "ok"})
    assert cap.posts == []


@pytest.mark.asyncio
async def test_notify_operator_noop_without_config(monkeypatch):
    monkeypatch.setattr("roost.config.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("roost.config.TELEGRAM_ALLOWED_USERS", [])
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _CaptureClient)
    _CaptureClient.posts = []
    await on.notify_operator("test")
    assert _CaptureClient.posts == []
