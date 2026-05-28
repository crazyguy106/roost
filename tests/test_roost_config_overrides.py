"""Phase A — cadence override semantics for ``roost-config/cadences/``.

The library file ``financial_advisor_intro.yaml`` is seeded into the DB
on boot. Dropping a same-slug file under ``roost-config/cadences/`` and
calling ``seed_user_config()`` must overwrite the library row.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture
def override_dir(tmp_path, monkeypatch):
    """Redirect USER_CONFIG_DIR to a tmp dir so we don't touch the
    real ``roost-config/cadences/`` on the dev machine."""
    from roost.extras.lead_nurture.services.cadences import loader
    fake = tmp_path / "cadences"
    fake.mkdir()
    monkeypatch.setattr(loader, "USER_CONFIG_DIR", fake)
    return fake


def test_user_config_override_wins_over_library(override_dir, db_cleanup):
    from roost.extras.lead_nurture.services.cadences import (
        seed_library, seed_user_config, get_cadence_by_slug,
    )

    # 1. Library seed populates the row.
    seed_library()
    before = get_cadence_by_slug("financial_advisor_intro", user_id="")
    assert before is not None
    assert before["name"] == "Financial Advisor — Intro Sequence"
    assert before["source"] == "library"

    # 2. Drop an override with a different name.
    lib = Path(
        "roost/extras/lead_nurture/services/cadences/library/"
        "financial_advisor_intro.yaml"
    )
    override = override_dir / "financial_advisor_intro.yaml"
    shutil.copy(lib, override)
    override.write_text(
        override.read_text().replace(
            "Financial Advisor — Intro Sequence",
            "Custom Name From roost-config",
        )
    )

    # 3. Apply the override.
    result = seed_user_config()
    assert result["seeded"] == 1
    assert not result["errors"]

    after = get_cadence_by_slug("financial_advisor_intro", user_id="")
    assert after["name"] == "Custom Name From roost-config"
    assert after["source"] == "user"


def test_user_config_seed_no_dir_no_op(monkeypatch, tmp_path):
    from roost.extras.lead_nurture.services.cadences import loader, seed_user_config
    monkeypatch.setattr(loader, "USER_CONFIG_DIR", tmp_path / "missing")
    result = seed_user_config()
    assert result == {"seeded": 0, "skipped": 0, "errors": []}
