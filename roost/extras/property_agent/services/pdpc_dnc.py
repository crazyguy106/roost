"""PDPC Do-Not-Call (DNC) Registry scrub.

Singapore's PDPA forbids sending marketing voice calls / SMS / fax to
numbers registered on the DNC. Organisations must scrub their contact
list against the registry no more than 21 days before the send.

This module wraps the DNC Registry B2B API. It does *not* automate the
registration / API-key issuance flow with PDPC — that's a one-off
manual step (org applies, PDPC issues key + ORG_ID).

Three registers exist; pass any subset to `check`:
- "DNC_NoVoiceCall"  — no marketing voice calls
- "DNC_NoTextMessage" — no marketing SMS / MMS
- "DNC_NoFax"         — no marketing fax

A number that returns `registers_blocked = []` is clean for marketing
on the registers checked. Cache the result with `expires_at = now + 21 days`
and re-scrub before that lapses.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from roost.config import DNC_API_BASE_URL, DNC_API_KEY, DNC_ENABLED, DNC_ORG_ID

logger = logging.getLogger("roost.extras.property_agent.services.pdpc_dnc")

REGISTERS = ("DNC_NoVoiceCall", "DNC_NoTextMessage", "DNC_NoFax")
SCRUB_VALIDITY_DAYS = 21

_SG_NUMBER = re.compile(r"^(?:\+?65)?(\d{8})$")


class DncConfigError(RuntimeError):
    """Raised when the DNC adapter is called without credentials configured."""


def _normalise(number: str) -> str:
    """Return an 8-digit local SG number, stripping `+65` / spaces / dashes."""
    cleaned = re.sub(r"[\s\-]", "", number)
    m = _SG_NUMBER.match(cleaned)
    if not m:
        raise ValueError(f"not a valid Singapore mobile/landline: {number!r}")
    return m.group(1)


def _ensure_configured() -> None:
    if not DNC_ENABLED:
        raise DncConfigError("DNC adapter disabled (set DNC_ENABLED=true)")
    if not DNC_API_KEY or not DNC_ORG_ID:
        raise DncConfigError("DNC_API_KEY and DNC_ORG_ID must be set")


def check(numbers: list[str], registers: list[str] | None = None) -> dict:
    """Scrub a batch of numbers against the DNC Registry.

    Args:
        numbers: List of Singapore phone numbers in any common format
            (`+6591234567`, `91234567`, `9123 4567` all accepted).
        registers: Subset of REGISTERS to check. Defaults to all three.

    Returns:
        {
            "ok": True,
            "scrubbed_at": ISO8601 UTC,
            "expires_at":  ISO8601 UTC (scrubbed_at + 21 days),
            "results": [
                {
                    "number": "91234567",
                    "input": "+6591234567",
                    "registers_blocked": ["DNC_NoTextMessage", ...],
                    "allowed": False,  # True if registers_blocked is empty
                },
                ...
            ],
            "registers_checked": [...],
        }
    """
    _ensure_configured()
    registers = list(registers) if registers else list(REGISTERS)
    bad = [r for r in registers if r not in REGISTERS]
    if bad:
        raise ValueError(f"unknown register(s): {bad}; expected {REGISTERS}")

    pairs: list[tuple[str, str]] = []  # (input, normalised)
    for n in numbers:
        pairs.append((n, _normalise(n)))

    payload = {
        "OrgId": DNC_ORG_ID,
        "Registers": registers,
        "PhoneNumbers": [n for _, n in pairs],
    }
    headers = {
        "Authorization": f"Bearer {DNC_API_KEY}",
        "Content-Type": "application/json",
    }

    resp = httpx.post(
        f"{DNC_API_BASE_URL}/scrub",
        json=payload,
        headers=headers,
        timeout=20.0,
    )
    resp.raise_for_status()
    body = resp.json()

    by_number: dict[str, list[str]] = {n: [] for _, n in pairs}
    for entry in body.get("results", []):
        num = str(entry.get("phoneNumber", "")).lstrip("+").lstrip("65")
        if num in by_number:
            by_number[num] = list(entry.get("registersBlocked", []))

    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=SCRUB_VALIDITY_DAYS)

    return {
        "ok": True,
        "scrubbed_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "registers_checked": registers,
        "results": [
            {
                "input": original,
                "number": num,
                "registers_blocked": by_number.get(num, []),
                "allowed": not by_number.get(num),
            }
            for original, num in pairs
        ],
    }
