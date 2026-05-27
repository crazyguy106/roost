"""Stripe adapter — read-only MVP (Phase 1A).

Wraps a handful of Stripe REST endpoints (api.stripe.com/v1) using the
secret key in `STRIPE_API_KEY`. Returns normalized dicts so callers don't
depend on Stripe's wire shape.

Phase 1B will add: refunds, subscription cancels, customer create/update,
and proper pagination via `starting_after` cursors.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from roost.config import STRIPE_API_KEY, STRIPE_ENABLED

logger = logging.getLogger("roost.extras.sme_ops.services.stripe")

DEFAULT_BASE_URL = "https://api.stripe.com/v1"


class StripeClient:
    """Thin httpx wrapper around Stripe's read endpoints."""

    def __init__(
        self, api_key: str = "", *,
        base_url: str = DEFAULT_BASE_URL,
        enabled: bool | None = None,
    ):
        self.api_key = (api_key or STRIPE_API_KEY).strip()
        self.base_url = base_url.rstrip("/")
        self.enabled = STRIPE_ENABLED if enabled is None else enabled

    def is_configured(self) -> bool:
        return bool(self.enabled and self.api_key)

    # ── HTTP plumbing ─────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> dict:
        if not self.is_configured():
            return {"error": "stripe_disabled"}
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=params or {},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code >= 400:
                return {
                    "error": f"stripe_http_{resp.status_code}",
                    "detail": resp.text[:500],
                }
            return resp.json()
        except Exception as e:
            logger.warning("Stripe GET %s failed: %s", path, e)
            return {"error": "stripe_request_failed", "detail": str(e)}

    def _post(self, path: str, form: dict) -> dict:
        if not self.is_configured():
            return {"error": "stripe_disabled"}
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.post(
                    f"{self.base_url}/{path.lstrip('/')}",
                    data=form,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                )
            if resp.status_code >= 400:
                return {
                    "error": f"stripe_http_{resp.status_code}",
                    "detail": resp.text[:500],
                }
            return resp.json()
        except Exception as e:
            logger.warning("Stripe POST %s failed: %s", path, e)
            return {"error": "stripe_request_failed", "detail": str(e)}

    # ── Read endpoints ────────────────────────────────────────────────

    def list_charges(self, limit: int = 50) -> list[dict] | dict:
        body = self._get("charges", {"limit": min(limit, 100)})
        if "error" in body:
            return body
        return [self._normalize_charge(c) for c in body.get("data", [])]

    def list_subscriptions(self, limit: int = 50) -> list[dict] | dict:
        body = self._get("subscriptions", {"limit": min(limit, 100)})
        if "error" in body:
            return body
        return [self._normalize_subscription(s) for s in body.get("data", [])]

    def list_customers(self, limit: int = 50) -> list[dict] | dict:
        body = self._get("customers", {"limit": min(limit, 100)})
        if "error" in body:
            return body
        return [self._normalize_customer(c) for c in body.get("data", [])]

    # ── Write endpoints ───────────────────────────────────────────────

    def create_refund(
        self,
        charge_id: str,
        amount: float | None = None,
        reason: str | None = None,
    ) -> dict:
        """Refund a charge in full (amount=None) or partially (amount in major units)."""
        if not charge_id:
            return {"error": "charge_id_required"}
        form: dict = {"charge": charge_id}
        if amount is not None:
            form["amount"] = int(round(amount * 100))
        if reason:
            form["reason"] = reason
        body = self._post("refunds", form)
        if "error" in body:
            return body
        return {
            "id": body.get("id"),
            "charge_id": body.get("charge"),
            "amount": (body.get("amount") or 0) / 100,
            "currency": (body.get("currency") or "").upper(),
            "status": body.get("status"),
            "reason": body.get("reason"),
            "created_at": body.get("created"),
        }

    def create_payment_link(
        self,
        amount: float,
        currency: str,
        description: str | None = None,
    ) -> dict:
        """Create a one-off payment link.

        Stripe requires a Price, so we inline-create one via line_items.
        """
        form: dict = {
            "line_items[0][quantity]": 1,
            "line_items[0][price_data][currency]": currency.lower(),
            "line_items[0][price_data][unit_amount]": int(round(amount * 100)),
            "line_items[0][price_data][product_data][name]": description or "Payment",
        }
        body = self._post("payment_links", form)
        if "error" in body:
            return body
        return {
            "id": body.get("id"),
            "url": body.get("url"),
            "active": body.get("active"),
        }

    # ── Normalizers ───────────────────────────────────────────────────

    @staticmethod
    def _normalize_charge(c: dict) -> dict:
        return {
            "id": c.get("id"),
            "amount": (c.get("amount") or 0) / 100,
            "currency": (c.get("currency") or "").upper(),
            "status": c.get("status"),
            "paid": c.get("paid"),
            "customer_id": c.get("customer"),
            "created_at": c.get("created"),
            "description": c.get("description"),
        }

    @staticmethod
    def _normalize_subscription(s: dict) -> dict:
        return {
            "id": s.get("id"),
            "status": s.get("status"),
            "customer_id": s.get("customer"),
            "current_period_end": s.get("current_period_end"),
            "cancel_at_period_end": s.get("cancel_at_period_end"),
        }

    @staticmethod
    def _normalize_customer(c: dict) -> dict:
        return {
            "id": c.get("id"),
            "email": c.get("email"),
            "name": c.get("name"),
            "balance": (c.get("balance") or 0) / 100,
            "created_at": c.get("created"),
        }
