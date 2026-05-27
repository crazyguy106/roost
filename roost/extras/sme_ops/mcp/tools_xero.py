"""MCP tools: Xero (read-only, Phase 1A of SME Ops)."""

from __future__ import annotations

import logging

from roost.mcp.server import mcp
from roost.extras.sme_ops.services.xero import XeroClient

logger = logging.getLogger("roost.mcp.tools_xero")


@mcp.tool()
def xero_list_invoices(limit: int = 50) -> dict:
    """List Xero invoices with totals, due dates, and contact references."""
    out = XeroClient().list_invoices(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "invoices": out}


@mcp.tool()
def xero_list_contacts(limit: int = 50) -> dict:
    """List Xero contacts (customers + suppliers)."""
    out = XeroClient().list_contacts(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "contacts": out}


@mcp.tool()
def xero_list_bank_transactions(limit: int = 50) -> dict:
    """List Xero bank transactions with reconciliation state."""
    out = XeroClient().list_bank_transactions(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "transactions": out}


@mcp.tool()
def xero_create_invoice(
    contact_id: str,
    line_items: list,
    due_date: str | None = None,
    status: str = "DRAFT",
    type_: str = "ACCREC",
) -> dict:
    """Create a Xero invoice.

    `line_items`: list of `{description, quantity, unit_amount, account_code?}`.
    `status`: DRAFT | SUBMITTED | AUTHORISED.
       - DRAFT executes immediately (no notification, fully reversible).
       - SUBMITTED / AUTHORISED route through Guardian as a pending draft
         requiring human approval (an authorised invoice can trigger a
         customer email and locks the document).
    `type_`: ACCREC (sales — money in) | ACCPAY (bill — money out).
    """
    from roost.services.guardian import guardian_gate
    args = {
        "contact_id": contact_id, "line_items": line_items,
        "due_date": due_date, "status": status, "type_": type_,
    }
    gated = guardian_gate("xero_create_invoice", args)
    if gated is not None:
        return gated
    out = XeroClient().create_invoice(
        contact_id, line_items, due_date=due_date,
        status=status, type_=type_,
    )
    if "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "invoice": out}
