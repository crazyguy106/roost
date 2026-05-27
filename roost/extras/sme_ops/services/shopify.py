"""Shopify adapter — read-only MVP (Phase 1A).

Custom-app admin API: pass `SHOPIFY_STORE_DOMAIN` (e.g. `mystore.myshopify.com`)
and `SHOPIFY_ACCESS_TOKEN` (header `X-Shopify-Access-Token`).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from roost.config import (
    SHOPIFY_ACCESS_TOKEN,
    SHOPIFY_ENABLED,
    SHOPIFY_STORE_DOMAIN,
)

logger = logging.getLogger("roost.extras.sme_ops.services.shopify")

API_VERSION = "2024-04"


class ShopifyClient:
    def __init__(
        self, store_domain: str = "", access_token: str = "",
        *, enabled: bool | None = None,
    ):
        self.store_domain = (store_domain or SHOPIFY_STORE_DOMAIN).strip()
        self.access_token = (access_token or SHOPIFY_ACCESS_TOKEN).strip()
        self.enabled = SHOPIFY_ENABLED if enabled is None else enabled

    @property
    def base_url(self) -> str:
        return f"https://{self.store_domain}/admin/api/{API_VERSION}"

    def is_configured(self) -> bool:
        return bool(self.enabled and self.store_domain and self.access_token)

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.is_configured():
            return {"error": "shopify_disabled"}
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=params or {},
                    headers={"X-Shopify-Access-Token": self.access_token},
                )
            if resp.status_code >= 400:
                return {
                    "error": f"shopify_http_{resp.status_code}",
                    "detail": resp.text[:500],
                }
            return resp.json()
        except Exception as e:
            logger.warning("Shopify GET %s failed: %s", path, e)
            return {"error": "shopify_request_failed", "detail": str(e)}

    def _post(self, path: str, payload: dict) -> dict:
        if not self.is_configured():
            return {"error": "shopify_disabled"}
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"{self.base_url}/{path.lstrip('/')}",
                    json=payload,
                    headers={
                        "X-Shopify-Access-Token": self.access_token,
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code >= 400:
                return {
                    "error": f"shopify_http_{resp.status_code}",
                    "detail": resp.text[:500],
                }
            return resp.json()
        except Exception as e:
            logger.warning("Shopify POST %s failed: %s", path, e)
            return {"error": "shopify_request_failed", "detail": str(e)}

    def list_orders(self, limit: int = 50, status: str = "any") -> list | dict:
        body = self._get("orders.json", {
            "limit": min(limit, 250), "status": status,
        })
        if "error" in body:
            return body
        return [self._normalize_order(o) for o in body.get("orders", [])]

    def list_products(self, limit: int = 50) -> list | dict:
        body = self._get("products.json", {"limit": min(limit, 250)})
        if "error" in body:
            return body
        return [self._normalize_product(p) for p in body.get("products", [])]

    def list_customers(self, limit: int = 50) -> list | dict:
        body = self._get("customers.json", {"limit": min(limit, 250)})
        if "error" in body:
            return body
        return [self._normalize_customer(c) for c in body.get("customers", [])]

    # ── Write endpoints ───────────────────────────────────────────────

    def fulfill_order(
        self,
        order_id: int | str,
        tracking_number: str | None = None,
        tracking_company: str | None = None,
        notify_customer: bool = True,
    ) -> dict:
        """Create a fulfillment for all line items in the order.

        Uses the `/fulfillments.json` endpoint (2024-04 API). Caller may
        pass tracking metadata; omit to fulfil without tracking.
        """
        if not order_id:
            return {"error": "order_id_required"}
        fulfillment: dict = {
            "message": "Fulfilled via Roost",
            "notify_customer": notify_customer,
            "line_items_by_fulfillment_order": [],
        }
        # Fetch fulfillment orders first (Shopify requires them for new API)
        fos = self._get(f"orders/{order_id}/fulfillment_orders.json")
        if "error" in fos:
            return fos
        fo_list = fos.get("fulfillment_orders") or []
        if not fo_list:
            return {"error": "no_fulfillment_orders"}
        fulfillment["line_items_by_fulfillment_order"] = [
            {"fulfillment_order_id": fo["id"]} for fo in fo_list
        ]
        if tracking_number or tracking_company:
            fulfillment["tracking_info"] = {
                "number": tracking_number,
                "company": tracking_company,
            }
        body = self._post("fulfillments.json", {"fulfillment": fulfillment})
        if "error" in body:
            return body
        f = body.get("fulfillment") or {}
        return {
            "id": f.get("id"),
            "order_id": f.get("order_id"),
            "status": f.get("status"),
            "tracking_number": f.get("tracking_number"),
            "created_at": f.get("created_at"),
        }

    def cancel_order(
        self,
        order_id: int | str,
        reason: str = "other",
        refund: bool = False,
    ) -> dict:
        """Cancel an order. `reason` ∈ customer/fraud/inventory/declined/other.

        If `refund=True`, Shopify will issue a refund where possible.
        """
        if not order_id:
            return {"error": "order_id_required"}
        payload = {"reason": reason, "refund": refund}
        body = self._post(f"orders/{order_id}/cancel.json", payload)
        if "error" in body:
            return body
        o = body.get("order") or {}
        return {
            "id": o.get("id"),
            "name": o.get("name"),
            "cancelled_at": o.get("cancelled_at"),
            "cancel_reason": o.get("cancel_reason"),
            "financial_status": o.get("financial_status"),
        }

    @staticmethod
    def _normalize_order(o: dict) -> dict:
        return {
            "id": o.get("id"),
            "name": o.get("name"),
            "email": o.get("email"),
            "total": float(o.get("total_price") or 0),
            "currency": o.get("currency"),
            "financial_status": o.get("financial_status"),
            "fulfillment_status": o.get("fulfillment_status"),
            "created_at": o.get("created_at"),
            "line_items": len(o.get("line_items") or []),
        }

    @staticmethod
    def _normalize_product(p: dict) -> dict:
        variants = p.get("variants") or []
        return {
            "id": p.get("id"),
            "title": p.get("title"),
            "status": p.get("status"),
            "vendor": p.get("vendor"),
            "variants": len(variants),
            "inventory_total": sum(int(v.get("inventory_quantity") or 0)
                                   for v in variants),
        }

    @staticmethod
    def _normalize_customer(c: dict) -> dict:
        return {
            "id": c.get("id"),
            "email": c.get("email"),
            "name": f"{c.get('first_name') or ''} {c.get('last_name') or ''}".strip(),
            "orders_count": c.get("orders_count"),
            "total_spent": float(c.get("total_spent") or 0),
        }
