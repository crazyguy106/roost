"""Phase B — question packs in YAML.

Library packs ship at ``cadences/library/question-packs/`` and operator
overrides live at ``roost-config/question-packs/``. The qualification
flow reads via ``_get_pack(slug)`` which consults the YAML cache first
and falls back to the in-code dict only as a safety net.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_packs(tmp_path, monkeypatch):
    """Point USER_CONFIG_DIR + reload at a clean tmp dir so test
    overrides don't bleed into the dev machine's actual config."""
    from roost.extras.lead_nurture.services import question_packs
    fake = tmp_path / "question-packs"
    fake.mkdir()
    monkeypatch.setattr(question_packs, "USER_CONFIG_DIR", fake)
    question_packs.reload()
    yield fake, question_packs
    # Restore library-only state after the test
    question_packs.reload()


def test_library_packs_loaded_at_import(isolated_packs):
    _, qp = isolated_packs
    qp.reload()
    assert "property_buyer_intro" in qp._CACHE
    assert "financial_advisor_intro" in qp._CACHE


def test_financial_advisor_pack_comes_from_yaml_not_in_code(isolated_packs):
    """``QUESTIONS_BY_CADENCE`` doesn't define ``financial_advisor_intro``
    — if ``_get_pack`` returns one, it can only have come from YAML."""
    from roost.extras.lead_nurture.services import qualification
    assert "financial_advisor_intro" not in qualification.QUESTIONS_BY_CADENCE
    pack = qualification._get_pack("financial_advisor_intro")
    assert pack is not None
    assert len(pack) == 3
    assert {q["key"] for q in pack} == {"timeline", "trigger", "existing_coverage"}


def test_user_config_pack_overrides_library(isolated_packs):
    fake, qp = isolated_packs
    (fake / "financial_advisor_intro.yaml").write_text(
        "cadence_slug: financial_advisor_intro\n"
        "enabled: true\n"
        "questions:\n"
        "  - key: only_one\n"
        "    question: 'Custom question'\n"
        "    weight: 1.0\n"
    )
    qp.reload()
    from roost.extras.lead_nurture.services import qualification
    pack = qualification._get_pack("financial_advisor_intro")
    assert pack is not None
    assert len(pack) == 1
    assert pack[0]["key"] == "only_one"


def test_unknown_slug_returns_none(isolated_packs):
    from roost.extras.lead_nurture.services import qualification
    assert qualification._get_pack("not_a_real_slug") is None


def test_disabled_pack_ignored(isolated_packs):
    fake, qp = isolated_packs
    (fake / "disabled_one.yaml").write_text(
        "cadence_slug: disabled_one\n"
        "enabled: false\n"
        "questions:\n"
        "  - key: q1\n"
        "    question: 'Should not load'\n"
        "    weight: 1.0\n"
    )
    qp.reload()
    assert qp.get_questions_for_cadence("disabled_one") is None


def test_in_code_dict_still_fallback_for_property(isolated_packs):
    """If the library YAML for ``property_buyer_intro`` were missing, the
    in-code ``QUESTIONS_BY_CADENCE`` should still answer."""
    _, qp = isolated_packs
    # Simulate the YAML not loading by clearing the cache for this slug.
    qp._CACHE.pop("property_buyer_intro", None)
    from roost.extras.lead_nurture.services import qualification
    pack = qualification._get_pack("property_buyer_intro")
    assert pack is not None
    assert pack[0]["key"] == "timeline"


def test_scoring_through_yaml_pack(isolated_packs):
    """End-to-end: load YAML pack, score sample answers, verify routing."""
    from roost.extras.lead_nurture.services.qualification import _score, _get_pack
    pack = _get_pack("financial_advisor_intro")
    hot = _score(
        {"timeline": "asap", "trigger": "just married",
         "existing_coverage": "starting fresh"},
        pack,
    )
    cold = _score(
        {"timeline": "no rush", "trigger": "browsing",
         "existing_coverage": "comprehensive cover"},
        pack,
    )
    assert hot["label"] == "hot"
    assert cold["label"] == "cold"
