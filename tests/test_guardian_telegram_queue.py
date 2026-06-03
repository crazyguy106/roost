"""Tests for the Telegram queue surface on Guardian drafts (Phase 1C).

Covers:
- `guardian_gate` invokes `_notify_telegram_about_draft` after parking a
  money-moving draft, with the draft id + tool name + reason.
- `_notify_telegram_about_draft` posts a sendMessage payload with the
  ✅ Approve / ❌ Reject inline keyboard.
- `handle_guardian_draft_callback` routes `gdraft:approve:<id>` to
  `approve_draft`, ack-ing the user. Audit-logged.
- `handle_guardian_draft_callback` routes `gdraft:reject:<id>` to
  `reject_draft`, ack-ing the user. Audit-logged.
- `/gdrafts` (cmd_gdrafts) lists pending drafts.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.bot.handlers.guardian_drafts import (
    cmd_gdrafts, handle_guardian_draft_callback,
)
from roost.services import guardian as guardian_svc


# ────────────────────────────────────────────────────────────────────
# Per-test cleanup
# ────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_drafts():
    from roost.database import get_connection
    conn = get_connection()
    try:
        conn.execute("DELETE FROM guardian_drafts")
        conn.commit()
    finally:
        conn.close()
    yield


# ────────────────────────────────────────────────────────────────────
# Sync httpx capture (matches guardian.py's sync httpx.Client use)
# ────────────────────────────────────────────────────────────────────


class _CaptureSyncClient:
    """Sync context-manager mimicking httpx.Client; records the last
    JSON payload posted via .post()."""

    captured: dict | None = None

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None):
        _CaptureSyncClient.captured = json
        return SimpleNamespace(status_code=200)


@pytest.fixture
def patch_httpx(monkeypatch):
    import httpx
    monkeypatch.setattr(httpx, "Client", _CaptureSyncClient)
    _CaptureSyncClient.captured = None


# ────────────────────────────────────────────────────────────────────
# guardian_gate → _notify_telegram_about_draft
# ────────────────────────────────────────────────────────────────────


def test_guardian_gate_calls_notify_helper(monkeypatch):
    """When a money-moving tool is gated, the Telegram notify helper
    must be invoked with the new draft id."""
    captured = {}

    def fake_notify(draft_id, tool_name, tool_args, reason):
        captured["draft_id"] = draft_id
        captured["tool_name"] = tool_name
        captured["reason"] = reason

    monkeypatch.setattr(
        guardian_svc, "_notify_telegram_about_draft", fake_notify,
    )
    out = guardian_svc.guardian_gate(
        "stripe_create_refund", {"charge_id": "ch_1", "amount": 5.0},
    )
    assert out is not None
    assert out["status"] == "pending_approval"
    assert captured["draft_id"] == out["draft_id"]
    assert captured["tool_name"] == "stripe_create_refund"
    assert "money-moving" in captured["reason"].lower()


def test_guardian_gate_does_not_notify_for_safe_tools(monkeypatch):
    called = []

    def fake_notify(*a, **kw):
        called.append(1)

    monkeypatch.setattr(
        guardian_svc, "_notify_telegram_about_draft", fake_notify,
    )
    out = guardian_svc.guardian_gate("get_task", {"task_id": 1})
    assert out is None
    assert called == []


def test_notify_helper_attaches_approve_reject_keyboard(
    patch_httpx, monkeypatch,
):
    monkeypatch.setattr("roost.config.TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr("roost.config.TELEGRAM_ALLOWED_USERS", [42])

    guardian_svc._notify_telegram_about_draft(
        draft_id=123,
        tool_name="stripe_create_refund",
        tool_args={"charge_id": "ch_x", "amount": 10.0},
        reason="money-moving write",
    )
    payload = _CaptureSyncClient.captured
    assert payload is not None
    assert payload["chat_id"] == 42
    assert "#123" in payload["text"]
    assert "stripe_create_refund" in payload["text"]
    rm = payload["reply_markup"]
    callbacks = [b["callback_data"] for b in rm["inline_keyboard"][0]]
    assert callbacks == ["gdraft:approve:123", "gdraft:reject:123"]


def test_notify_helper_no_op_without_token(patch_httpx, monkeypatch):
    monkeypatch.setattr("roost.config.TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr("roost.config.TELEGRAM_ALLOWED_USERS", [])
    guardian_svc._notify_telegram_about_draft(
        draft_id=1, tool_name="x", tool_args={}, reason="y",
    )
    assert _CaptureSyncClient.captured is None


# ────────────────────────────────────────────────────────────────────
# Callback handler
# ────────────────────────────────────────────────────────────────────


def _make_cb_update(data: str, user_id: int = 42):
    query = SimpleNamespace(
        data=data,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query)


def _make_ctx():
    return SimpleNamespace(chat_data={})


def _make_draft() -> int:
    return guardian_svc.create_draft(
        "stripe_create_refund",
        {"charge_id": "ch_1", "amount": 7.0},
        user_id="",
        rule_name="money_movement_draft",
    )


@pytest.mark.asyncio
async def test_callback_approve_dispatches_service(monkeypatch):
    draft_id = _make_draft()

    called = {}

    def fake_approve(did):
        called["draft_id"] = did
        return {"ok": True, "status": "executed",
                "draft_id": did, "result": {}}

    monkeypatch.setattr(guardian_svc, "approve_draft", fake_approve)

    upd = _make_cb_update(f"gdraft:approve:{draft_id}")
    ctx = _make_ctx()
    await handle_guardian_draft_callback(upd, ctx)

    assert called["draft_id"] == draft_id
    upd.callback_query.answer.assert_awaited_once()
    assert "Approved" in upd.callback_query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_callback_approve_failure_alerts(monkeypatch):
    draft_id = _make_draft()

    def fake_approve(did):
        return {"ok": False, "status": "unknown_tool", "draft_id": did}

    monkeypatch.setattr(guardian_svc, "approve_draft", fake_approve)

    upd = _make_cb_update(f"gdraft:approve:{draft_id}")
    await handle_guardian_draft_callback(upd, _make_ctx())

    upd.callback_query.answer.assert_awaited_once()
    assert upd.callback_query.answer.await_args.kwargs.get("show_alert") is True


@pytest.mark.asyncio
async def test_callback_reject_dispatches_service(monkeypatch):
    draft_id = _make_draft()

    called = {}

    def fake_reject(did, reason=""):
        called["draft_id"] = did
        called["reason"] = reason
        return {"ok": True, "status": "rejected", "draft_id": did}

    monkeypatch.setattr(guardian_svc, "reject_draft", fake_reject)

    upd = _make_cb_update(f"gdraft:reject:{draft_id}")
    await handle_guardian_draft_callback(upd, _make_ctx())

    assert called["draft_id"] == draft_id
    assert "tg:42" in called["reason"]
    upd.callback_query.answer.assert_awaited_once()
    assert "Rejected" in upd.callback_query.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_callback_invalid_data_silently_acks():
    upd = _make_cb_update("gdraft:approve:not-a-number")
    await handle_guardian_draft_callback(upd, _make_ctx())
    upd.callback_query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_audit_logs_approve(monkeypatch):
    draft_id = _make_draft()
    logged = []

    def fake_log(actor, action, **kw):
        logged.append((actor, action, kw))

    monkeypatch.setattr(
        "roost.bot.handlers.guardian_drafts.activity.log_action", fake_log,
    )
    monkeypatch.setattr(
        guardian_svc, "approve_draft",
        lambda did: {"ok": True, "status": "executed",
                     "draft_id": did, "result": {}},
    )

    upd = _make_cb_update(f"gdraft:approve:{draft_id}")
    await handle_guardian_draft_callback(upd, _make_ctx())

    assert len(logged) == 1
    actor, action, kw = logged[0]
    assert actor == "telegram"
    assert action == "guardian.approve"
    assert kw["entity_type"] == "guardian_draft"
    assert kw["entity_id"] == draft_id
    assert kw["ok"] is True
    assert kw["actor_ref"] == "tg:42"


@pytest.mark.asyncio
async def test_callback_audit_logs_reject(monkeypatch):
    draft_id = _make_draft()
    logged = []

    def fake_log(actor, action, **kw):
        logged.append((actor, action, kw))

    monkeypatch.setattr(
        "roost.bot.handlers.guardian_drafts.activity.log_action", fake_log,
    )
    monkeypatch.setattr(
        guardian_svc, "reject_draft",
        lambda did, reason="": {"ok": True, "status": "rejected",
                                "draft_id": did},
    )

    upd = _make_cb_update(f"gdraft:reject:{draft_id}")
    await handle_guardian_draft_callback(upd, _make_ctx())

    assert len(logged) == 1
    actor, action, kw = logged[0]
    assert actor == "telegram"
    assert action == "guardian.reject"
    assert kw["entity_type"] == "guardian_draft"
    assert kw["entity_id"] == draft_id


# ────────────────────────────────────────────────────────────────────
# /gdrafts command
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_gdrafts_empty():
    msg = SimpleNamespace(reply_text=AsyncMock())
    upd = SimpleNamespace(message=msg)
    await cmd_gdrafts(upd, _make_ctx())
    msg.reply_text.assert_awaited_once()
    assert "No pending" in msg.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_cmd_gdrafts_lists_pending():
    did1 = guardian_svc.create_draft(
        "stripe_create_refund", {"charge_id": "ch_1"}, user_id="",
        rule_name="money_movement_draft",
    )
    did2 = guardian_svc.create_draft(
        "shopify_cancel_order", {"order_id": 99}, user_id="",
        rule_name="money_movement_draft",
    )
    msg = SimpleNamespace(reply_text=AsyncMock())
    upd = SimpleNamespace(message=msg)
    await cmd_gdrafts(upd, _make_ctx())
    text = msg.reply_text.await_args.args[0]
    assert f"#{did1}" in text
    assert f"#{did2}" in text
    assert "stripe_create_refund" in text
    assert "shopify_cancel_order" in text
