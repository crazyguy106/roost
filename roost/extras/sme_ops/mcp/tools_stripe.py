"""MCP tools: Stripe (read-only, Phase 1A of SME Ops)."""

from __future__ import annotations

import logging

from roost.mcp.server import mcp
from roost.extras.sme_ops.services.stripe import StripeClient

logger = logging.getLogger("roost.mcp.tools_stripe")


@mcp.tool()
def stripe_list_charges(limit: int = 50) -> dict:
    """List recent Stripe charges (most recent first).

    Returns `{ok, charges: [{id, amount, currency, status, paid,
    customer_id, created_at, description}, ...]}`. Amounts are in major
    units (e.g. dollars, not cents). Fails closed if STRIPE_ENABLED=false
    or STRIPE_API_KEY is unset.
    """
    out = StripeClient().list_charges(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "charges": out}


@mcp.tool()
def stripe_list_subscriptions(limit: int = 50) -> dict:
    """List active Stripe subscriptions."""
    out = StripeClient().list_subscriptions(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "subscriptions": out}


@mcp.tool()
def stripe_list_customers(limit: int = 50) -> dict:
    """List Stripe customers."""
    out = StripeClient().list_customers(limit=limit)
    if isinstance(out, dict) and "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "customers": out}


@mcp.tool()
def stripe_create_refund(
    charge_id: str,
    amount: float | None = None,
    reason: str | None = None,
) -> dict:
    """Draft a Stripe refund. Routes through Guardian — the call is
    persisted as a pending draft and only executes after a human
    approves it via the dashboard or `/api/sme/drafts/<id>/approve`.

    `amount` in major units; omit for full refund.
    `reason` may be: duplicate, fraudulent, requested_by_customer.
    """
    from roost.services.guardian import guardian_gate
    args = {"charge_id": charge_id, "amount": amount, "reason": reason}
    gated = guardian_gate("stripe_create_refund", args)
    if gated is not None:
        return gated
    out = StripeClient().create_refund(charge_id, amount=amount, reason=reason)
    if "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "refund": out}


@mcp.tool()
def stripe_create_payment_link(
    amount: float,
    currency: str,
    description: str | None = None,
) -> dict:
    """Create a one-off Stripe payment link. Returns a shareable URL."""
    out = StripeClient().create_payment_link(amount, currency, description=description)
    if "error" in out:
        return {"ok": False, **out}
    return {"ok": True, "payment_link": out}
