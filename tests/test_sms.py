"""Unit tests for the Twilio SMS adapter.

All HTTP is intercepted with `httpx.MockTransport` — no network, no env
secrets needed. Config is monkey-patched on the `roost.config` module
*and* on `roost.extras.messaging_external.services.sms` (which imports
them by name at module load).
"""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture
def enable_twilio(monkeypatch):
    """Flip SMS_ENABLED + populate Twilio creds for the adapter module."""
    from roost.extras.messaging_external.services import sms as sms_mod

    monkeypatch.setattr(sms_mod, "SMS_ENABLED", True)
    monkeypatch.setattr(sms_mod, "SMS_PROVIDER", "twilio")
    monkeypatch.setattr(sms_mod, "TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setattr(sms_mod, "TWILIO_AUTH_TOKEN", "tok_test")
    monkeypatch.setattr(sms_mod, "TWILIO_FROM_NUMBER", "+15550001234")
    return sms_mod


_REAL_HTTPX_CLIENT = httpx.Client


def _mock_client(handler):
    """Build an httpx.Client whose requests are routed to `handler`.

    Bound to the original `httpx.Client` constructor up-front so it still
    works after the adapter module's `httpx.Client` reference is patched.
    """
    return _REAL_HTTPX_CLIENT(transport=httpx.MockTransport(handler), timeout=10)


# ── Disabled / misconfigured ──────────────────────────────────────────


def test_disabled_returns_error(monkeypatch):
    from roost.extras.messaging_external.services import sms as sms_mod
    monkeypatch.setattr(sms_mod, "SMS_ENABLED", False)
    out = sms_mod.send_sms(to="+6591234567", body="hi")
    assert out["ok"] is False
    assert "SMS not enabled" in out["error"]


def test_missing_creds_returns_error(monkeypatch):
    from roost.extras.messaging_external.services import sms as sms_mod
    monkeypatch.setattr(sms_mod, "SMS_ENABLED", True)
    monkeypatch.setattr(sms_mod, "SMS_PROVIDER", "twilio")
    monkeypatch.setattr(sms_mod, "TWILIO_ACCOUNT_SID", "")
    monkeypatch.setattr(sms_mod, "TWILIO_AUTH_TOKEN", "")
    monkeypatch.setattr(sms_mod, "TWILIO_FROM_NUMBER", "")
    out = sms_mod.send_sms(to="+6591234567", body="hi")
    assert out["ok"] is False
    assert "Twilio not configured" in out["error"]


def test_unsupported_provider(monkeypatch):
    from roost.extras.messaging_external.services import sms as sms_mod
    monkeypatch.setattr(sms_mod, "SMS_ENABLED", True)
    monkeypatch.setattr(sms_mod, "SMS_PROVIDER", "messagebird")
    out = sms_mod.send_sms(to="+6591234567", body="hi")
    assert out["ok"] is False
    assert "Unsupported SMS provider" in out["error"]


# ── Happy path ────────────────────────────────────────────────────────


def test_happy_path_returns_message_sid(enable_twilio, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization", "")
        captured["body"] = request.content.decode()
        return httpx.Response(
            201,
            json={"sid": "SM_happy", "status": "queued", "to": "+6591234567"},
        )

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.httpx.Client",
        lambda **kw: _mock_client(handler),
    )

    out = enable_twilio.send_sms(to="+6591234567", body="hello world")
    assert out["ok"] is True
    assert out["message_id"] == "SM_happy"
    assert out["provider"] == "twilio"

    # Posted to the right account-scoped endpoint, with Basic auth, in form-encoded body.
    assert "/Accounts/AC_test/Messages.json" in captured["url"]
    assert captured["auth"].startswith("Basic ")
    assert "To=%2B6591234567" in captured["body"]
    assert "From=%2B15550001234" in captured["body"]
    assert "Body=hello+world" in captured["body"]


# ── 4xx / error envelope ──────────────────────────────────────────────


def test_4xx_returns_error_envelope(enable_twilio, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": 21211, "message": "Invalid 'To' phone number"},
        )

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.httpx.Client",
        lambda **kw: _mock_client(handler),
    )

    out = enable_twilio.send_sms(to="not-a-phone", body="hi")
    assert out["ok"] is False
    assert "Twilio API 400" in out["error"]
    assert out["details"]["code"] == 21211


def test_network_exception_returns_error_envelope(enable_twilio, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS lookup failed")

    monkeypatch.setattr(
        "roost.extras.messaging_external.services.sms.httpx.Client",
        lambda **kw: _mock_client(handler),
    )

    out = enable_twilio.send_sms(to="+6591234567", body="hi")
    assert out["ok"] is False
    assert "DNS lookup failed" in out["error"]
