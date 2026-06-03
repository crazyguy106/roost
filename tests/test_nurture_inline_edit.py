"""Tests for the FA-edition nurture Edit flow.

Covers:
- `apply_draft_edit` writes `body_override` / `subject_override` to the
  held enrollment, and refuses when the enrollment isn't awaiting approval.
- `approve_pending` uses the override body when present (skips template
  re-render).
- `_hold_for_approval` clears stale overrides when a new step is held.
- `handle_nurture_callback` routes `nedit:<id>` — sends a force-reply
  prompt + stores `nedit_enrollment_id` in `chat_data`.
- `handle_nurture_edit_reply` captures the reply, calls `apply_draft_edit`,
  clears the marker, and re-shows the Approve/Edit/Skip keyboard.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.database import get_connection
from roost.extras.lead_nurture.bot.nurture_approval import (
    handle_nurture_callback, handle_nurture_edit_reply,
)
from roost.extras.lead_nurture.services import cadences as cadences_svc
from roost.extras.lead_nurture.services import nurture as nurture_svc


# ────────────────────────────────────────────────────────────────────
# Fixtures — minimal cadence + held enrollment we can reuse
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def clean_nurture_tables():
    conn = get_connection()
    for table in ("nurture_enrollments", "nurture_cadences", "cadence_preapprovals"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()
    yield
    conn = get_connection()
    for table in ("nurture_enrollments", "nurture_cadences", "cadence_preapprovals"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()


@pytest.fixture
def held_enrollment(clean_nurture_tables):
    """Create a cadence + enrollment paused with `awaiting_approval:0`."""
    cadences_svc.set_cadence(
        slug="t-buyer",
        name="Test Buyer",
        description="",
        vertical="generic",
        steps=[{"day_offset": 0, "template": "test-tmpl", "channel": "email"}],
        enabled=True,
        source="library",
        user_id="",
    )
    enr = cadences_svc.enroll_lead(
        cadence_slug="t-buyer",
        contact_email="lead@example.com",
        contact_name="Lead",
        channel="email",
    )
    cadences_svc.update_enrollment(
        enr["id"], status="paused", pause_reason="awaiting_approval:0",
    )
    return cadences_svc.get_enrollment(enr["id"])


# ────────────────────────────────────────────────────────────────────
# apply_draft_edit
# ────────────────────────────────────────────────────────────────────


def test_apply_draft_edit_stores_body(held_enrollment):
    res = nurture_svc.apply_draft_edit(
        held_enrollment["id"], body="Rewritten body.",
    )
    assert res["ok"] is True
    after = cadences_svc.get_enrollment(held_enrollment["id"])
    assert after["body_override"] == "Rewritten body."
    assert after["subject_override"] is None


def test_apply_draft_edit_stores_subject_when_given(held_enrollment):
    nurture_svc.apply_draft_edit(
        held_enrollment["id"], body="body", subject="new subject",
    )
    after = cadences_svc.get_enrollment(held_enrollment["id"])
    assert after["body_override"] == "body"
    assert after["subject_override"] == "new subject"


def test_apply_draft_edit_refuses_active_enrollment(clean_nurture_tables):
    cadences_svc.set_cadence(
        slug="t-x", name="X", description="", vertical="generic",
        steps=[{"day_offset": 0, "template": "t", "channel": "email"}],
        enabled=True, source="library", user_id="",
    )
    enr = cadences_svc.enroll_lead(
        cadence_slug="t-x", contact_email="a@b.com", channel="email",
    )
    res = nurture_svc.apply_draft_edit(enr["id"], body="nope")
    assert res["ok"] is False
    assert "not awaiting approval" in res["error"]


# ────────────────────────────────────────────────────────────────────
# approve_pending honours body_override
# ────────────────────────────────────────────────────────────────────


def test_approve_pending_uses_body_override(monkeypatch, held_enrollment):
    """When body_override is set, dispatch must receive the edited body —
    not the template-rendered one."""
    nurture_svc.apply_draft_edit(held_enrollment["id"], body="EDITED BODY")

    captured: dict = {}

    def fake_build(template_name, fields, channel, *, user_id=""):
        return {
            "subject": "Tmpl Subject",
            "body": "ORIGINAL TEMPLATE BODY",
            "template_id": 1,
            "template_name": template_name,
            "channel": channel,
        }

    def fake_dispatch(*, enrollment, message, when_utc):
        captured["body"] = message["body"]
        captured["subject"] = message["subject"]
        return {"ok": True, "channel": message["channel"], "ref": "x", "detail": "sent"}

    monkeypatch.setattr(nurture_svc, "_build_message", fake_build)
    monkeypatch.setattr(nurture_svc, "_dispatch_send", fake_dispatch)
    monkeypatch.setattr(nurture_svc, "_log_to_crm", lambda *a, **k: None)

    res = nurture_svc.approve_pending(held_enrollment["id"])
    assert res["ok"] is True
    assert captured["body"] == "EDITED BODY"
    # subject_override unset → keeps template subject
    assert captured["subject"] == "Tmpl Subject"


def test_approve_pending_without_override_uses_template(
    monkeypatch, held_enrollment,
):
    captured: dict = {}

    def fake_build(template_name, fields, channel, *, user_id=""):
        return {
            "subject": "Tmpl",
            "body": "TEMPLATE BODY",
            "template_id": 1,
            "template_name": template_name,
            "channel": channel,
        }

    def fake_dispatch(*, enrollment, message, when_utc):
        captured["body"] = message["body"]
        return {"ok": True, "channel": message["channel"], "ref": "x", "detail": "sent"}

    monkeypatch.setattr(nurture_svc, "_build_message", fake_build)
    monkeypatch.setattr(nurture_svc, "_dispatch_send", fake_dispatch)
    monkeypatch.setattr(nurture_svc, "_log_to_crm", lambda *a, **k: None)

    nurture_svc.approve_pending(held_enrollment["id"])
    assert captured["body"] == "TEMPLATE BODY"


# ────────────────────────────────────────────────────────────────────
# _hold_for_approval clears stale overrides
# ────────────────────────────────────────────────────────────────────


def test_hold_for_approval_clears_stale_overrides(
    monkeypatch, held_enrollment,
):
    """A previously-edited enrollment must start its NEXT held step from a
    clean slate — `body_override` is per-step, not persistent."""
    nurture_svc.apply_draft_edit(held_enrollment["id"], body="stale edit")
    monkeypatch.setattr(nurture_svc, "_notify_telegram", lambda *a, **k: None)

    nurture_svc._hold_for_approval(
        held_enrollment["id"], step_index=1, message={
            "channel": "email", "subject": "s", "body": "b",
            "template_name": "t",
        },
    )
    after = cadences_svc.get_enrollment(held_enrollment["id"])
    assert after["body_override"] is None
    assert after["subject_override"] is None


# ────────────────────────────────────────────────────────────────────
# Callback: nedit prompt
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
    ctx = SimpleNamespace(
        chat_data={},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    return ctx


@pytest.mark.asyncio
async def test_callback_nedit_prompts_for_reply(held_enrollment):
    upd = _make_cb_update(f"nedit:{held_enrollment['id']}")
    ctx = _make_ctx()
    handled = await handle_nurture_callback(upd, ctx)
    assert handled is True
    upd.callback_query.answer.assert_awaited_with("Reply with the edited body")
    ctx.bot.send_message.assert_awaited_once()
    sent_kwargs = ctx.bot.send_message.await_args.kwargs
    assert sent_kwargs["chat_id"] == 99
    assert f"#{held_enrollment['id']}" in sent_kwargs["text"]
    # ForceReply markup attached
    assert sent_kwargs.get("reply_markup") is not None
    # chat_data flag set so the reply MessageHandler picks it up
    assert ctx.chat_data.get("nedit_enrollment_id") == held_enrollment["id"]


@pytest.mark.asyncio
async def test_callback_nedit_rejects_non_held(clean_nurture_tables):
    """If the enrollment is active (not paused awaiting approval), Edit
    short-circuits with an alert."""
    cadences_svc.set_cadence(
        slug="t-y", name="Y", description="", vertical="generic",
        steps=[{"day_offset": 0, "template": "t", "channel": "email"}],
        enabled=True, source="library", user_id="",
    )
    enr = cadences_svc.enroll_lead(
        cadence_slug="t-y", contact_email="a@b.com", channel="email",
    )
    upd = _make_cb_update(f"nedit:{enr['id']}")
    ctx = _make_ctx()
    handled = await handle_nurture_callback(upd, ctx)
    assert handled is True
    upd.callback_query.answer.assert_awaited_once()
    assert upd.callback_query.answer.await_args.kwargs.get("show_alert") is True
    assert ctx.chat_data.get("nedit_enrollment_id") is None


# ────────────────────────────────────────────────────────────────────
# handle_nurture_edit_reply captures reply, applies, re-shows keyboard
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
    """No `nedit_enrollment_id` in chat_data → handler returns False so the
    agent catch-all gets a chance."""
    upd = _make_reply_update("hello")
    ctx = _make_ctx()
    handled = await handle_nurture_edit_reply(upd, ctx)
    assert handled is False


@pytest.mark.asyncio
async def test_edit_reply_applies_override_and_reshows_keyboard(held_enrollment):
    upd = _make_reply_update("Updated body from operator.")
    ctx = _make_ctx()
    ctx.chat_data["nedit_enrollment_id"] = held_enrollment["id"]

    handled = await handle_nurture_edit_reply(upd, ctx)
    assert handled is True
    # marker cleared
    assert "nedit_enrollment_id" not in ctx.chat_data
    # override persisted
    after = cadences_svc.get_enrollment(held_enrollment["id"])
    assert after["body_override"] == "Updated body from operator."
    # confirmation message with the Approve/Edit/Skip keyboard
    ctx.bot.send_message.assert_awaited_once()
    kw = ctx.bot.send_message.await_args.kwargs
    rm = kw["reply_markup"]
    btns = rm["inline_keyboard"][0]
    callbacks = [b["callback_data"] for b in btns]
    assert callbacks == [
        f"napprove:{held_enrollment['id']}",
        f"nedit:{held_enrollment['id']}",
        f"nskip:{held_enrollment['id']}",
    ]


@pytest.mark.asyncio
async def test_edit_reply_empty_text_keeps_previous_draft(held_enrollment):
    upd = _make_reply_update("   ")  # whitespace only
    ctx = _make_ctx()
    ctx.chat_data["nedit_enrollment_id"] = held_enrollment["id"]

    handled = await handle_nurture_edit_reply(upd, ctx)
    assert handled is True
    upd.message.reply_text.assert_awaited_once()
    assert "Empty" in upd.message.reply_text.await_args.args[0]
    # marker cleared, no override applied
    assert "nedit_enrollment_id" not in ctx.chat_data
    after = cadences_svc.get_enrollment(held_enrollment["id"])
    assert after["body_override"] is None
