"""Web API for the Singapore property-agent toolkit.

Thin JSON wrappers around the IRAS / DNC / CDD service modules. The
calculator endpoints are pure compute — no auth gating beyond whatever
the rest of the app applies. The DNC and CDD endpoints fail closed if
the adapter isn't configured.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from roost.config import CDD_ENABLED, DNC_ENABLED, PROPERTY_AGENT_ENABLED
from roost.services import cdd_screening, iras_stamp_duty, pdpc_dnc

logger = logging.getLogger("roost.web.api_property_agent")

router = APIRouter(prefix="/api/property-agent", tags=["property-agent"])


def _require_enabled() -> None:
    if not PROPERTY_AGENT_ENABLED:
        raise HTTPException(status_code=404, detail="Property-agent toolkit disabled")


# ── Stamp duty (always available) ───────────────────────────────────


@router.post("/stamp-duty/buyer")
def api_buyer_stamp_duty(payload: dict[str, Any] = Body(...)) -> dict:
    _require_enabled()
    try:
        result = iras_stamp_duty.calc_buyer_total(
            price=float(payload.get("price", 0) or 0),
            profile=str(payload.get("profile", "sc")),
            property_count=int(payload.get("property_count", 1) or 1),
            residential=bool(payload.get("residential", True)),
            fta_exempt=bool(payload.get("fta_exempt", False)),
        )
    except (ValueError, TypeError) as e:
        return {"error": str(e)}
    out = result.as_dict()
    out["ok"] = True
    return out


@router.post("/stamp-duty/seller")
def api_seller_stamp_duty(payload: dict[str, Any] = Body(...)) -> dict:
    _require_enabled()
    price = float(payload.get("price", 0) or 0)
    months = int(payload.get("holding_period_months", 0) or 0)
    duty, rate = iras_stamp_duty.calc_ssd(price, months)
    years = months / 12
    if years <= 1:
        tier = "≤1yr"
    elif years <= 2:
        tier = ">1–2yr"
    elif years <= 3:
        tier = ">2–3yr"
    else:
        tier = ">3yr"
    return {"ok": True, "ssd": duty, "rate": rate, "tier": f"{tier} {rate}"}


@router.post("/stamp-duty/lease")
def api_lease_stamp_duty(payload: dict[str, Any] = Body(...)) -> dict:
    _require_enabled()
    rent = float(payload.get("annual_rent", 0) or 0)
    months = int(payload.get("lease_term_months", 0) or 0)
    duty = iras_stamp_duty.calc_lease_duty(rent, months)
    years = months / 12
    if years <= 4:
        basis = f"0.4% × {rent:.0f} × {years:.2f}yr"
    else:
        basis = f"0.4% × {rent:.0f} × 4yr (cap)"
    return {"ok": True, "duty": duty, "basis": basis}


# ── DNC scrub (gated) ───────────────────────────────────────────────


@router.post("/dnc/scrub")
def api_dnc_scrub(payload: dict[str, Any] = Body(...)) -> dict:
    _require_enabled()
    if not DNC_ENABLED:
        return {"error": "DNC adapter disabled (set DNC_ENABLED=true)"}
    numbers = payload.get("numbers") or []
    registers = payload.get("registers") or None
    if not isinstance(numbers, list) or not numbers:
        return {"error": "numbers must be a non-empty list"}
    try:
        return pdpc_dnc.check([str(n) for n in numbers], registers=registers)
    except pdpc_dnc.DncConfigError as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": f"input error: {e}"}
    except Exception as e:
        logger.exception("DNC scrub failed")
        return {"error": f"DNC scrub failed: {e}"}


# ── CDD screening (gated) ───────────────────────────────────────────


@router.post("/cdd/screen")
def api_cdd_screen(payload: dict[str, Any] = Body(...)) -> dict:
    _require_enabled()
    if not CDD_ENABLED:
        return {"error": "CDD adapter disabled (set CDD_ENABLED=true)"}
    name = (payload.get("name") or "").strip()
    if not name:
        return {"error": "name is required"}
    try:
        return cdd_screening.screen(
            name=name,
            dob=payload.get("dob") or None,
            nationality=payload.get("nationality") or None,
            id_number=payload.get("id_number") or None,
        )
    except cdd_screening.CddConfigError as e:
        return {"error": str(e)}
    except NotImplementedError as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": f"input error: {e}"}
    except Exception as e:
        logger.exception("CDD screen failed")
        return {"error": f"CDD screen failed: {e}"}
