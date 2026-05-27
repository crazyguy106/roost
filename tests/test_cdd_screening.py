"""Tests for CDD screening (sanctions/PEP/adverse media).

Real vendor API not hit. Verifies vendor dispatch, config gating, parse
shape, risk-score weighting, and the 30-day expiry window.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def configured_cdd(monkeypatch):
    from roost.extras.property_agent.services import cdd_screening as cdd
    monkeypatch.setattr(cdd, "CDD_ENABLED", True)
    monkeypatch.setattr(cdd, "CDD_VENDOR", "complyadvantage")
    monkeypatch.setattr(cdd, "CDD_API_KEY", "test-key")
    monkeypatch.setattr(cdd, "CDD_API_BASE_URL", "")
    monkeypatch.setattr(cdd, "CDD_REFRESH_DAYS", 30)
    yield


def _fake_resp(body: dict):
    return type(
        "R", (),
        {"raise_for_status": lambda self: None, "json": lambda self: body},
    )()


def test_disabled_returns_config_error(monkeypatch):
    from roost.extras.property_agent.services import cdd_screening as cdd
    monkeypatch.setattr(cdd, "CDD_ENABLED", False)
    with pytest.raises(cdd.CddConfigError, match="disabled"):
        cdd.screen("Test")


def test_unknown_vendor_raises(monkeypatch):
    from roost.extras.property_agent.services import cdd_screening as cdd
    monkeypatch.setattr(cdd, "CDD_VENDOR", "made-up")
    with pytest.raises(cdd.CddConfigError, match="unknown CDD vendor"):
        cdd.screen("Test")


def test_acuris_not_implemented(monkeypatch):
    from roost.extras.property_agent.services import cdd_screening as cdd
    monkeypatch.setattr(cdd, "CDD_VENDOR", "acuris")
    with pytest.raises(NotImplementedError):
        cdd.screen("Test")


def test_missing_api_key_raises(monkeypatch):
    from roost.extras.property_agent.services import cdd_screening as cdd
    monkeypatch.setattr(cdd, "CDD_API_KEY", "")
    with pytest.raises(cdd.CddConfigError, match="API_KEY"):
        cdd.screen("Test")


def test_empty_name_rejected():
    from roost.extras.property_agent.services import cdd_screening as cdd
    with pytest.raises(ValueError, match="name is required"):
        cdd.screen("   ")


def test_clean_screen_returns_no_hits():
    from roost.extras.property_agent.services import cdd_screening as cdd
    body = {"content": {"data": {"id": "search-123", "hits": []}}}
    with patch("roost.extras.property_agent.services.cdd_screening.httpx.post", return_value=_fake_resp(body)):
        out = cdd.screen("Tan Ah Kow", dob="1980-05-12", nationality="SG")
    assert out["ok"] is True
    assert out["matched"] is False
    assert out["hits"] == []
    assert out["risk_score"] == 0.0
    assert out["vendor"] == "complyadvantage"
    assert out["search_id"] == "search-123"
    # 30-day expiry window
    delta = datetime.fromisoformat(out["expires_at"]) - datetime.fromisoformat(out["screened_at"])
    assert delta.days == 30


def test_sanction_hit_marks_matched_and_high_risk():
    from roost.extras.property_agent.services import cdd_screening as cdd
    body = {"content": {"data": {"id": "s1", "hits": [
        {
            "score": 0.95,
            "match_status": "true_positive",
            "doc": {
                "name": "John Sanctioned",
                "types": ["sanction"],
                "sources": ["OFAC SDN", "UN Consolidated"],
            },
        }
    ]}}}
    with patch("roost.extras.property_agent.services.cdd_screening.httpx.post", return_value=_fake_resp(body)):
        out = cdd.screen("John Sanctioned")
    assert out["matched"] is True
    assert out["risk_score"] >= 0.9
    assert out["hits"][0]["types"] == ["sanction"]
    assert "OFAC SDN" in out["hits"][0]["sources"]


def test_false_positive_does_not_count_as_match():
    from roost.extras.property_agent.services import cdd_screening as cdd
    body = {"content": {"data": {"hits": [
        {
            "score": 0.4,
            "match_status": "false_positive",
            "doc": {"name": "Common Name", "types": ["pep"]},
        }
    ]}}}
    with patch("roost.extras.property_agent.services.cdd_screening.httpx.post", return_value=_fake_resp(body)):
        out = cdd.screen("Common Name")
    assert out["matched"] is False
    assert out["risk_score"] == 0.0


def test_pep_hit_lower_risk_than_sanction():
    from roost.extras.property_agent.services import cdd_screening as cdd
    body = {"content": {"data": {"hits": [
        {
            "score": 0.8,
            "match_status": "potential_match",
            "doc": {"name": "Some Minister", "types": ["pep-class-1"]},
        }
    ]}}}
    with patch("roost.extras.property_agent.services.cdd_screening.httpx.post", return_value=_fake_resp(body)):
        out = cdd.screen("Some Minister")
    assert out["matched"] is True
    # PEP class-1 weight 0.6 × potential_match 0.7 × score 0.8 = 0.336
    assert 0.2 < out["risk_score"] < 0.5


def test_search_payload_includes_dob_and_country():
    from roost.extras.property_agent.services import cdd_screening as cdd
    captured = {}

    def fake_post(url, params, json, timeout):
        captured["url"] = url
        captured["params"] = params
        captured["json"] = json
        return _fake_resp({"content": {"data": {"hits": []}}})

    with patch("roost.extras.property_agent.services.cdd_screening.httpx.post", side_effect=fake_post):
        cdd.screen("Tan Ah Kow", dob="1980-05-12", nationality="sg", id_number="S1234567A")

    assert captured["params"] == {"api_key": "test-key"}
    assert captured["json"]["search_term"] == "Tan Ah Kow"
    assert captured["json"]["filters"]["birth_year"] == 1980
    assert captured["json"]["filters"]["country_codes"] == ["SG"]
    assert captured["json"]["client_ref"] == "S1234567A"
