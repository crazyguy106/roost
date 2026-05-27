"""Unit tests for the Telegram customer fallback handler.

Covers `handle_customer_message` — the group=-3 dispatcher that converts
non-operator Telegram DMs into lead-ingest, STOP/HELP intercepts,
qualification answers, or mark_inbound bumps.

Pattern mirrors `test_bot_lead_capture.py`: build a fake Update via
SimpleNamespace + AsyncMock so we can call the async handler directly
without spinning up a real PTB Application.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.extras.lead_nurture.bot.customer import (
    _Stop,
    _first_token,
    handle_customer_message,
)


# ── Update / context builders ─────────────────────────────────────────


def _make_update(text: str, *, user_id: int = 999, chat_id: int = 999,
                 first_name: str = "Cust", last_name: str = ""):
    msg = SimpleNamespace(reply_text=AsyncMock(), text=text)
    user = SimpleNamespace(id=user_id, first_name=first_name, last_name=last_name)
    chat = SimpleNamespace(id=chat_id)
    return SimpleNamespace(
        message=msg,
        effective_user=user,
        effective_chat=chat,
    )


def _ctx():
    return SimpleNamespace()


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch):
    """Operator allowlist for these tests: just user 42."""
    monkeypatch.setattr(
        "roost.extras.lead_nurture.bot.customer.TELEGRAM_ALLOWED_USERS", {42},
    )


# ── _first_token unit ─────────────────────────────────────────────────


def test_first_token_basics():
    assert _first_token("STOP") == "STOP"
    assert _first_token("stop") == "STOP"
    assert _first_token("stop.") == "STOP"
    assert _first_token(" Stop! ") == "STOP"
    assert _first_token("HELP me please") == "HELP"
    assert _first_token("") == ""
    assert _first_token("   ") == ""


# ── Passthrough cases (handler returns silently) ──────────────────────


@pytest.mark.asyncio
async def test_empty_text_returns_silently():
    upd = _make_update("")
    # Should not raise Stop; should not call reply_text.
    out = await handle_customer_message(upd, _ctx())
    assert out is None
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_slash_command_passes_through():
    """Slash commands belong to CommandHandlers — fallback must yield."""
    upd = _make_update("/inbox")
    out = await handle_customer_message(upd, _ctx())
    assert out is None
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_link_prefix_passes_through():
    """`LINK <code>` is handled at group -2 — fallback must yield."""
    upd = _make_update("LINK abc123")
    out = await handle_customer_message(upd, _ctx())
    assert out is None
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_operator_message_passes_through():
    """Operator (allowlisted user) → normal stack handles it."""
    upd = _make_update("hello bot", user_id=42)
    out = await handle_customer_message(upd, _ctx())
    assert out is None
    upd.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_message_or_chat_returns_silently():
    upd = SimpleNamespace(
        message=None,
        effective_user=SimpleNamespace(id=999, first_name="x", last_name=""),
        effective_chat=SimpleNamespace(id=999),
    )
    out = await handle_customer_message(upd, _ctx())
    assert out is None


# ── STOP ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_keyword_exits_enrollments_and_replies(monkeypatch):
    called = {"exit": None, "ingest": 0, "mark": 0}

    def fake_exit(*, telegram_chat_id="", phone="", email="", reason=""):
        called["exit"] = {
            "telegram_chat_id": telegram_chat_id,
            "reason": reason,
        }
        return 2

    def fake_ingest(**kw):
        called["ingest"] += 1

    def fake_mark(**kw):
        called["mark"] += 1
        return 0

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        fake_exit,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        fake_mark,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update("STOP", chat_id=777)
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert called["exit"] == {
        "telegram_chat_id": "777",
        "reason": "opted_out:telegram",
    }
    assert called["ingest"] == 0
    assert called["mark"] == 0
    # Confirmation reply was sent.
    upd.message.reply_text.assert_awaited_once()
    reply = upd.message.reply_text.await_args.args[0]
    assert "unsubscribed" in reply.lower()
    assert "start" in reply.lower()  # mentions START to resubscribe


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    "stop.",
    " Stop! ",
    "UNSUBSCRIBE",
    "cancel",
    "Quit",
    "STOPALL",
    "END",
])
async def test_stop_variants_all_fire(monkeypatch, body):
    exits = []

    def fake_exit(*, telegram_chat_id="", phone="", email="", reason=""):
        exits.append(telegram_chat_id)
        return 1

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        fake_exit,
    )

    upd = _make_update(body, chat_id=555)
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert exits == ["555"], f"variant {body!r} did not trigger exit"


@pytest.mark.asyncio
async def test_stop_swallows_exit_exception(monkeypatch):
    """Broken DB must not bubble back to Telegram (would retry-storm).
    Reply still sent."""

    def boom(**kw):
        raise RuntimeError("db gone")

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        boom,
    )

    upd = _make_update("STOP")
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    upd.message.reply_text.assert_awaited_once()


# ── HELP ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_help_keyword_replies_with_help_text(monkeypatch):
    called = {"exit": 0, "ingest": 0, "mark": 0}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.exit_enrollments_by_contact",
        lambda **kw: called.__setitem__("exit", called["exit"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: called.__setitem__("mark", called["mark"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: called.__setitem__("ingest", called["ingest"] + 1),
    )

    upd = _make_update("HELP")
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert called == {"exit": 0, "ingest": 0, "mark": 0}
    upd.message.reply_text.assert_awaited_once()
    reply = upd.message.reply_text.await_args.args[0]
    assert "stop" in reply.lower()
    assert "support" in reply.lower()


@pytest.mark.asyncio
async def test_info_keyword_also_fires_help(monkeypatch):
    upd = _make_update("INFO")
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())
    upd.message.reply_text.assert_awaited_once()


# ── Qualification routing ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_qualification_handled_short_circuits(monkeypatch):
    """If qualification engine handles the message, no ingest, no mark_inbound."""
    called = {"qual": None, "ingest": 0, "mark": 0}

    def fake_qual(*, channel, identifier, text):
        called["qual"] = {"channel": channel, "id": identifier, "text": text}
        return {"handled": True, "complete": False}

    def fake_ingest(**kw):
        called["ingest"] += 1

    def fake_mark(**kw):
        called["mark"] += 1
        return 0

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        fake_qual,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        fake_mark,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update("about 50 staff", chat_id=321)
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert called["qual"] == {
        "channel": "telegram", "id": "321", "text": "about 50 staff",
    }
    assert called["ingest"] == 0
    assert called["mark"] == 0


@pytest.mark.asyncio
async def test_qualification_unhandled_falls_through_to_mark_inbound(monkeypatch):
    """qualification.process_answer returns handled=False → ingest path runs."""
    called = {"qual": 0, "mark": 0, "ingest": 0}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        lambda **kw: (called.__setitem__("qual", called["qual"] + 1)
                      or {"handled": False}),
    )
    # mark_inbound returns 0 → no existing enrollment → fall through to ingest
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: called.__setitem__("mark", called["mark"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: called.__setitem__("ingest", called["ingest"] + 1),
    )

    upd = _make_update("hi there")
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert called == {"qual": 1, "mark": 1, "ingest": 1}


@pytest.mark.asyncio
async def test_qualification_exception_is_non_fatal(monkeypatch):
    """Broken qualification engine must not crash the inbound path."""
    called = {"mark": 0, "ingest": 0}

    def boom(**kw):
        raise RuntimeError("qual broken")

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        boom,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: called.__setitem__("mark", called["mark"] + 1) or 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: called.__setitem__("ingest", called["ingest"] + 1),
    )

    upd = _make_update("hi")
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    # Despite qualification raising, we still fall through to ingest pipeline.
    assert called == {"mark": 1, "ingest": 1}


# ── Existing-enrollment inbound (mark_inbound, no re-ingest) ──────────


@pytest.mark.asyncio
async def test_existing_enrollment_marks_inbound_and_skips_ingest(monkeypatch):
    called = {"mark": None, "ingest": 0}

    def fake_mark(*, telegram_chat_id="", phone="", email="", when_utc=""):
        called["mark"] = telegram_chat_id
        return 2  # 2 enrollments touched

    def fake_ingest(**kw):
        called["ingest"] += 1

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        lambda **kw: {"handled": False},
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        fake_mark,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update("thanks, sounds good", chat_id=888)
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert called["mark"] == "888"
    assert called["ingest"] == 0  # MUST NOT re-ingest


# ── First-contact ingest ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_contact_calls_ingest_lead(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        lambda **kw: {"handled": False},
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: 0,  # no existing enrollment
    )

    def fake_ingest(**kw):
        captured.update(kw)

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update(
        "I'd like a SOC quote",
        chat_id=111, first_name="Alice", last_name="Tan",
    )
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert captured["channel"] == "telegram"
    assert captured["telegram_chat_id"] == "111"
    assert captured["name"] == "Alice Tan"
    assert captured["message_text"] == "I'd like a SOC quote"
    assert captured["source"] == "telegram_inbound"
    assert captured["qualifying_identifier"] == "111"


@pytest.mark.asyncio
async def test_first_contact_name_handles_missing_last_name(monkeypatch):
    captured: dict = {}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        lambda **kw: {"handled": False},
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: 0,
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: captured.update(kw),
    )

    upd = _make_update("hi", first_name="Solo", last_name="")
    with pytest.raises(_Stop):
        await handle_customer_message(upd, _ctx())

    assert captured["name"] == "Solo"


@pytest.mark.asyncio
async def test_first_contact_swallows_ingest_exception(monkeypatch):
    """Lead ingest must not bubble — Telegram would retry-storm us."""

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.qualification.process_answer",
        lambda **kw: {"handled": False},
    )
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.cadences.store.mark_inbound_for_contact",
        lambda **kw: 0,
    )

    def boom(**kw):
        raise RuntimeError("db locked")

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", boom,
    )

    upd = _make_update("hello")
    with pytest.raises(_Stop):  # still raises Stop, not RuntimeError
        await handle_customer_message(upd, _ctx())
