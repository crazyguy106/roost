"""Tests for the Telegram `/lead` inbound capture handler."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from roost.extras.lead_nurture.bot.lead_capture import (
    cmd_lead, _parse_lead_command, _classify_contact,
)


@pytest.fixture(autouse=True)
def allow_user(monkeypatch):
    monkeypatch.setattr("roost.bot.security.TELEGRAM_ALLOWED_USERS", {42})


def _make_update(text: str):
    msg = SimpleNamespace(reply_text=AsyncMock(), text=text)
    user = SimpleNamespace(id=42, first_name="Test")
    return SimpleNamespace(message=msg, effective_user=user, callback_query=None)


def _ctx():
    return SimpleNamespace(args=[])


# ── parser ────────────────────────────────────────────────────────────


def test_parse_empty_returns_usage():
    out = _parse_lead_command("/lead")
    assert "Usage" in out["error"]


def test_parse_missing_contact_errors():
    out = _parse_lead_command("/lead Alice Tan")
    assert "name and a contact" in out["error"]


def test_parse_minimal_name_and_email():
    out = _parse_lead_command("/lead Alice Tan | alice@example.com")
    assert out["name"] == "Alice Tan"
    assert out["contact"] == "alice@example.com"
    assert out["vertical"] == "generic"
    assert out["notes"] == ""


def test_parse_full_four_parts():
    out = _parse_lead_command(
        "/lead Bob Lee | +6591234567 | property_agent | met at networking dinner"
    )
    assert out["name"] == "Bob Lee"
    assert out["contact"] == "+6591234567"
    assert out["vertical"] == "property_agent"
    assert out["notes"] == "met at networking dinner"


def test_parse_handles_extra_pipes_in_notes():
    # Anything after the third pipe folds into notes — but split is fixed
    # at 4 fields, so a stray pipe in notes lands as a 5th part and is dropped
    # (acceptable tradeoff vs awkward escaping).
    out = _parse_lead_command(
        "/lead Carol | carol@x.com | generic | needs ROI by Q3"
    )
    assert out["notes"] == "needs ROI by Q3"


# ── contact classifier ────────────────────────────────────────────────


def test_classify_email():
    kind, value = _classify_contact("alice@example.com")
    assert kind == "email"
    assert value == "alice@example.com"


def test_classify_phone():
    kind, value = _classify_contact("+6591234567")
    assert kind == "phone"
    assert value == "+6591234567"


def test_classify_phone_strips_formatting_punctuation():
    """Spaces, dashes, parens, dots — all common in user input."""
    for raw, expected in (
        ("+65 9123 4567", "+6591234567"),
        ("+65-9123-4567", "+6591234567"),
        ("(65) 9123 4567", "6591234567"),
        ("+65.9123.4567", "+6591234567"),
    ):
        kind, value = _classify_contact(raw)
        assert kind == "phone", f"{raw!r} → {kind}"
        assert value == expected, f"{raw!r} normalised to {value!r}"


def test_classify_local_8_digit_phone_accepted():
    """SG/MY/HK mobile without country code is still a plausible phone."""
    kind, value = _classify_contact("91234567")
    assert kind == "phone"
    assert value == "91234567"


def test_classify_rejects_too_short():
    kind, _ = _classify_contact("1234567")  # 7 digits — below ITU min
    assert kind == "unknown"


def test_classify_rejects_too_long():
    kind, _ = _classify_contact("+1234567890123456")  # 16 digits — above E.164 max
    assert kind == "unknown"


def test_classify_rejects_gibberish():
    for raw in ("???", "hello", "abc@xyz", "@no-local", ""):
        kind, _ = _classify_contact(raw)
        assert kind == "unknown", f"{raw!r} → {kind}"


def test_classify_rejects_letters_mixed_with_digits():
    """'call me 9123' style — has digits but isn't a phone number."""
    kind, _ = _classify_contact("call me 9123")
    assert kind == "unknown"


# ── handler ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_lead_usage_on_empty():
    upd = _make_update("/lead")
    await cmd_lead(upd, _ctx())
    out = upd.message.reply_text.await_args.args[0]
    assert "Usage" in out


@pytest.mark.asyncio
async def test_cmd_lead_happy_email(monkeypatch):
    captured = {}

    def fake_ingest(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "enrollment_id": 17,
            "cadence_slug": "generic_b2b",
            "crm_person_id": "p_42",
        }

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update("/lead Alice Tan | alice@example.com")
    await cmd_lead(upd, _ctx())

    assert captured["channel"] == "telegram"
    assert captured["source"] == "telegram_promote"
    assert captured["name"] == "Alice Tan"
    assert captured["email"] == "alice@example.com"
    assert "phone" not in captured  # email branch shouldn't set phone

    out = upd.message.reply_text.await_args.args[0]
    assert "Enrollment #17" in out
    assert "generic_b2b" in out
    assert "p_42" in out


@pytest.mark.asyncio
async def test_cmd_lead_happy_phone_with_vertical_and_notes(monkeypatch):
    captured = {}

    def fake_ingest(**kwargs):
        captured.update(kwargs)
        return {"ok": True, "enrollment_id": 18, "cadence_slug": "property_warm"}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update("/lead Bob | +6591234567 | property_agent | met at dinner")
    await cmd_lead(upd, _ctx())

    assert captured["phone"] == "+6591234567"
    assert "email" not in captured
    assert captured["vertical"] == "property_agent"
    assert captured["fields"] == {"notes": "met at dinner"}


@pytest.mark.asyncio
async def test_cmd_lead_surfaces_service_errors(monkeypatch):
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead",
        lambda **kw: {"ok": False, "errors": ["CRM not configured: missing API key"]},
    )

    upd = _make_update("/lead Alice | alice@x.com")
    await cmd_lead(upd, _ctx())
    out = upd.message.reply_text.await_args.args[0]
    assert "not ingested" in out
    assert "CRM not configured" in out


@pytest.mark.asyncio
async def test_cmd_lead_rejects_unparseable_contact(monkeypatch):
    """A non-email non-phone contact must NOT reach ingest_lead."""
    called = {"n": 0}

    def fake_ingest(**kw):
        called["n"] += 1
        return {"ok": True}

    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", fake_ingest,
    )

    upd = _make_update("/lead Alice | ??? gibberish ???")
    await cmd_lead(upd, _ctx())
    out = upd.message.reply_text.await_args.args[0]
    assert "Couldn't parse" in out
    assert called["n"] == 0  # service must not be invoked


@pytest.mark.asyncio
async def test_cmd_lead_handles_unexpected_exception(monkeypatch):
    def boom(**kw):
        raise RuntimeError("DB connection lost")
    monkeypatch.setattr(
        "roost.extras.lead_nurture.services.leads.ingest_lead", boom,
    )
    upd = _make_update("/lead Alice | alice@x.com")
    await cmd_lead(upd, _ctx())
    out = upd.message.reply_text.await_args.args[0]
    assert "Couldn't ingest lead" in out
    assert "DB connection lost" in out
