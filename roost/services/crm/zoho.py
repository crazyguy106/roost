"""Zoho CRM provider.

Uses the v2 REST API. Auth is OAuth — we keep a refresh token and mint a
fresh access token (1 hour TTL) on demand. The access token is cached in
memory; refresh-token is persisted via the credentials service.

Docs: https://www.zoho.com/crm/developer/docs/api/v2/
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx

from roost.services.crm.base import (
    CrmAuthError,
    CrmConfigError,
    CrmError,
    CrmNotFoundError,
    CrmProvider,
    Deal,
    Org,
    Person,
)

logger = logging.getLogger("roost.services.crm.zoho")


def _cred(key: str, env: str) -> str:
    try:
        from roost.services.credentials import get_credential
        v = get_credential(key)
        if v:
            return v
    except Exception:
        pass
    return os.getenv(env, "")


class ZohoProvider(CrmProvider):
    name = "zoho"

    def __init__(self) -> None:
        self.client_id = _cred("ZOHO_CLIENT_ID", "ZOHO_CLIENT_ID")
        self.client_secret = _cred("ZOHO_CLIENT_SECRET", "ZOHO_CLIENT_SECRET")
        self.refresh_token = _cred("ZOHO_REFRESH_TOKEN", "ZOHO_REFRESH_TOKEN")
        self.accounts_url = os.getenv("ZOHO_ACCOUNTS_URL", "https://accounts.zoho.com").rstrip("/")
        self.api_domain = os.getenv("ZOHO_API_DOMAIN", "https://www.zohoapis.com").rstrip("/")
        self._access_token: str | None = None
        self._access_exp: float = 0.0

    def _ensure_config(self) -> None:
        if not (self.client_id and self.client_secret and self.refresh_token):
            raise CrmConfigError("Zoho not configured (need ZOHO_CLIENT_ID/SECRET/REFRESH_TOKEN)")

    def _access(self) -> str:
        self._ensure_config()
        now = time.time()
        if self._access_token and now < self._access_exp - 60:
            return self._access_token
        try:
            r = httpx.post(f"{self.accounts_url}/oauth/v2/token", params={
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            }, timeout=20.0)
        except httpx.HTTPError as e:
            raise CrmError(f"Zoho token refresh failed: {e}") from e
        if r.status_code >= 400:
            raise CrmAuthError(f"Zoho refresh rejected: {r.status_code} {r.text[:200]}")
        d = r.json()
        if "access_token" not in d:
            raise CrmAuthError(f"Zoho refresh: no access_token in {d}")
        self._access_token = d["access_token"]
        self._access_exp = now + int(d.get("expires_in", 3600))
        if d.get("api_domain"):
            self.api_domain = d["api_domain"].rstrip("/")
        return self._access_token

    def _request(self, method: str, path: str, *, json=None, params=None) -> dict:
        url = f"{self.api_domain}/crm/v2{path}"
        headers = {"Authorization": f"Zoho-oauthtoken {self._access()}",
                   "Content-Type": "application/json"}
        try:
            r = httpx.request(method, url, headers=headers, json=json, params=params, timeout=30.0)
        except httpx.HTTPError as e:
            raise CrmError(f"Zoho request failed: {e}") from e
        if r.status_code in (401, 403):
            raise CrmAuthError(f"Zoho rejected auth: {r.status_code} {r.text[:200]}")
        if r.status_code == 404:
            raise CrmNotFoundError(f"Zoho 404: {path}")
        if r.status_code == 204 or not r.content:
            return {}
        if r.status_code >= 400:
            raise CrmError(f"Zoho {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError as e:
            raise CrmError(f"Zoho returned non-JSON: {e}") from e

    def test_connection(self) -> dict:
        try:
            self._access()
            self._request("GET", "/Contacts", params={"per_page": 1})
            return {"ok": True, "detail": "Connected to Zoho CRM"}
        except CrmError as e:
            return {"ok": False, "detail": str(e)}

    @staticmethod
    def _person_from_zoho(d: dict) -> Person:
        name = d.get("Full_Name") or " ".join(
            x for x in [d.get("First_Name"), d.get("Last_Name")] if x).strip() or None
        emails = [d["Email"]] if d.get("Email") else []
        phones = [d[k] for k in ("Phone", "Mobile") if d.get(k)]
        org_id = None
        acct = d.get("Account_Name")
        if isinstance(acct, dict):
            org_id = acct.get("id")
        return Person(id=str(d.get("id", "")), name=name, emails=emails, phones=phones,
                      organization_id=org_id, raw=d)

    @staticmethod
    def _org_from_zoho(d: dict) -> Org:
        return Org(id=str(d.get("id", "")), name=d.get("Account_Name"),
                   domain=d.get("Website"), raw=d)

    @staticmethod
    def _deal_from_zoho(d: dict) -> Deal:
        amount = d.get("Amount")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        return Deal(id=str(d.get("id", "")), name=d.get("Deal_Name"), stage=d.get("Stage"),
                    value=amount, raw=d)

    def find_person(self, *, email=None, phone=None):
        criteria = []
        if email:
            criteria.append(f"(Email:equals:{email})")
        if phone:
            criteria.append(f"(Phone:equals:{phone})")
        if not criteria:
            return None
        q = "or".join(criteria) if len(criteria) > 1 else criteria[0]
        data = self._request("GET", "/Contacts/search", params={"criteria": q})
        items = data.get("data") or []
        return self._person_from_zoho(items[0]) if items else None

    def get_person(self, person_id: str) -> Person:
        data = self._request("GET", f"/Contacts/{person_id}")
        items = data.get("data") or []
        if not items:
            raise CrmNotFoundError(f"Zoho contact {person_id} not found")
        return self._person_from_zoho(items[0])

    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        data = self._request("GET", "/Contacts/search",
                             params={"word": query, "per_page": limit})
        return [self._person_from_zoho(x) for x in (data.get("data") or [])]

    def create_person(self, *, name=None, emails=None, phones=None, organization_id=None, **extra):
        rec: dict[str, Any] = dict(extra)
        if name:
            parts = name.split(" ", 1)
            rec["First_Name"] = parts[0]
            rec["Last_Name"] = parts[1] if len(parts) > 1 else parts[0]
        if emails:
            rec["Email"] = emails[0]
        if phones:
            rec["Phone"] = phones[0]
        if organization_id:
            rec["Account_Name"] = {"id": organization_id}
        data = self._request("POST", "/Contacts", json={"data": [rec]})
        out = (data.get("data") or [{}])[0]
        new_id = (out.get("details") or {}).get("id") or out.get("id")
        return self.get_person(new_id) if new_id else Person(id="", raw=data)

    def update_person(self, person_id: str, **fields):
        rec: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name" and v:
                parts = v.split(" ", 1)
                rec["First_Name"] = parts[0]
                rec["Last_Name"] = parts[1] if len(parts) > 1 else parts[0]
            elif k == "email":
                rec["Email"] = v
            elif k == "phone":
                rec["Phone"] = v
            else:
                rec[k] = v
        self._request("PUT", f"/Contacts/{person_id}", json={"data": [rec]})
        return self.get_person(person_id)

    def find_org(self, *, domain=None, name=None):
        criteria = []
        if domain:
            criteria.append(f"(Website:equals:{domain})")
        if name:
            criteria.append(f"(Account_Name:equals:{name})")
        if not criteria:
            return None
        q = "or".join(criteria) if len(criteria) > 1 else criteria[0]
        data = self._request("GET", "/Accounts/search", params={"criteria": q})
        items = data.get("data") or []
        return self._org_from_zoho(items[0]) if items else None

    def create_org(self, *, name: str, domain=None, **extra):
        rec = {"Account_Name": name, **extra}
        if domain:
            rec["Website"] = domain
        data = self._request("POST", "/Accounts", json={"data": [rec]})
        out = (data.get("data") or [{}])[0]
        new_id = (out.get("details") or {}).get("id") or out.get("id")
        return Org(id=str(new_id or ""), name=name, domain=domain, raw=data)

    def list_deals(self, *, person_id=None, stage=None, limit: int = 50):
        params = {"per_page": limit}
        if stage:
            data = self._request("GET", "/Deals/search",
                                 params={"criteria": f"(Stage:equals:{stage})", "per_page": limit})
        elif person_id:
            data = self._request("GET", f"/Contacts/{person_id}/Deals", params=params)
        else:
            data = self._request("GET", "/Deals", params=params)
        return [self._deal_from_zoho(x) for x in (data.get("data") or [])]

    def create_deal(self, *, name: str, stage=None, value=None, person_id=None,
                    organization_id=None, **extra):
        rec: dict[str, Any] = {"Deal_Name": name, **extra}
        if stage:
            rec["Stage"] = stage
        if value is not None:
            rec["Amount"] = value
        if person_id:
            rec["Contact_Name"] = {"id": person_id}
        if organization_id:
            rec["Account_Name"] = {"id": organization_id}
        data = self._request("POST", "/Deals", json={"data": [rec]})
        out = (data.get("data") or [{}])[0]
        new_id = (out.get("details") or {}).get("id") or out.get("id")
        return Deal(id=str(new_id or ""), name=name, stage=stage, value=value, raw=data)

    def update_deal(self, deal_id: str, **fields):
        rec: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name":
                rec["Deal_Name"] = v
            elif k == "stage":
                rec["Stage"] = v
            elif k == "value":
                rec["Amount"] = v
            else:
                rec[k] = v
        self._request("PUT", f"/Deals/{deal_id}", json={"data": [rec]})
        d = self._request("GET", f"/Deals/{deal_id}")
        items = d.get("data") or []
        return self._deal_from_zoho(items[0]) if items else Deal(id=deal_id, raw=d)

    def move_deal_stage(self, deal_id: str, stage: str):
        return self.update_deal(deal_id, stage=stage)

    def append_note(self, *, person_id=None, deal_id=None, content: str, title=None):
        if person_id:
            parent_id, parent_module = person_id, "Contacts"
        elif deal_id:
            parent_id, parent_module = deal_id, "Deals"
        else:
            raise ValueError("append_note requires person_id or deal_id")
        rec = {
            "Note_Title": title or "Roost note",
            "Note_Content": content,
            "Parent_Id": {"id": parent_id, "module": {"api_name": parent_module}},
        }
        data = self._request("POST", "/Notes", json={"data": [rec]})
        out = (data.get("data") or [{}])[0]
        return str((out.get("details") or {}).get("id") or out.get("id") or "")

    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at=None, subject=None):
        ts = (occurred_at or datetime.utcnow()).isoformat(timespec="seconds") + "Z"
        title = f"[{channel}.{direction}] {subject or ts}"
        return self.append_note(person_id=person_id, content=content, title=title)

    def set_custom_field(self, *, person_id=None, deal_id=None, key: str, value: Any) -> None:
        if person_id:
            self._request("PUT", f"/Contacts/{person_id}", json={"data": [{key: value}]})
        elif deal_id:
            self._request("PUT", f"/Deals/{deal_id}", json={"data": [{key: value}]})
        else:
            raise ValueError("set_custom_field requires person_id or deal_id")
