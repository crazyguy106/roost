"""Tests for the IRAS stamp-duty calculator.

Reference figures cross-checked against IRAS published examples and the
post-27-Apr-2023 ABSD table. The point of this calculator is to keep
agents from quoting stale rates (e.g. PR 2nd property at the old 25%).
"""

from __future__ import annotations

import pytest

from roost.services import iras_stamp_duty as iras


# ── BSD ──────────────────────────────────────────────────────────────


def test_bsd_residential_known_examples():
    # IRAS worked example: $1,000,000 residential.
    # 1% × 180k + 2% × 180k + 3% × 640k = 1,800 + 3,600 + 19,200 = 24,600
    assert iras.calc_bsd(1_000_000) == 24_600


def test_bsd_residential_top_tier_six_percent():
    # $4M residential — should hit the 6% band on the slice above $3M.
    # 1% × 180k = 1,800
    # 2% × 180k = 3,600
    # 3% × 640k = 19,200
    # 4% × 500k = 20,000
    # 5% × 1.5M = 75,000
    # 6% × 1M  = 60,000
    # Total    = 179,600
    assert iras.calc_bsd(4_000_000) == 179_600


def test_bsd_zero_for_zero_price():
    assert iras.calc_bsd(0) == 0


def test_bsd_non_residential_uses_different_brackets():
    # $2M non-res:
    # 1% × 180k = 1,800
    # 2% × 180k = 3,600
    # 3% × 640k = 19,200
    # 4% × 640k = 25,600
    # 5% × 360k = 18,000
    # Total    = 68,200
    assert iras.calc_bsd(2_000_000, residential=False) == 68_200


# ── ABSD ─────────────────────────────────────────────────────────────


def test_absd_sc_first_property_zero():
    duty, rate = iras.calc_absd(1_000_000, "sc", 1)
    assert duty == 0 and rate == "0%"


def test_absd_sc_second_property_20pct():
    duty, rate = iras.calc_absd(1_000_000, "sc", 2)
    assert duty == 200_000 and rate == "20%"


def test_absd_sc_third_property_30pct():
    duty, rate = iras.calc_absd(1_000_000, "sc", 3)
    assert duty == 300_000 and rate == "30%"


def test_absd_pr_first_property_5pct():
    duty, rate = iras.calc_absd(1_000_000, "spr", 1)
    assert duty == 50_000 and rate == "5%"


def test_absd_pr_second_property_30pct_not_25pct():
    """The whole point of this tool — guard against the stale 25% figure."""
    duty, rate = iras.calc_absd(1_000_000, "spr", 2)
    assert duty == 300_000 and rate == "30%"


def test_absd_foreign_60pct_flat():
    duty, rate = iras.calc_absd(1_000_000, "foreign", 1)
    assert duty == 600_000 and rate == "60%"


def test_absd_entity_65pct_flat():
    duty, rate = iras.calc_absd(1_000_000, "entity", 1)
    assert duty == 650_000 and rate == "65%"


def test_absd_trustee_65pct_flat():
    duty, rate = iras.calc_absd(1_000_000, "trustee", 1)
    assert duty == 650_000 and rate == "65%"


def test_absd_fta_exemption_treats_foreigner_as_sc():
    duty, rate = iras.calc_absd(1_000_000, "foreign", 1, fta_exempt=True)
    assert duty == 0 and rate == "0%"


def test_absd_unknown_profile_raises():
    with pytest.raises(ValueError, match="unknown buyer profile"):
        iras.calc_absd(1_000_000, "alien", 1)


# ── SSD ──────────────────────────────────────────────────────────────


def test_ssd_within_one_year_12pct():
    duty, rate = iras.calc_ssd(1_500_000, holding_period_months=6)
    assert duty == 180_000 and rate == "12%"


def test_ssd_year_one_to_two_8pct():
    duty, rate = iras.calc_ssd(1_500_000, holding_period_months=18)
    assert duty == 120_000 and rate == "8%"


def test_ssd_year_two_to_three_4pct():
    duty, rate = iras.calc_ssd(1_500_000, holding_period_months=30)
    assert duty == 60_000 and rate == "4%"


def test_ssd_after_three_years_zero():
    duty, rate = iras.calc_ssd(1_500_000, holding_period_months=40)
    assert duty == 0 and rate == "0%"


# ── Lease duty ───────────────────────────────────────────────────────


def test_lease_duty_short_lease():
    # AAR 36k, 24 months → 0.4% × 72,000 = 288
    assert iras.calc_lease_duty(annual_rent=36_000, lease_term_months=24) == 288


def test_lease_duty_long_lease_capped_at_four_years():
    # AAR 36k, 60 months → capped at 4× AAR = 144,000 → 0.4% = 576
    assert iras.calc_lease_duty(annual_rent=36_000, lease_term_months=60) == 576


# ── Combined buyer total ─────────────────────────────────────────────


def test_buyer_total_pr_second_property():
    """PR buying $1.5M as their 2nd home — common agent scenario."""
    result = iras.calc_buyer_total(1_500_000, "spr", 2)
    # BSD: 1% × 180k + 2% × 180k + 3% × 640k + 4% × 500k = 44,600
    assert result.bsd == 44_600
    assert result.absd == 450_000  # 30% × 1.5M
    assert result.total == 494_600
    assert any("30%" in n for n in result.notes)


def test_buyer_total_non_residential_no_absd():
    result = iras.calc_buyer_total(2_000_000, "foreign", 1, residential=False)
    assert result.absd == 0
    assert any("Non-residential" in n for n in result.notes)
