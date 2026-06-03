"""Tests for the FA-edition recipe-draft Edit flow.

Covers:
- `apply_run_draft_edit` writes `draft_output` on an awaiting-approval run;
  refuses if the run is missing or in a different status.
- `handle_recipe_callback` routes `recipe:edit:<run_id>` — sends a
  force-reply prompt seeded with `draft_output`, stashes `redit_run_id`
  in `chat_data`. Audit-logs the prompt.
- `recipe:edit` rejects runs that aren't `awaiting_approval`.
- `handle_recipe_edit_reply` is a no-op without the chat_data marker.
- `handle_recipe_edit_reply` applies the edit, re-shows the
  Approve/Edit/Skip keyboard, and clears the marker.
- `_notify_telegram` (WhatsApp + Chatwoot + WeChat) attaches an
  `inline_keyboard` reply_markup when `status == 'awaiting_approval'`,
  and omits it for classify-only / completed runs.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.bot.handlers.recipes import (
    handle_recipe_callback, handle_recipe_edit_reply,
)
from roost.database import get_connection
from roost.services import recipes as recipes_svc


# ────────────────────────────────────────────────────────────────────
# Per-test cleanup
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_runs():
    conn = get_connection()
    conn.execute("DELETE FROM automation_runs")
    conn.execute("DELETE FROM automation_recipes")
    conn.commit()
    conn.close()
    yield
    conn = get_connection()
    conn.execute("DELETE FROM automation_runs")
    conn.execute("DELETE FROM automation_recipes")
    conn.commit()
    conn.close()


@pytest.fixture
def held_run(clean_runs):
    """Create a recipe + run paused with `status='awaiting_approval'`."""
    recipe = recipes_svc.create_recipe(
        name="Test recipe",
        instructions="reply politely",
        risk_tier="external_write",
    )
    run = recipes_svc.create_run(recipe["id"], trigger_data={})
    recipes_svc.update_run(
        run["id"], status="awaiting_approval", draft_output="ORIGINAL DRAFT",
    )
    return recipes_svc.get_run(run["id"])


# ────────────────────────────────────────────────────────────────────
# apply_run_draft_edit
# ────────────────────────────────────────────────────────────────────


def test_apply_run_draft_edit_persists(held_run):
    res = recipes_svc.apply_run_draft_edit(held_run["id"], "Rewritten draft.")
    assert res["ok"] is True
    after = recipes_svc.get_run(held_run["id"])
    assert after["draft_output"] == "Rewritten draft."


def test_apply_run_draft_edit_refuses_missing(clean_runs):
    res = recipes_svc.apply_run_draft_edit(99_999, "nope")
    assert res["ok"] is False
    assert "not found" in res["error"]


def test_apply_run_draft_edit_refuses_completed(clean_runs):
    recipe = recipes_svc.create_recipe(
        name="x", instructions="i", risk_tier="external_write",
    )
    run = recipes_svc.create_run(recipe["id"])
    recipes_svc.update_run(run["id"], status="completed", draft_output="done")
    res = recipes_svc.apply_run_draft_edit(run["id"], "edit")
    assert res["ok"] is False
    assert "not awaiting approval" in res["error"]


# ────────────────────────────────────────────────────────────────────
# Callback: recipe:edit
# ────────────────────────────────────────────────────────────────────


def _make_cb_update(data: str, user_id: int = 42):
    msg = SimpleNamespace(chat_id=99)
    query = SimpleNamespace(
        data=data,
        message=msg,
        from_user=SimpleNamespace(id=user_id),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query, effective_user=None)


def _make_ctx():
    return SimpleNamespace(
        chat_data={},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_callback_recipe_edit_prompts_for_reply(held_run):
    upd = _make_cb_update(f"recipe:edit:{held_run['id']}")
    ctx = _make_ctx()
    await handle_recipe_callback(upd, ctx)
    upd.callback_query.answer.assert_awaited_with("Reply with the edited draft")
    ctx.bot.send_message.assert_awaited_once()
    kw = ctx.bot.send_message.await_args.kwargs
    assert kw["chat_id"] == 99
    assert f"#{held_run['id']}" in kw["text"]
    # Original draft seeded into the prompt
    assert "ORIGINAL DRAFT" in kw["text"]
    # ForceReply markup attached
    assert kw.get("reply_markup") is not None
    # chat_data marker so the reply handler picks it up
    assert ctx.chat_data.get("redit_run_id") == held_run["id"]


@pytest.mark.asyncio
async def test_callback_recipe_edit_rejects_completed(clean_runs):
    recipe = recipes_svc.create_recipe(
        name="x", instructions="i", risk_tier="external_write",
    )
    run = recipes_svc.create_run(recipe["id"])
    recipes_svc.update_run(run["id"], status="completed", draft_output="d")

    upd = _make_cb_update(f"recipe:edit:{run['id']}")
    ctx = _make_ctx()
    await handle_recipe_callback(upd, ctx)
    upd.callback_query.answer.assert_awaited_once()
    assert upd.callback_query.answer.await_args.kwargs.get("show_alert") is True
    assert ctx.chat_data.get("redit_run_id") is None


# ────────────────────────────────────────────────────────────────────
# handle_recipe_edit_reply
# ────────────────────────────────────────────────────────────────────


def _make_reply_update(text: str, user_id: int = 42, chat_id: int = 99):
    msg = SimpleNamespace(
        text=text,
        chat_id=chat_id,
        from_user=SimpleNamespace(id=user_id),
        reply_text=AsyncMock(),
    )
    return SimpleNamespace(message=msg, effective_user=None)


@pytest.mark.asyncio
async def test_edit_reply_ignored_without_marker():
    upd = _make_reply_update("hello")
    ctx = _make_ctx()
    handled = await handle_recipe_edit_reply(upd, ctx)
    assert handled is False


@pytest.mark.asyncio
async def test_edit_reply_applies_and_reshows_keyboard(held_run):
    upd = _make_reply_update("Operator's revised draft.")
    ctx = _make_ctx()
    ctx.chat_data["redit_run_id"] = held_run["id"]

    handled = await handle_recipe_edit_reply(upd, ctx)
    assert handled is True
    assert "redit_run_id" not in ctx.chat_data

    after = recipes_svc.get_run(held_run["id"])
    assert after["draft_output"] == "Operator's revised draft."

    ctx.bot.send_message.assert_awaited_once()
    kw = ctx.bot.send_message.await_args.kwargs
    rm = kw["reply_markup"]
    # InlineKeyboardMarkup object — pull the buttons row out
    row = rm.inline_keyboard[0]
    callbacks = [b.callback_data for b in row]
    assert callbacks == [
        f"recipe:approve:{held_run['id']}",
        f"recipe:edit:{held_run['id']}",
        f"recipe:skip:{held_run['id']}",
    ]


@pytest.mark.asyncio
async def test_edit_reply_empty_keeps_previous_draft(held_run):
    upd = _make_reply_update("   ")
    ctx = _make_ctx()
    ctx.chat_data["redit_run_id"] = held_run["id"]
    handled = await handle_recipe_edit_reply(upd, ctx)
    assert handled is True
    upd.message.reply_text.assert_awaited_once()
    assert "Empty" in upd.message.reply_text.await_args.args[0]
    # marker cleared, draft unchanged
    assert "redit_run_id" not in ctx.chat_data
    after = recipes_svc.get_run(held_run["id"])
    assert after["draft_output"] == "ORIGINAL DRAFT"


# ────────────────────────────────────────────────────────────────────
# _notify_telegram attaches inline keyboard when awaiting_approval
# ────────────────────────────────────────────────────────────────────


class _CaptureClient:
    """Async context manager mimicking httpx.AsyncClient — records the
    most recent JSON payload sent via .post()."""

    captured: dict | None = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None):
        _CaptureClient.captured = json
        return SimpleNamespace(status_code=200)


@pytest.fixture
def patch_telegram(monkeypatch):
    """Patch TELEGRAM_BOT_TOKEN + allowed users + httpx in all three
    _notify_telegram surfaces."""
    monkeypatch.setattr("roost.config.TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr("roost.config.TELEGRAM_ALLOWED_USERS", {42})
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", _CaptureClient)
    _CaptureClient.captured = None


@pytest.mark.asyncio
async def test_whatsapp_notify_attaches_keyboard_when_held(patch_telegram):
    from roost.extras.messaging_external.web.api_whatsapp import _notify_telegram

    msg = {"sender_name": "Alice", "text": "hi"}
    await _notify_telegram(msg, {
        "classification": {"intent": "buyer_lead", "urgency": "warm"},
        "draft": "Hello back!",
        "status": "awaiting_approval",
        "run_id": 7,
    })
    payload = _CaptureClient.captured
    assert payload is not None
    rm = payload.get("reply_markup")
    assert rm is not None
    callbacks = [b["callback_data"] for b in rm["inline_keyboard"][0]]
    assert callbacks == ["recipe:approve:7", "recipe:edit:7", "recipe:skip:7"]


@pytest.mark.asyncio
async def test_whatsapp_notify_no_keyboard_when_classified_only(patch_telegram):
    from roost.extras.messaging_external.web.api_whatsapp import _notify_telegram

    msg = {"sender_name": "Alice", "text": "hi"}
    await _notify_telegram(msg, {
        "classification": {"intent": "buyer_lead", "urgency": "cold"},
        "draft": "",
        "status": "classified_only",
    })
    assert "reply_markup" not in (_CaptureClient.captured or {})


@pytest.mark.asyncio
async def test_chatwoot_notify_attaches_keyboard_when_held(patch_telegram):
    from roost.extras.messaging_external.web.api_chatwoot import _notify_telegram

    parsed = {"contact": {"name": "Bob"}, "content": "yo"}
    await _notify_telegram(parsed, {
        "classification": {"intent": "x", "urgency": "warm"},
        "draft": "Reply",
        "status": "awaiting_approval",
        "run_id": 11,
    })
    payload = _CaptureClient.captured
    assert payload is not None
    rm = payload.get("reply_markup")
    callbacks = [b["callback_data"] for b in rm["inline_keyboard"][0]]
    assert callbacks == ["recipe:approve:11", "recipe:edit:11", "recipe:skip:11"]


@pytest.mark.asyncio
async def test_wechat_notify_attaches_keyboard_when_held(patch_telegram):
    from roost.extras.messaging_external.web.api_wechat import _notify_telegram

    msg = {"sender": "user-abc", "text": "ping"}
    await _notify_telegram(msg, {
        "classification": {"intent": "x", "urgency": "warm"},
        "draft": "Reply",
        "status": "awaiting_approval",
        "run_id": 13,
    })
    payload = _CaptureClient.captured
    assert payload is not None
    rm = payload.get("reply_markup")
    callbacks = [b["callback_data"] for b in rm["inline_keyboard"][0]]
    assert callbacks == ["recipe:approve:13", "recipe:edit:13", "recipe:skip:13"]
