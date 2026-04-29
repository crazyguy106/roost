"""MCP tools wrapping IRAS stamp-duty calculations.

For Singapore property agents and their clients. Pure-Python rate tables
(see `roost/services/iras_stamp_duty.py`) — no IRAS account, no portal.
"""

from __future__ import annotations

import logging

from roost.mcp.server import mcp
from roost.services import iras_stamp_duty as iras

logger = logging.getLogger("roost.mcp.tools_iras")


@mcp.tool()
def iras_calc_buyer_stamp_duty(
    price: float,
    profile: str,
    property_count: int,
    residential: bool = True,
    fta_exempt: bool = False,
) -> dict:
    """Compute BSD + ABSD on a Singapore property purchase.

    Args:
        price: Higher of consideration price or market value, in SGD.
        profile: Buyer profile — one of "sc", "spr", "foreign", "entity", "trustee".
        property_count: Count *including* this purchase (e.g. PR buying their
            2nd residential → 2). Foreign / entity / trustee rates are flat,
            so this only matters for SC and SPR.
        residential: True for residential property (BSD residential schedule
            + ABSD applies). False for commercial / industrial (no ABSD).
        fta_exempt: Set True for nationals of the 5 FTA-exempt countries
            (USA, Iceland, Liechtenstein, Norway, Switzerland) buying as
            individuals — they're taxed at SC rates.

    Returns:
        {"ok": True, "bsd": int, "absd": int, "total": int, "notes": [str], "profile": str, "property_count": int}
        or {"error": "..."} on bad input.
    """
    try:
        result = iras.calc_buyer_total(
            price=price,
            profile=profile,
            property_count=property_count,
            residential=residential,
            fta_exempt=fta_exempt,
        )
    except ValueError as e:
        return {"error": str(e)}
    out = result.as_dict()
    out["ok"] = True
    out["profile"] = profile.lower()
    out["property_count"] = property_count
    return out


@mcp.tool()
def iras_calc_seller_stamp_duty(price: float, holding_period_months: int) -> dict:
    """Compute SSD on a residential resale.

    Args:
        price: Sale price (or market value, whichever higher), in SGD.
        holding_period_months: Months between purchase and sale.

    Returns:
        {"ok": True, "ssd": int, "rate": str, "tier": str}
        Tiers: "≤1yr 12%", ">1–2yr 8%", ">2–3yr 4%", ">3yr 0%".
    """
    duty, rate = iras.calc_ssd(price, holding_period_months)
    years = holding_period_months / 12
    if years <= 1:
        tier = "≤1yr"
    elif years <= 2:
        tier = ">1–2yr"
    elif years <= 3:
        tier = ">2–3yr"
    else:
        tier = ">3yr"
    return {"ok": True, "ssd": duty, "rate": rate, "tier": f"{tier} {rate}"}


@mcp.tool()
def iras_calc_lease_stamp_duty(annual_rent: float, lease_term_months: int) -> dict:
    """Compute tenancy / lease stamp duty.

    Rate is 0.4% of total rent (capped at 4× annual rent for leases > 4 years).

    Args:
        annual_rent: AAR — average annual rent in SGD.
        lease_term_months: Total lease term in months.

    Returns:
        {"ok": True, "duty": int, "basis": str}
    """
    duty = iras.calc_lease_duty(annual_rent, lease_term_months)
    years = lease_term_months / 12
    basis = (
        f"0.4% × {annual_rent:.0f} × {years:.2f}yr"
        if years <= 4
        else f"0.4% × {annual_rent:.0f} × 4yr (cap)"
    )
    return {"ok": True, "duty": duty, "basis": basis}
