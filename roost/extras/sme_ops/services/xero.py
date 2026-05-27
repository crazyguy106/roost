"""Xero adapter (Phase 1A read + Phase 1B writes/OAuth).

Auth precedence:
  1. OAuth2 tokens stored in `xero_oauth_tokens` (set up via
     /api/xero/oauth/{start,callback}) — auto-refreshed on use.
  2. Personal Access Token (`XERO_PAT` + `XERO_TENANT_ID`) — fallback for
     single-user instances that haven't run the OAuth dance.

API: https://api.xero.com/api.xro/2.0
"""

from __future__ import annotations

import logging

import httpx

from roost.config import XERO_ENABLED, XERO_PAT, XERO_TENANT_ID
from roost.extras.sme_ops.services import xero_oauth

logger = logging.getLogger("roost.extras.sme_ops.services.xero")

DEFAULT_BASE_URL = "https://api.xero.com/api.xro/2.0"


class XeroClient:
    def __init__(
        self, pat: str = "", tenant_id: str = "",
        *, base_url: str = DEFAULT_BASE_URL, enabled: bool | None = None,
    ):
        self.pat = (pat or XERO_PAT).strip()
        self.tenant_id = (tenant_id or XERO_TENANT_ID).strip()
        self.base_url = base_url.rstrip("/")
        self.enabled = XERO_ENABLED if enabled is None else enabled

    def is_configured(self) -> bool:
        if not self.enabled:
            return False
        if self.pat and self.tenant_id:
            return True
        return xero_oauth.access_token_for() is not None

    def _auth(self) -> tuple[str, str] | None:
        """Return (token, tenant_id) using OAuth if available else PAT."""
        tok = xero_oauth.access_token_for(self.tenant_id or None)
        if tok:
            return tok
        if self.pat and self.tenant_id:
            return self.pat, self.tenant_id
        return None

    def _request(
        self, method: str, path: str,
        params: dict | None = None, json: dict | None = None,
    ) -> dict:
        if not self.enabled:
            return {"error": "xero_disabled"}
        auth = self._auth()
        if not auth:
            return {"error": "xero_disabled"}
        token, tenant = auth
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    params=params or {},
                    json=json,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Xero-Tenant-Id": tenant,
                        "Accept": "application/json",
                    },
                )
            if resp.status_code >= 400:
                return {
                    "error": f"xero_http_{resp.status_code}",
                    "detail": resp.text[:500],
                }
            return resp.json()
        except Exception as e:
            logger.warning("Xero %s %s failed: %s", method, path, e)
            return {"error": "xero_request_failed", "detail": str(e)}

    def _get(self, path: str, params: dict | None = None) -> dict:
        return self._request("GET", path, params=params)

    def list_invoices(self, limit: int = 50) -> list | dict:
        body = self._get("Invoices", {"page": 1})
        if "error" in body:
            return body
        return [self._normalize_invoice(i)
                for i in body.get("Invoices", [])[:limit]]

    def list_contacts(self, limit: int = 50) -> list | dict:
        body = self._get("Contacts", {"page": 1})
        if "error" in body:
            return body
        return [self._normalize_contact(c)
                for c in body.get("Contacts", [])[:limit]]

    def list_bank_transactions(self, limit: int = 50) -> list | dict:
        body = self._get("BankTransactions", {"page": 1})
        if "error" in body:
            return body
        return [self._normalize_bank_txn(t)
                for t in body.get("BankTransactions", [])[:limit]]

    # ── Write endpoints (Phase 1B, OAuth-only) ───────────────────────

    def create_invoice(
        self,
        contact_id: str,
        line_items: list[dict],
        due_date: str | None = None,
        status: str = "DRAFT",
        type_: str = "ACCREC",
    ) -> dict:
        """Create an invoice.

        `line_items`: list of dicts with `description`, `quantity`,
        `unit_amount`, optional `account_code`.
        `status`: DRAFT | SUBMITTED | AUTHORISED.
        `type_`: ACCREC (sales) | ACCPAY (bills).
        """
        if not contact_id:
            return {"error": "contact_id_required"}
        if not line_items:
            return {"error": "line_items_required"}
        payload = {
            "Invoices": [{
                "Type": type_,
                "Contact": {"ContactID": contact_id},
                "LineItems": [
                    {
                        "Description": li.get("description", ""),
                        "Quantity": li.get("quantity", 1),
                        "UnitAmount": li.get("unit_amount", 0),
                        **({"AccountCode": li["account_code"]}
                           if li.get("account_code") else {}),
                    }
                    for li in line_items
                ],
                "Status": status,
                **({"DueDate": due_date} if due_date else {}),
            }]
        }
        body = self._request("POST", "Invoices", json=payload)
        if "error" in body:
            return body
        invoices = body.get("Invoices") or []
        if not invoices:
            return {"error": "no_invoice_returned"}
        return self._normalize_invoice(invoices[0])

    @staticmethod
    def _normalize_invoice(i: dict) -> dict:
        contact = i.get("Contact") or {}
        return {
            "id": i.get("InvoiceID"),
            "number": i.get("InvoiceNumber"),
            "type": i.get("Type"),
            "status": i.get("Status"),
            "total": i.get("Total"),
            "amount_due": i.get("AmountDue"),
            "amount_paid": i.get("AmountPaid"),
            "currency": i.get("CurrencyCode"),
            "due_date": i.get("DueDateString") or i.get("DueDate"),
            "contact_name": contact.get("Name"),
            "contact_id": contact.get("ContactID"),
        }

    @staticmethod
    def _normalize_contact(c: dict) -> dict:
        return {
            "id": c.get("ContactID"),
            "name": c.get("Name"),
            "email": c.get("EmailAddress"),
            "is_customer": c.get("IsCustomer"),
            "is_supplier": c.get("IsSupplier"),
        }

    @staticmethod
    def _normalize_bank_txn(t: dict) -> dict:
        return {
            "id": t.get("BankTransactionID"),
            "type": t.get("Type"),
            "status": t.get("Status"),
            "total": t.get("Total"),
            "currency": t.get("CurrencyCode"),
            "date": t.get("DateString") or t.get("Date"),
            "is_reconciled": t.get("IsReconciled"),
        }
