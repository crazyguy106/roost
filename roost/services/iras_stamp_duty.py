"""IRAS stamp duty calculator (Singapore).

Pure-Python rate tables. No network, no IRAS account needed. Covers:
- BSD (Buyer's Stamp Duty) — residential and non-residential.
- ABSD (Additional Buyer's Stamp Duty) — post-27-Apr-2023 rates.
- SSD (Seller's Stamp Duty) — residential.
- Lease duty (tenancy stamping).

Buyer profiles for ABSD:
    "sc"       Singapore Citizen
    "spr"      Singapore Permanent Resident
    "foreign"  Foreigner (any nationality not covered by FTA exemption)
    "entity"   Singapore-incorporated company / trust (non-living-trust)
    "trustee"  Trustee acquiring on behalf of an identifiable beneficiary

FTA-exempt foreign nationals (US, Iceland, Liechtenstein, Norway, Switzerland)
are taxed at the SC rate; pass `fta_exempt=True` to apply that.

All amounts in SGD. Duty rounded down to the nearest dollar (IRAS convention).
"""

from __future__ import annotations

from dataclasses import dataclass

ABSD_EFFECTIVE_FROM = "2023-04-27"

_BSD_RESIDENTIAL = [
    (180_000, 0.01),
    (180_000, 0.02),
    (640_000, 0.03),
    (500_000, 0.04),
    (1_500_000, 0.05),
    (None,    0.06),
]

_BSD_NON_RESIDENTIAL = [
    (180_000, 0.01),
    (180_000, 0.02),
    (640_000, 0.03),
    (640_000, 0.04),
    (None,    0.05),
]

_ABSD_RATES = {
    "sc":      [0.00, 0.20, 0.30, 0.30, 0.30, 0.30, 0.30],
    "spr":     [0.05, 0.30, 0.35, 0.35, 0.35, 0.35, 0.35],
    "foreign": [0.60] * 7,
    "entity":  [0.65] * 7,
    "trustee": [0.65] * 7,
}

_SSD_RESIDENTIAL = [
    (1, 0.12),
    (2, 0.08),
    (3, 0.04),
]


@dataclass
class StampDutyBreakdown:
    bsd: int
    absd: int
    total: int
    notes: list[str]

    def as_dict(self) -> dict:
        return {"bsd": self.bsd, "absd": self.absd, "total": self.total, "notes": self.notes}


def _tiered_duty(amount: float, brackets: list[tuple[int | None, float]]) -> float:
    remaining = amount
    duty = 0.0
    for cap, rate in brackets:
        if remaining <= 0:
            break
        slice_ = remaining if cap is None else min(remaining, cap)
        duty += slice_ * rate
        remaining -= slice_
    return duty


def calc_bsd(price: float, *, residential: bool = True) -> int:
    """Buyer's Stamp Duty. `price` is the higher of consideration or market value."""
    if price <= 0:
        return 0
    brackets = _BSD_RESIDENTIAL if residential else _BSD_NON_RESIDENTIAL
    return int(_tiered_duty(price, brackets))


def calc_absd(
    price: float,
    profile: str,
    property_count: int,
    *,
    fta_exempt: bool = False,
) -> tuple[int, str]:
    """Additional Buyer's Stamp Duty (post-27 Apr 2023).

    `property_count` is the count *including* the new purchase
    (e.g. PR buying their second residential property → property_count=2).
    Returns (duty, applied_rate_label).
    """
    if price <= 0:
        return 0, "0%"
    profile = profile.lower()
    if profile not in _ABSD_RATES:
        raise ValueError(
            f"unknown buyer profile {profile!r}; expected one of {sorted(_ABSD_RATES)}"
        )
    if fta_exempt and profile == "foreign":
        rates = _ABSD_RATES["sc"]
    else:
        rates = _ABSD_RATES[profile]
    idx = max(1, min(property_count, len(rates))) - 1
    rate = rates[idx]
    return int(price * rate), f"{rate * 100:g}%"


def calc_ssd(price: float, holding_period_months: int) -> tuple[int, str]:
    """Seller's Stamp Duty. Residential property only, sold within 3 years."""
    if price <= 0 or holding_period_months < 0:
        return 0, "0%"
    years_held = holding_period_months / 12
    for cutoff_years, rate in _SSD_RESIDENTIAL:
        if years_held <= cutoff_years:
            return int(price * rate), f"{rate * 100:g}%"
    return 0, "0%"


def calc_lease_duty(annual_rent: float, lease_term_months: int) -> int:
    """Tenancy / lease stamp duty.

    For leases up to 4 years: 0.4% of total rent over the term.
    For leases > 4 years (or indefinite): 0.4% of 4× the average annual rent.
    """
    if annual_rent <= 0 or lease_term_months <= 0:
        return 0
    years = lease_term_months / 12
    if years <= 4:
        total_rent = annual_rent * years
    else:
        total_rent = annual_rent * 4
    return int(total_rent * 0.004)


def calc_buyer_total(
    price: float,
    profile: str,
    property_count: int,
    *,
    residential: bool = True,
    fta_exempt: bool = False,
) -> StampDutyBreakdown:
    """One-shot helper for the agent path: BSD + ABSD on a residential purchase."""
    bsd = calc_bsd(price, residential=residential)
    absd = 0
    notes: list[str] = []
    applied_rate = "0%"
    if residential:
        absd, applied_rate = calc_absd(
            price, profile, property_count, fta_exempt=fta_exempt
        )
        notes.append(f"ABSD applied at {applied_rate} ({profile.upper()}, property #{property_count})")
        if fta_exempt and profile == "foreign":
            notes.append("FTA exemption applied — taxed at Singapore Citizen rates")
    else:
        notes.append("Non-residential — no ABSD")
    notes.append(f"Rates effective from {ABSD_EFFECTIVE_FROM}")
    return StampDutyBreakdown(bsd=bsd, absd=absd, total=bsd + absd, notes=notes)
