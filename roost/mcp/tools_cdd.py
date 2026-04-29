"""MCP tool: CDD screening (sanctions / PEP / adverse media).

Mandatory under CEA PC 01-21 / 02-23 for property agents. Result lands
in the deal record as a timestamped evidence artefact.
"""

from __future__ import annotations

import logging

from roost.config import CDD_ENABLED
from roost.mcp.server import mcp
from roost.services import cdd_screening

logger = logging.getLogger("roost.mcp.tools_cdd")


@mcp.tool()
def cdd_screen(
    name: str,
    dob: str | None = None,
    nationality: str | None = None,
    id_number: str | None = None,
) -> dict:
    """Screen a person against sanctions, PEP, and adverse-media lists.

    Required for AML/CFT Customer Due Diligence under CEA Practice
    Circulars 01-21 and 02-23. Cache the result for `expires_at` (default
    30 days) before re-screening.

    Args:
        name: Full legal name as on official ID.
        dob: Date of birth, ISO format "YYYY-MM-DD". Optional but strongly
            recommended for common names.
        nationality: ISO alpha-2 country code (e.g. "SG", "MY").
        id_number: NRIC / FIN / passport — stored on the search record
            for audit reference, not used as a search key.

    Returns:
        {
            "ok": True,
            "matched": bool,                  # any non-false-positive hit
            "risk_score": float (0.0-1.0),    # 0 clean, 1 confirmed sanction
            "vendor": "complyadvantage",
            "search_id": "...",               # vendor-side audit reference
            "screened_at": ISO8601,
            "expires_at": ISO8601,
            "hits": [{name, types, match_status, score, sources}, ...]
        }
        or {"error": "..."} if disabled / misconfigured / vendor down.
    """
    if not CDD_ENABLED:
        return {"error": "CDD adapter disabled (set CDD_ENABLED=true)"}
    try:
        return cdd_screening.screen(
            name=name, dob=dob, nationality=nationality, id_number=id_number
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
