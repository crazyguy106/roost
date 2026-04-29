"""Customer Due Diligence (CDD) screening.

Mandatory for Singapore property agents under CEA Practice Circulars
01-21 and 02-23 (AML/CFT). Each buyer / tenant must be screened against:
- **Sanctions lists** (UN, OFAC, EU, MAS targeted financial sanctions)
- **PEPs** (Politically Exposed Persons + close associates / family)
- **Adverse media** (negative news indicating financial crime risk)

This module exposes a vendor-agnostic interface (`Provider`) so we can
swap ComplyAdvantage ↔ Acuris (Dow Jones) ↔ Refinitiv World-Check
without touching call sites. Today only ComplyAdvantage is wired.

Each successful screen returns an evidence record with
`expires_at = screened_at + CDD_REFRESH_DAYS` (default 30). Persist that
on the deal record — the audit trail is the whole point.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from roost.config import (
    CDD_API_BASE_URL,
    CDD_API_KEY,
    CDD_ENABLED,
    CDD_REFRESH_DAYS,
    CDD_VENDOR,
)

logger = logging.getLogger("roost.services.cdd_screening")


class CddConfigError(RuntimeError):
    """CDD adapter not configured (missing key / unknown vendor / disabled)."""


# ── Vendor-agnostic types ───────────────────────────────────────────


@dataclass
class Hit:
    """One candidate match against a sanctions / PEP / adverse-media list."""
    name: str
    types: list[str]                # e.g. ["sanction", "pep", "adverse-media"]
    match_status: str               # "potential_match" | "true_positive" | "false_positive" | "unknown"
    score: float                    # 0.0–1.0; vendor-specific calibration
    sources: list[str] = field(default_factory=list)   # list names (e.g. "OFAC SDN")
    raw: dict[str, Any] = field(default_factory=dict)  # vendor-native payload


@dataclass
class ScreeningResult:
    matched: bool
    hits: list[Hit]
    risk_score: float                # 0.0 (clean) – 1.0 (high risk)
    screened_at: datetime
    expires_at: datetime
    vendor: str
    search_id: str = ""              # vendor-side reference for audit retrieval


class Provider(ABC):
    """A CDD vendor wrapper."""

    @abstractmethod
    def screen(
        self,
        name: str,
        *,
        dob: str | None = None,
        nationality: str | None = None,
        id_number: str | None = None,
    ) -> ScreeningResult:
        ...


# ── ComplyAdvantage ──────────────────────────────────────────────────


_COMPLY_DEFAULT_BASE = "https://api.complyadvantage.com"
_RISK_TYPE_WEIGHTS = {
    "sanction": 1.0,
    "warning": 0.8,
    "fitness-probity": 0.6,
    "pep": 0.5,
    "pep-class-1": 0.6,
    "pep-class-2": 0.5,
    "pep-class-3": 0.4,
    "pep-class-4": 0.3,
    "adverse-media": 0.3,
}


class ComplyAdvantageProvider(Provider):
    def __init__(self, api_key: str, base_url: str = ""):
        if not api_key:
            raise CddConfigError("CDD_API_KEY not set")
        self.api_key = api_key
        self.base_url = (base_url or _COMPLY_DEFAULT_BASE).rstrip("/")

    def screen(
        self,
        name: str,
        *,
        dob: str | None = None,
        nationality: str | None = None,
        id_number: str | None = None,
    ) -> ScreeningResult:
        filters: dict[str, Any] = {
            "types": ["sanction", "warning", "fitness-probity", "pep", "adverse-media"],
        }
        if dob:
            try:
                filters["birth_year"] = int(dob.split("-", 1)[0])
            except (ValueError, AttributeError):
                pass
        if nationality:
            filters["country_codes"] = [nationality.upper()]

        payload = {
            "search_term": name,
            "fuzziness": 0.6,
            "filters": filters,
        }
        # id_number isn't a first-class CA filter — append to search term to bias matching.
        if id_number:
            payload["client_ref"] = id_number

        resp = httpx.post(
            f"{self.base_url}/searches",
            params={"api_key": self.api_key},
            json=payload,
            timeout=30.0,
        )
        resp.raise_for_status()
        body = resp.json()
        return self._parse(body)

    @staticmethod
    def _parse(body: dict) -> ScreeningResult:
        content = body.get("content", {}) or {}
        data = content.get("data", {}) or content
        raw_hits: list[dict] = data.get("hits", []) or []
        search_id = str(data.get("id", "") or data.get("ref", ""))

        hits: list[Hit] = []
        max_weight = 0.0
        for h in raw_hits:
            doc = h.get("doc", {}) or {}
            types = list(doc.get("types", []) or [])
            sources = list(doc.get("sources", []) or [])
            score = float(h.get("score", 0.0) or 0.0)
            status = h.get("match_status", "unknown") or "unknown"
            hits.append(Hit(
                name=doc.get("name", "") or "",
                types=types,
                match_status=status,
                score=score,
                sources=sources,
                raw=h,
            ))
            for t in types:
                w = _RISK_TYPE_WEIGHTS.get(t, 0.2)
                if status == "true_positive":
                    w *= 1.0
                elif status == "potential_match":
                    w *= 0.7
                elif status == "false_positive":
                    w *= 0.0
                max_weight = max(max_weight, w * max(score, 0.5))

        now = datetime.now(timezone.utc)
        return ScreeningResult(
            matched=any(h.match_status != "false_positive" for h in hits),
            hits=hits,
            risk_score=round(min(max_weight, 1.0), 3),
            screened_at=now,
            expires_at=now + timedelta(days=CDD_REFRESH_DAYS),
            vendor="complyadvantage",
            search_id=search_id,
        )


# ── Dispatch ────────────────────────────────────────────────────────


def _get_provider() -> Provider:
    if not CDD_ENABLED:
        raise CddConfigError("CDD adapter disabled (set CDD_ENABLED=true)")
    vendor = CDD_VENDOR.lower()
    if vendor == "complyadvantage":
        return ComplyAdvantageProvider(api_key=CDD_API_KEY, base_url=CDD_API_BASE_URL)
    if vendor in {"acuris", "refinitiv"}:
        raise NotImplementedError(f"CDD vendor {vendor!r} not yet implemented")
    raise CddConfigError(f"unknown CDD vendor: {vendor!r}")


def screen(
    name: str,
    *,
    dob: str | None = None,
    nationality: str | None = None,
    id_number: str | None = None,
) -> dict:
    """Public entry point. Returns a JSON-safe dict for MCP / web callers.

    Args:
        name: Full legal name as it appears on official ID.
        dob: ISO date "YYYY-MM-DD". Used to disambiguate common names.
        nationality: ISO 3166-1 alpha-2 country code (e.g. "SG").
        id_number: NRIC / FIN / passport. Stored as `client_ref` for audit.
    """
    provider = _get_provider()
    if not name or not name.strip():
        raise ValueError("name is required")
    result = provider.screen(name=name.strip(), dob=dob, nationality=nationality, id_number=id_number)
    return {
        "ok": True,
        "matched": result.matched,
        "risk_score": result.risk_score,
        "vendor": result.vendor,
        "search_id": result.search_id,
        "screened_at": result.screened_at.isoformat(),
        "expires_at": result.expires_at.isoformat(),
        "hits": [
            {
                "name": h.name,
                "types": h.types,
                "match_status": h.match_status,
                "score": h.score,
                "sources": h.sources,
            }
            for h in result.hits
        ],
    }
