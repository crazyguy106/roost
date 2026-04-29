"""Tests for the PDPC DNC scrub service.

We don't hit the real registry — production credentials are issued by
PDPC manually. These tests verify normalisation, register validation,
config gating, and result-shape parsing using a mocked HTTP layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def configured_dnc(monkeypatch):
    """Pretend the adapter is configured so service calls reach the HTTP layer."""
    from roost.services import pdpc_dnc as dnc
    monkeypatch.setattr(dnc, "DNC_ENABLED", True)
    monkeypatch.setattr(dnc, "DNC_API_KEY", "test-key")
    monkeypatch.setattr(dnc, "DNC_ORG_ID", "ORG-1")
    yield


def test_normalises_various_input_formats():
    from roost.services.pdpc_dnc import _normalise
    assert _normalise("+6591234567") == "91234567"
    assert _normalise("6591234567") == "91234567"
    assert _normalise("91234567") == "91234567"
    assert _normalise("9123 4567") == "91234567"
    assert _normalise("9123-4567") == "91234567"


def test_rejects_non_singapore_numbers():
    from roost.services.pdpc_dnc import _normalise
    with pytest.raises(ValueError, match="not a valid Singapore"):
        _normalise("+14155551212")
    with pytest.raises(ValueError):
        _normalise("123")


def test_rejects_unknown_register():
    from roost.services import pdpc_dnc
    with pytest.raises(ValueError, match="unknown register"):
        pdpc_dnc.check(["91234567"], registers=["DNC_NoEmail"])


def test_disabled_raises_config_error(monkeypatch):
    from roost.services import pdpc_dnc
    monkeypatch.setattr(pdpc_dnc, "DNC_ENABLED", False)
    with pytest.raises(pdpc_dnc.DncConfigError, match="disabled"):
        pdpc_dnc.check(["91234567"])


def test_missing_credentials_raises_config_error(monkeypatch):
    from roost.services import pdpc_dnc
    monkeypatch.setattr(pdpc_dnc, "DNC_API_KEY", "")
    with pytest.raises(pdpc_dnc.DncConfigError, match="API_KEY"):
        pdpc_dnc.check(["91234567"])


def test_check_parses_blocked_and_allowed_numbers():
    from roost.services import pdpc_dnc

    fake_response = type(
        "R", (),
        {
            "raise_for_status": lambda self: None,
            "json": lambda self: {
                "results": [
                    {"phoneNumber": "91234567", "registersBlocked": ["DNC_NoTextMessage"]},
                    {"phoneNumber": "98765432", "registersBlocked": []},
                ]
            },
        },
    )()

    with patch("roost.services.pdpc_dnc.httpx.post", return_value=fake_response):
        out = pdpc_dnc.check(["+6591234567", "98765432"])

    assert out["ok"] is True
    assert out["registers_checked"] == list(pdpc_dnc.REGISTERS)
    by_input = {r["input"]: r for r in out["results"]}
    assert by_input["+6591234567"]["allowed"] is False
    assert by_input["+6591234567"]["registers_blocked"] == ["DNC_NoTextMessage"]
    assert by_input["98765432"]["allowed"] is True
    assert by_input["98765432"]["registers_blocked"] == []

    # Validity window — 21 days
    scrubbed = datetime.fromisoformat(out["scrubbed_at"])
    expires = datetime.fromisoformat(out["expires_at"])
    assert (expires - scrubbed).days == pdpc_dnc.SCRUB_VALIDITY_DAYS
    assert scrubbed.tzinfo is not None


def test_check_only_requested_registers():
    from roost.services import pdpc_dnc

    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["payload"] = json
        return type(
            "R", (),
            {"raise_for_status": lambda self: None, "json": lambda self: {"results": []}},
        )()

    with patch("roost.services.pdpc_dnc.httpx.post", side_effect=fake_post):
        pdpc_dnc.check(["91234567"], registers=["DNC_NoTextMessage"])

    assert captured["payload"]["Registers"] == ["DNC_NoTextMessage"]
    assert captured["payload"]["OrgId"] == "ORG-1"
    assert captured["payload"]["PhoneNumbers"] == ["91234567"]
