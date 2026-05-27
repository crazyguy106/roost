"""SME Ops bundle — Zapier ingress + native Stripe / Shopify / Xero adapters.

Mounts:
- 5 API routers: zapier, stripe, shopify, xero, sme_drafts
- 3 page routes: /sme/{sync-status,orders,cashflow}
- 3 MCP tool modules: tools_stripe, tools_shopify, tools_xero
- 2 bundle-owned tables: sme_ops_events, xero_oauth_tokens

Gated by `SME_OPS_ENABLED`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request

from roost.extras._base import Bundle

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger("roost.extras.sme_ops")

_BUNDLE_DIR = Path(__file__).resolve().parent


def _build_pages_router():
    """Build /sme/* page router with bundle templates on the Jinja path.
    Per-app-create so routes don't leak between test apps."""
    from fastapi import APIRouter, HTTPException
    from jinja2 import ChoiceLoader, FileSystemLoader

    from roost.web.pages import _base_context, templates as core_templates

    bundle_loader = FileSystemLoader(str(_BUNDLE_DIR / "templates"))
    existing = core_templates.env.loader
    if isinstance(existing, ChoiceLoader):
        if bundle_loader not in existing.loaders:
            existing.loaders = list(existing.loaders) + [bundle_loader]
    else:
        core_templates.env.loader = ChoiceLoader([existing, bundle_loader])

    pages = APIRouter()

    def _require_enabled():
        from roost.config import SME_OPS_ENABLED
        if not SME_OPS_ENABLED:
            raise HTTPException(status_code=404, detail="SME Ops bundle disabled")

    @pages.get("/sme/sync-status")
    def sme_ops_sync_status(request: Request):
        _require_enabled()
        from roost.config import (
            SHOPIFY_ACCESS_TOKEN, SHOPIFY_ENABLED, SHOPIFY_STORE_DOMAIN,
            SHOPIFY_WEBHOOK_SECRET,
            STRIPE_API_KEY, STRIPE_ENABLED, STRIPE_WEBHOOK_SECRET,
            XERO_ENABLED, XERO_PAT, XERO_TENANT_ID,
            ZAPIER_ENABLED, ZAPIER_INGRESS_TOKEN, ZAPIER_OUTBOUND_URL,
        )
        from roost.services.guardian import list_pending_drafts
        from roost.extras.sme_ops.services.zapier import recent_stats
        return core_templates.TemplateResponse("sme_ops/sync_status.html", {
            **_base_context(request),
            "active_tab": "",
            "page_title": "SME Ops — Sync Status",
            "zapier_enabled": ZAPIER_ENABLED,
            "zapier_ingress_configured": bool(ZAPIER_INGRESS_TOKEN),
            "zapier_outbound_configured": bool(ZAPIER_OUTBOUND_URL),
            "zapier_stats": recent_stats("zapier"),
            "stripe_enabled": STRIPE_ENABLED,
            "stripe_api_configured": bool(STRIPE_API_KEY),
            "stripe_webhook_configured": bool(STRIPE_WEBHOOK_SECRET),
            "stripe_stats": recent_stats("stripe"),
            "shopify_enabled": SHOPIFY_ENABLED,
            "shopify_api_configured": bool(SHOPIFY_STORE_DOMAIN and SHOPIFY_ACCESS_TOKEN),
            "shopify_webhook_configured": bool(SHOPIFY_WEBHOOK_SECRET),
            "shopify_stats": recent_stats("shopify"),
            "xero_enabled": XERO_ENABLED,
            "xero_configured": bool(XERO_PAT and XERO_TENANT_ID),
            "pending_drafts": list_pending_drafts(limit=25),
        })

    @pages.get("/sme/orders")
    def sme_ops_orders(request: Request):
        _require_enabled()
        from roost.extras.sme_ops.services.shopify import ShopifyClient
        from roost.extras.sme_ops.services.stripe import StripeClient

        rows: list[dict] = []
        sources: list[dict] = []

        sc = ShopifyClient()
        if sc.is_configured():
            out = sc.list_orders(limit=25)
            if isinstance(out, list):
                for o in out:
                    rows.append({
                        "source": "shopify",
                        "ref": o.get("name") or str(o.get("id")),
                        "customer": o.get("email") or "",
                        "amount": o.get("total"),
                        "currency": o.get("currency"),
                        "status": o.get("financial_status") or "",
                        "fulfillment": o.get("fulfillment_status") or "",
                        "created_at": o.get("created_at"),
                    })
                sources.append({"name": "Shopify", "count": len(out)})
            else:
                sources.append({"name": "Shopify", "error": out.get("error")})
        else:
            sources.append({"name": "Shopify", "configured": False})

        pc = StripeClient()
        if pc.is_configured():
            out = pc.list_charges(limit=25)
            if isinstance(out, list):
                for c in out:
                    rows.append({
                        "source": "stripe",
                        "ref": c.get("id"),
                        "customer": c.get("customer_id") or "",
                        "amount": c.get("amount"),
                        "currency": c.get("currency"),
                        "status": c.get("status") or "",
                        "fulfillment": "",
                        "created_at": c.get("created_at"),
                    })
                sources.append({"name": "Stripe", "count": len(out)})
            else:
                sources.append({"name": "Stripe", "error": out.get("error")})
        else:
            sources.append({"name": "Stripe", "configured": False})

        rows.sort(key=lambda r: (r.get("created_at") or 0), reverse=True)
        return core_templates.TemplateResponse("sme_ops/orders.html", {
            **_base_context(request),
            "active_tab": "",
            "page_title": "SME Ops — Orders",
            "rows": rows,
            "sources": sources,
        })

    @pages.get("/sme/cashflow")
    def sme_ops_cashflow(request: Request):
        _require_enabled()
        from roost.extras.sme_ops.services.stripe import StripeClient
        from roost.extras.sme_ops.services.xero import XeroClient

        summary: list[dict] = []
        invoices: list[dict] = []
        charges: list[dict] = []
        bank_txns: list[dict] = []

        xc = XeroClient()
        if xc.is_configured():
            inv = xc.list_invoices(limit=25)
            if isinstance(inv, list):
                invoices = inv
                money_in = sum((i.get("amount_paid") or 0) for i in inv)
                money_out = sum((i.get("total") or 0) - (i.get("amount_paid") or 0)
                                for i in inv if i.get("type") == "ACCPAY")
                summary.append({
                    "source": "Xero invoices",
                    "in": money_in, "out": money_out,
                    "net": money_in - money_out,
                })
            bt = xc.list_bank_transactions(limit=25)
            if isinstance(bt, list):
                bank_txns = bt
        else:
            summary.append({"source": "Xero", "configured": False})

        pc = StripeClient()
        if pc.is_configured():
            ch = pc.list_charges(limit=50)
            if isinstance(ch, list):
                charges = ch
                money_in = sum((c.get("amount") or 0) for c in ch
                               if c.get("status") == "succeeded" and c.get("paid"))
                summary.append({
                    "source": "Stripe charges",
                    "in": money_in, "out": 0,
                    "net": money_in,
                })
        else:
            summary.append({"source": "Stripe", "configured": False})

        return core_templates.TemplateResponse("sme_ops/cashflow.html", {
            **_base_context(request),
            "active_tab": "",
            "page_title": "SME Ops — Cashflow",
            "summary": summary,
            "invoices": invoices,
            "charges": charges,
            "bank_txns": bank_txns,
        })

    return pages


def _register(app: "FastAPI", mcp) -> None:  # noqa: ARG001
    # MCP tools (decorators self-register on import).
    from roost.extras.sme_ops.mcp import tools_shopify  # noqa: F401
    from roost.extras.sme_ops.mcp import tools_stripe  # noqa: F401
    from roost.extras.sme_ops.mcp import tools_xero  # noqa: F401

    # API routers.
    from roost.extras.sme_ops.web.api_shopify import router as shopify_router
    from roost.extras.sme_ops.web.api_sme_drafts import router as sme_drafts_router
    from roost.extras.sme_ops.web.api_stripe import router as stripe_router
    from roost.extras.sme_ops.web.api_xero import router as xero_router
    from roost.extras.sme_ops.web.api_zapier import router as zapier_router

    app.include_router(zapier_router)
    app.include_router(stripe_router)
    app.include_router(shopify_router)
    app.include_router(xero_router)
    app.include_router(sme_drafts_router)
    app.include_router(_build_pages_router())


def _schema_sql() -> str:
    """Bundle-owned tables: sme_ops_events (event audit log) +
    xero_oauth_tokens (per-tenant OAuth2 tokens)."""
    return """
    -- SME Ops bundle: audit log of inbound Zapier (and future adapter) events.
    CREATE TABLE IF NOT EXISTS sme_ops_events (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        source       TEXT NOT NULL,
        event        TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        received_at  TEXT NOT NULL DEFAULT (datetime('now')),
        processed_at TEXT,
        status       TEXT NOT NULL DEFAULT 'received'
                      CHECK (status IN ('received', 'processed', 'failed'))
    );
    CREATE INDEX IF NOT EXISTS idx_sme_ops_events_source ON sme_ops_events(source);
    CREATE INDEX IF NOT EXISTS idx_sme_ops_events_received ON sme_ops_events(received_at);

    -- Xero OAuth2 tokens (one row per connected tenant).
    CREATE TABLE IF NOT EXISTS xero_oauth_tokens (
        tenant_id     TEXT PRIMARY KEY,
        access_token  TEXT NOT NULL,
        refresh_token TEXT NOT NULL,
        expires_at    TEXT NOT NULL,
        scope         TEXT NOT NULL DEFAULT '',
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """


BUNDLE = Bundle(
    name="sme_ops",
    flag_name="SME_OPS_ENABLED",
    register=_register,
    schema_sql=_schema_sql,
)
