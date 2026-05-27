"""MCP tools for PDPC compliance checks (Singapore).

Currently exposes the DNC Registry scrub. Future additions: PDPA breach
notification helpers, CDD lookup wrappers.
"""

from __future__ import annotations

import logging

from roost.config import DNC_ENABLED
from roost.mcp.server import mcp
from roost.extras.property_agent.services import pdpc_dnc

logger = logging.getLogger("roost.extras.property_agent.mcp.tools_pdpc")


@mcp.tool()
def pdpc_dnc_check(numbers: list[str], registers: list[str] | None = None) -> dict:
    """Scrub Singapore phone numbers against the PDPC Do-Not-Call registry.

    Required before sending marketing voice / SMS / fax. The scrub result is
    valid for 21 days under the Spam Control Act — store the `expires_at`
    field and re-scrub before that lapses.

    Args:
        numbers: Singapore phone numbers, any common format
            (`+6591234567`, `91234567`, `9123 4567` all OK).
        registers: Optional subset of
            `["DNC_NoVoiceCall", "DNC_NoTextMessage", "DNC_NoFax"]`.
            Defaults to all three.

    Returns:
        Per-number `{input, number, registers_blocked, allowed}` plus
        `scrubbed_at` and `expires_at`. A number with `allowed: True`
        is safe to message on the registers checked.

    Errors:
        Returns `{"error": "..."}` if `DNC_ENABLED` is false or
        `DNC_API_KEY` / `DNC_ORG_ID` aren't configured.
    """
    if not DNC_ENABLED:
        return {"error": "DNC adapter disabled (set DNC_ENABLED=true)"}
    try:
        return pdpc_dnc.check(numbers, registers)
    except pdpc_dnc.DncConfigError as e:
        return {"error": str(e)}
    except ValueError as e:
        return {"error": f"input error: {e}"}
    except Exception as e:
        logger.exception("DNC scrub failed")
        return {"error": f"DNC scrub failed: {e}"}
