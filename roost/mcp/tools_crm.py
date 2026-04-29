"""MCP tools: CRM-agnostic adapter.

These tools speak to whichever CRM is configured via CRM_PROVIDER:
attio | hubspot | zoho | salesforce | pipedrive | local.

Recipes that drive these tools are portable across CRM backends — only
the CRM_PROVIDER env var needs to change.
"""

from __future__ import annotations

import logging
from typing import Any

from roost.mcp.server import mcp
from roost.services.crm import (
    CrmAuthError,
    CrmConfigError,
    CrmError,
    CrmNotFoundError,
    get_provider,
)

logger = logging.getLogger("roost.mcp.tools_crm")


def _safe(fn):
    """Wrap a CRM call so failures become {"error": ...} instead of raising."""
    def _w(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except CrmConfigError as e:
            return {"error": f"CRM not configured: {e}"}
        except CrmAuthError as e:
            return {"error": f"CRM auth rejected: {e}"}
        except CrmNotFoundError as e:
            return {"error": f"CRM record not found: {e}"}
        except CrmError as e:
            return {"error": f"CRM error: {e}"}
        except Exception as e:  # noqa: BLE001
            logger.exception("crm tool failed")
            return {"error": f"unexpected: {e}"}
    return _w


@mcp.tool()
def crm_test_connection() -> dict:
    """Ping the active CRM provider. Returns {ok, detail, provider}."""
    crm = get_provider()
    out = _safe(crm.test_connection)()
    if isinstance(out, dict):
        out.setdefault("provider", crm.name)
    return out


@mcp.tool()
def crm_find_person(email: str | None = None, phone: str | None = None) -> dict:
    """Find the first person in the active CRM by email or phone."""
    crm = get_provider()
    p = _safe(crm.find_person)(email=email, phone=phone)
    if isinstance(p, dict):  # error
        return p
    return p.as_dict() if p else {"error": "not found"}


@mcp.tool()
def crm_get_person(person_id: str) -> dict:
    """Fetch a person by CRM record id."""
    crm = get_provider()
    p = _safe(crm.get_person)(person_id)
    return p if isinstance(p, dict) else p.as_dict()


@mcp.tool()
def crm_search_people(query: str, limit: int = 20) -> dict:
    """Free-text search for people in the active CRM."""
    crm = get_provider()
    out = _safe(crm.search_people)(query, limit=limit)
    if isinstance(out, dict):
        return out
    return {"results": [p.as_dict() for p in out]}


@mcp.tool()
def crm_create_person(
    name: str | None = None,
    emails: list[str] | None = None,
    phones: list[str] | None = None,
    organization_id: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Create a person record. `extra` carries vendor-specific attributes."""
    crm = get_provider()
    p = _safe(crm.create_person)(
        name=name, emails=emails, phones=phones,
        organization_id=organization_id, **(extra or {}),
    )
    return p if isinstance(p, dict) else p.as_dict()


@mcp.tool()
def crm_update_person(person_id: str, fields: dict) -> dict:
    """Patch a person. `fields` keys may be vendor-specific."""
    crm = get_provider()
    p = _safe(crm.update_person)(person_id, **(fields or {}))
    return p if isinstance(p, dict) else p.as_dict()


@mcp.tool()
def crm_list_deals(person_id: str | None = None, stage: str | None = None,
                   limit: int = 50) -> dict:
    """List deals, optionally filtered by person or stage."""
    crm = get_provider()
    out = _safe(crm.list_deals)(person_id=person_id, stage=stage, limit=limit)
    if isinstance(out, dict):
        return out
    return {"deals": [d.as_dict() for d in out]}


@mcp.tool()
def crm_create_deal(
    name: str,
    stage: str | None = None,
    value: float | None = None,
    currency: str | None = None,
    person_id: str | None = None,
    organization_id: str | None = None,
    extra: dict | None = None,
) -> dict:
    """Create a deal/opportunity."""
    crm = get_provider()
    kw: dict[str, Any] = dict(extra or {})
    if currency:
        kw["currency"] = currency
    d = _safe(crm.create_deal)(
        name=name, stage=stage, value=value,
        person_id=person_id, organization_id=organization_id, **kw,
    )
    return d if isinstance(d, dict) else d.as_dict()


@mcp.tool()
def crm_move_deal_stage(deal_id: str, stage: str) -> dict:
    """Move a deal to a new stage."""
    crm = get_provider()
    d = _safe(crm.move_deal_stage)(deal_id, stage)
    return d if isinstance(d, dict) else d.as_dict()


@mcp.tool()
def crm_append_note(content: str, person_id: str | None = None,
                    deal_id: str | None = None, title: str | None = None) -> dict:
    """Attach a note to a person or deal. Returns {note_id} or {error}."""
    crm = get_provider()
    nid = _safe(crm.append_note)(person_id=person_id, deal_id=deal_id,
                                  content=content, title=title)
    return nid if isinstance(nid, dict) else {"note_id": nid}


@mcp.tool()
def crm_log_communication(
    person_id: str,
    channel: str,
    direction: str,
    content: str,
    subject: str | None = None,
) -> dict:
    """Log a comms event (whatsapp|telegram|email|sms|call, inbound|outbound)."""
    crm = get_provider()
    aid = _safe(crm.log_communication)(
        person_id=person_id, channel=channel, direction=direction,
        content=content, subject=subject,
    )
    return aid if isinstance(aid, dict) else {"activity_id": aid}


@mcp.tool()
def crm_set_custom_field(key: str, value: Any, person_id: str | None = None,
                          deal_id: str | None = None) -> dict:
    """Set a vendor-defined custom attribute on a person or deal."""
    crm = get_provider()
    out = _safe(crm.set_custom_field)(
        person_id=person_id, deal_id=deal_id, key=key, value=value,
    )
    if isinstance(out, dict):
        return out
    return {"ok": True}
