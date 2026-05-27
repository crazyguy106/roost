"""MCP tools: Shopify (read-only, Phase 1A of SME Ops)."""

from __future__ import annotations

import logging

from roost.mcp.server import mcp
from roost.extras.sme_ops.services.shopify import ShopifyClient

logger = logging.getLogger("roost.mcp.tools_shopify")


@mcp.tool()
def shopify_list_orders(limit: int = 50, status: str = "any") -> dict:
    """List Shopify orders. `status` accepts open / closed / cancelled / any."""
    out = ShopifyClient().list_orders(limit=limit, status=status)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "orders": out}


@mcp.tool()
def shopify_list_products(limit: int = 50) -> dict:
    """List Shopify products with variant counts and inventory totals."""
    out = ShopifyClient().list_products(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "products": out}


@mcp.tool()
def shopify_list_customers(limit: int = 50) -> dict:
    """List Shopify customers."""
    out = ShopifyClient().list_customers(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "customers": out}


@mcp.tool()
def shopify_fulfill_order(
    order_id: int,
    tracking_number: str | None = None,
    tracking_company: str | None = None,
    notify_customer: bool = True,
) -> dict:
    """Mark a Shopify order as fulfilled (all line items)."""
    out = ShopifyClient().fulfill_order(
        order_id, tracking_number=tracking_number,
        tracking_company=tracking_company, notify_customer=notify_customer,
    )
    if "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "fulfillment": out}


@mcp.tool()
def shopify_cancel_order(
    order_id: int,
    reason: str = "other",
    refund: bool = False,
) -> dict:
    """Draft a Shopify order cancellation. Routes through Guardian — the
    call is persisted as a pending draft and only executes after a human
    approves it via the dashboard or `/api/sme/drafts/<id>/approve`.

    Set `refund=True` to also issue a refund as part of the cancellation.
    `reason` ∈ customer / fraud / inventory / declined / other.
    """
    from roost.services.guardian import guardian_gate
    args = {"order_id": order_id, "reason": reason, "refund": refund}
    gated = guardian_gate("shopify_cancel_order", args)
    if gated is not None:
        return gated
    out = ShopifyClient().cancel_order(order_id, reason=reason, refund=refund)
    if "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "order": out}
