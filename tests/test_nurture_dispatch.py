"""Channel matrix for `nurture._dispatch_send`.

Each test stubs the relevant external send (Gmail scheduler, WhatsApp send,
Telegram notifier) so we never hit the network. Covers happy paths plus the
common failure modes (missing contact, raised exceptions, unsupported channel).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


def _msg(channel: str, **extra) -> dict:
    return {
        "subject": "Hi",
        "body": "Body text",
        "template_id": 1,
        "template_name": "tmpl",
        "channel": channel,
        **extra,
    }


def _enr(**extra) -> dict:
    base = {
        "id": 1, "user_id": "",
        "contact_email": "to@example.com",
        "contact_phone": "+6591234567",
    }
    base.update(extra)
    return base


# ── Email ─────────────────────────────────────────────────────────────


def test_dispatch_email_happy(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n
    calls: list[dict] = []
    monkeypatch.setattr(
        "roost.services.scheduled_emails.schedule_email",
        lambda **kw: calls.append(kw) or {"id": 99},
    )
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("email"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is True
    assert out["ref"] == "99"
    assert calls and calls[0]["to"] == "to@example.com"
    assert calls[0]["provider"] == "gmail"


def test_dispatch_email_missing_contact():
    from roost.extras.lead_nurture.services import nurture as n
    out = n._dispatch_send(
        enrollment=_enr(contact_email=""), message=_msg("email"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "no contact_email" in out["detail"]


def test_dispatch_email_raises_returns_not_ok(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n

    def boom(**kw):
        raise RuntimeError("smtp down")

    monkeypatch.setattr("roost.services.scheduled_emails.schedule_email", boom)
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("email"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "smtp down" in out["detail"]


# ── WhatsApp ──────────────────────────────────────────────────────────


def test_dispatch_whatsapp_happy(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.whatsapp.send_text_message",
        lambda to, text: {"messages": [{"id": "wamid.123"}]},
    )
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("whatsapp"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is True
    assert out["ref"] == "wamid.123"


def test_dispatch_whatsapp_missing_phone():
    from roost.extras.lead_nurture.services import nurture as n
    out = n._dispatch_send(
        enrollment=_enr(contact_phone=""), message=_msg("whatsapp"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "no contact_phone" in out["detail"]


def test_dispatch_whatsapp_raises(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n

    def boom(to, text):
        raise RuntimeError("131047 re-engagement window")

    monkeypatch.setattr("roost.extras.messaging_external.services.whatsapp.send_text_message", boom)
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("whatsapp"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "131047" in out["detail"]


# ── SMS ───────────────────────────────────────────────────────────────


def test_dispatch_sms_happy(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.send_sms",
        lambda to, body: {"ok": True, "message_id": "SM_abc"},
    )
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("sms"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is True
    assert out["channel"] == "sms"
    assert out["ref"] == "SM_abc"


def test_dispatch_sms_missing_phone():
    from roost.extras.lead_nurture.services import nurture as n
    out = n._dispatch_send(
        enrollment=_enr(contact_phone=""), message=_msg("sms"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "no contact_phone" in out["detail"]


def test_dispatch_sms_adapter_error_surfaces(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n
    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.send_sms",
        lambda to, body: {"ok": False, "error": "SMS not enabled (SMS_ENABLED=false)"},
    )
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("sms"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "SMS not enabled" in out["detail"]


# ── Telegram + unsupported ────────────────────────────────────────────


def test_dispatch_telegram_broadcast(monkeypatch):
    from roost.extras.lead_nurture.services import nurture as n
    seen: list[str] = []
    monkeypatch.setattr(n, "_notify_telegram", lambda text: seen.append(text))
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("telegram"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is True
    assert out["channel"] == "telegram"
    assert seen == ["Body text"]


def test_dispatch_unsupported_channel():
    from roost.extras.lead_nurture.services import nurture as n
    out = n._dispatch_send(
        enrollment=_enr(), message=_msg("carrier_pigeon"),
        when_utc=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    assert out["ok"] is False
    assert "unsupported channel" in out["detail"]
