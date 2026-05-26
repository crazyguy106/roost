"""Tests for the Telegram nurture-approval handlers.

We construct minimal Update/CallbackQuery stand-ins (AsyncMock for the async
methods) and patch the underlying service functions, since `@authorized`
just gates on user_id being in TELEGRAM_ALLOWED_USERS.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.extras.lead_nurture.bot.nurture_approval import (
    cmd_napprove, cmd_nskip, cmd_nlist, cmd_preapprove,
    handle_nurture_callback,
)


@pytest.fixture(autouse=True)
def allow_user(monkeypatch):
    monkeypatch.setattr(
        "roost.bot.security.TELEGRAM_ALLOWED_USERS", {42},
    )


def _make_update(text: str = "/cmd"):
    msg = SimpleNamespace(reply_text=AsyncMock(), text=text)
    user = SimpleNamespace(id=42, first_name="Test")
    return SimpleNamespace(
        message=msg, effective_user=user, callback_query=None,
    )


def _make_ctx(args):
    return SimpleNamespace(args=args)


# ── /napprove ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_napprove_requires_id():
    upd = _make_update()
    await cmd_napprove(upd, _make_ctx([]))
    upd.message.reply_text.assert_awaited_once()
    assert "Usage" in upd.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_napprove_happy_path(monkeypatch):
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.nurture.approve_pending",
        lambda eid: {"ok": True, "action": "sent_after_approval"},
    )
    upd = _make_update()
    await cmd_napprove(upd, _make_ctx(["7"]))
    out = upd.message.reply_text.await_args.args[0]
    assert "#7" in out and "Approved" in out


@pytest.mark.asyncio
async def test_napprove_failure_reports_error(monkeypatch):
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.nurture.approve_pending",
        lambda eid: {"ok": False, "error": "no pending dispatch"},
    )
    upd = _make_update()
    await cmd_napprove(upd, _make_ctx(["7"]))
    out = upd.message.reply_text.await_args.args[0]
    assert "failed" in out and "no pending dispatch" in out


# ── /nskip ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nskip_happy_path(monkeypatch):
    captured = {}

    def fake_skip(eid, reason=""):
        captured["eid"] = eid
        captured["reason"] = reason
        return {"ok": True}

    monkeypatch.setattr("roost.extras.lead_nurture.services.nurture.skip_pending", fake_skip)
    upd = _make_update()
    await cmd_nskip(upd, _make_ctx(["12", "not", "interested"]))
    assert captured == {"eid": 12, "reason": "not interested"}
    assert "Skipped" in upd.message.reply_text.await_args.args[0]


# ── /nlist ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nlist_default_paused(monkeypatch):
    rows = [{
        "id": 1, "cadence_slug": "wa-buyer", "current_step": 0,
        "channel": "whatsapp", "contact_name": "Alice",
    }]
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.list_enrollments",
        lambda **kw: rows if kw.get("status") == "paused" else [],
    )
    upd = _make_update()
    await cmd_nlist(upd, _make_ctx([]))
    out = upd.message.reply_text.await_args.args[0]
    assert "paused" in out and "Alice" in out


@pytest.mark.asyncio
async def test_nlist_rejects_bad_status():
    upd = _make_update()
    await cmd_nlist(upd, _make_ctx(["garbage"]))
    assert "Usage" in upd.message.reply_text.await_args.args[0]


# ── /preapprove ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preapprove_parses_kv(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured.update(kw)
        return {"id": 99, "cadence_slug": kw["cadence_slug"],
                "source": kw["source"], "channel": kw["channel"],
                "vertical": kw["vertical"]}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.create_preapproval", fake_create,
    )
    upd = _make_update()
    await cmd_preapprove(upd, _make_ctx([
        "wa-buyer", "source=attio", "channel=whatsapp", "note=trusted",
    ]))
    assert captured["cadence_slug"] == "wa-buyer"
    assert captured["source"] == "attio"
    assert captured["channel"] == "whatsapp"
    assert captured["vertical"] == "*"
    assert captured["note"] == "trusted"
    assert "#99" in upd.message.reply_text.await_args.args[0]


# ── inline button callback ────────────────────────────────────────────


def _make_cb_update(data: str):
    query = SimpleNamespace(
        data=data,
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
    return SimpleNamespace(callback_query=query, effective_user=None)


@pytest.mark.asyncio
async def test_callback_ignores_unrelated():
    upd = _make_cb_update("other:123")
    handled = await handle_nurture_callback(upd, None)
    assert handled is False
    upd.callback_query.answer.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_napprove_success(monkeypatch):
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.nurture.approve_pending",
        lambda eid: {"ok": True},
    )
    upd = _make_cb_update("napprove:5")
    handled = await handle_nurture_callback(upd, None)
    assert handled is True
    upd.callback_query.answer.assert_awaited_with("Approved & sent")
    upd.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_napprove_failure_alerts(monkeypatch):
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.nurture.approve_pending",
        lambda eid: {"ok": False, "error": "boom"},
    )
    upd = _make_cb_update("napprove:5")
    handled = await handle_nurture_callback(upd, None)
    assert handled is True
    upd.callback_query.answer.assert_awaited_once()
    assert upd.callback_query.answer.await_args.kwargs.get("show_alert") is True
    upd.callback_query.edit_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_callback_nskip_success(monkeypatch):
    captured = {}

    def fake_skip(eid, reason=""):
        captured["eid"] = eid
        captured["reason"] = reason
        return {"ok": True}

    monkeypatch.setattr("roost.extras.lead_nurture.services.nurture.skip_pending", fake_skip)
    upd = _make_cb_update("nskip:9")
    handled = await handle_nurture_callback(upd, None)
    assert handled is True
    assert captured["eid"] == 9
    upd.callback_query.answer.assert_awaited_with("Skipped")


@pytest.mark.asyncio
async def test_callback_bad_id():
    upd = _make_cb_update("napprove:notanint")
    handled = await handle_nurture_callback(upd, None)
    assert handled is True
    upd.callback_query.answer.assert_awaited_once()
    assert upd.callback_query.answer.await_args.kwargs.get("show_alert") is True
