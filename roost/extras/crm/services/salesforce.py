"""Salesforce CRM provider.

Uses the SObject REST API. Auth is OAuth — for v1 we accept a stored
access token + instance URL (refresh-token plumbing belongs in a future
auth flow). SOQL search via /query.

Docs: https://developer.salesforce.com/docs/atlas.en-us.api_rest.meta/api_rest/
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from roost.extras.crm.services.base import (
    CrmAuthError,
    CrmConfigError,
    CrmError,
    CrmNotFoundError,
    CrmProvider,
    Deal,
    Org,
    Person,
)

logger = logging.getLogger("roost.extras.crm.services.salesforce")


def _cred(key: str, env: str) -> str:
    try:
        from roost.services.credentials import get_credential
        v = get_credential(key)
        if v:
            return v
    except Exception:
        pass
    return os.getenv(env, "")


def _soql_quote(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


class SalesforceProvider(CrmProvider):
    name = "salesforce"

    def __init__(self) -> None:
        self.instance_url = (_cred("SALESFORCE_INSTANCE_URL", "SALESFORCE_INSTANCE_URL")
                             or "").rstrip("/")
        self.access_token = _cred("SALESFORCE_ACCESS_TOKEN", "SALESFORCE_ACCESS_TOKEN")
        self.api_version = os.getenv("SALESFORCE_API_VERSION", "v59.0")

    def _ensure(self) -> None:
        if not self.instance_url or not self.access_token:
            raise CrmConfigError("Salesforce not configured "
                                 "(set SALESFORCE_INSTANCE_URL and SALESFORCE_ACCESS_TOKEN)")

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json=None, params=None) -> dict:
        self._ensure()
        url = f"{self.instance_url}/services/data/{self.api_version}{path}"
        try:
            r = httpx.request(method, url, headers=self._headers(), json=json,
                              params=params, timeout=30.0)
        except httpx.HTTPError as e:
            raise CrmError(f"Salesforce request failed: {e}") from e
        if r.status_code in (401, 403):
            raise CrmAuthError(f"Salesforce rejected auth: {r.status_code} {r.text[:200]}")
        if r.status_code == 404:
            raise CrmNotFoundError(f"Salesforce 404: {path}")
        if r.status_code == 204 or not r.content:
            return {}
        if r.status_code >= 400:
            raise CrmError(f"Salesforce {r.status_code}: {r.text[:300]}")
        try:
            return r.json()
        except ValueError as e:
            raise CrmError(f"Salesforce returned non-JSON: {e}") from e

    def _query(self, soql: str) -> list[dict]:
        d = self._request("GET", "/query", params={"q": soql})
        return d.get("records") or []

    def test_connection(self) -> dict:
        try:
            self._query("SELECT Id FROM Contact LIMIT 1")
            return {"ok": True, "detail": f"Connected to {self.instance_url}"}
        except CrmError as e:
            return {"ok": False, "detail": str(e)}

    @staticmethod
    def _person_from_sf(d: dict) -> Person:
        name = d.get("Name") or " ".join(
            x for x in [d.get("FirstName"), d.get("LastName")] if x).strip() or None
        emails = [d["Email"]] if d.get("Email") else []
        phones = [d[k] for k in ("Phone", "MobilePhone") if d.get(k)]
        return Person(id=str(d.get("Id", "")), name=name, emails=emails, phones=phones,
                      organization_id=d.get("AccountId"), raw=d)

    @staticmethod
    def _org_from_sf(d: dict) -> Org:
        return Org(id=str(d.get("Id", "")), name=d.get("Name"), domain=d.get("Website"), raw=d)

    @staticmethod
    def _deal_from_sf(d: dict) -> Deal:
        amount = d.get("Amount")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        return Deal(id=str(d.get("Id", "")), name=d.get("Name"), stage=d.get("StageName"),
                    value=amount, raw=d)

    PERSON_COLS = "Id, FirstName, LastName, Name, Email, Phone, MobilePhone, AccountId"
    ORG_COLS = "Id, Name, Website"
    DEAL_COLS = "Id, Name, StageName, Amount, AccountId"

    def find_person(self, *, email=None, phone=None):
        cl = []
        if email:
            cl.append(f"Email = '{_soql_quote(email)}'")
        if phone:
            cl.append(f"Phone = '{_soql_quote(phone)}'")
        if not cl:
            return None
        soql = f"SELECT {self.PERSON_COLS} FROM Contact WHERE {' OR '.join(cl)} LIMIT 1"
        rows = self._query(soql)
        return self._person_from_sf(rows[0]) if rows else None

    def get_person(self, person_id: str) -> Person:
        d = self._request("GET", f"/sobjects/Contact/{person_id}")
        return self._person_from_sf(d)

    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        q = _soql_quote(query)
        soql = (f"SELECT {self.PERSON_COLS} FROM Contact WHERE "
                f"Name LIKE '%{q}%' OR Email LIKE '%{q}%' LIMIT {limit}")
        return [self._person_from_sf(r) for r in self._query(soql)]

    def create_person(self, *, name=None, emails=None, phones=None, organization_id=None, **extra):
        body: dict[str, Any] = dict(extra)
        if name:
            parts = name.split(" ", 1)
            body["FirstName"] = parts[0]
            body["LastName"] = parts[1] if len(parts) > 1 else parts[0]
        else:
            body.setdefault("LastName", "(Unnamed)")
        if emails:
            body["Email"] = emails[0]
        if phones:
            body["Phone"] = phones[0]
        if organization_id:
            body["AccountId"] = organization_id
        data = self._request("POST", "/sobjects/Contact", json=body)
        return self.get_person(data["id"])

    def update_person(self, person_id: str, **fields):
        body: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name" and v:
                parts = v.split(" ", 1)
                body["FirstName"] = parts[0]
                body["LastName"] = parts[1] if len(parts) > 1 else parts[0]
            elif k == "email":
                body["Email"] = v
            elif k == "phone":
                body["Phone"] = v
            else:
                body[k] = v
        self._request("PATCH", f"/sobjects/Contact/{person_id}", json=body)
        return self.get_person(person_id)

    def find_org(self, *, domain=None, name=None):
        cl = []
        if domain:
            cl.append(f"Website = '{_soql_quote(domain)}'")
        if name:
            cl.append(f"Name = '{_soql_quote(name)}'")
        if not cl:
            return None
        soql = f"SELECT {self.ORG_COLS} FROM Account WHERE {' OR '.join(cl)} LIMIT 1"
        rows = self._query(soql)
        return self._org_from_sf(rows[0]) if rows else None

    def create_org(self, *, name: str, domain=None, **extra):
        body: dict[str, Any] = {"Name": name, **extra}
        if domain:
            body["Website"] = domain
        d = self._request("POST", "/sobjects/Account", json=body)
        new = self._request("GET", f"/sobjects/Account/{d['id']}")
        return self._org_from_sf(new)

    def list_deals(self, *, person_id=None, stage=None, limit: int = 50):
        clauses = []
        if person_id:
            # Salesforce: contact↔opportunity via OpportunityContactRole
            soql = (f"SELECT OpportunityId FROM OpportunityContactRole "
                    f"WHERE ContactId = '{_soql_quote(person_id)}' LIMIT {limit}")
            ids = [r["OpportunityId"] for r in self._query(soql)]
            if not ids:
                return []
            id_list = ", ".join(f"'{_soql_quote(i)}'" for i in ids)
            soql2 = f"SELECT {self.DEAL_COLS} FROM Opportunity WHERE Id IN ({id_list}) LIMIT {limit}"
            return [self._deal_from_sf(r) for r in self._query(soql2)]
        if stage:
            clauses.append(f"StageName = '{_soql_quote(stage)}'")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        soql = f"SELECT {self.DEAL_COLS} FROM Opportunity{where} LIMIT {limit}"
        return [self._deal_from_sf(r) for r in self._query(soql)]

    def create_deal(self, *, name: str, stage=None, value=None, person_id=None,
                    organization_id=None, **extra):
        body: dict[str, Any] = {"Name": name, **extra}
        body["StageName"] = stage or "Prospecting"
        body["CloseDate"] = extra.get("CloseDate", datetime.now(timezone.utc).date().isoformat())
        if value is not None:
            body["Amount"] = value
        if organization_id:
            body["AccountId"] = organization_id
        d = self._request("POST", "/sobjects/Opportunity", json=body)
        deal_id = d["id"]
        if person_id:
            self._request("POST", "/sobjects/OpportunityContactRole",
                          json={"OpportunityId": deal_id, "ContactId": person_id})
        new = self._request("GET", f"/sobjects/Opportunity/{deal_id}")
        return self._deal_from_sf(new)

    def update_deal(self, deal_id: str, **fields):
        body: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name":
                body["Name"] = v
            elif k == "stage":
                body["StageName"] = v
            elif k == "value":
                body["Amount"] = v
            else:
                body[k] = v
        self._request("PATCH", f"/sobjects/Opportunity/{deal_id}", json=body)
        new = self._request("GET", f"/sobjects/Opportunity/{deal_id}")
        return self._deal_from_sf(new)

    def move_deal_stage(self, deal_id: str, stage: str):
        return self.update_deal(deal_id, stage=stage)

    def append_note(self, *, person_id=None, deal_id=None, content: str, title=None):
        parent = person_id or deal_id
        if not parent:
            raise ValueError("append_note requires person_id or deal_id")
        # ContentNote requires base64-encoded content + linking. Simpler: use a Task.
        body = {
            "Subject": title or "Roost note",
            "Description": content,
            "WhoId" if person_id else "WhatId": parent,
            "Status": "Completed",
            "ActivityDate": datetime.now(timezone.utc).date().isoformat(),
        }
        d = self._request("POST", "/sobjects/Task", json=body)
        return str(d.get("id", ""))

    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at=None, subject=None):
        ts = (occurred_at or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(timespec="seconds") + "Z"
        body = {
            "Subject": subject or f"[{channel}.{direction}] {ts}",
            "Description": content,
            "WhoId": person_id,
            "Status": "Completed",
            "TaskSubtype": "Call" if channel == "call" else "Task",
            "ActivityDate": datetime.now(timezone.utc).date().isoformat(),
        }
        d = self._request("POST", "/sobjects/Task", json=body)
        return str(d.get("id", ""))

    def set_custom_field(self, *, person_id=None, deal_id=None, key: str, value: Any) -> None:
        if person_id:
            self._request("PATCH", f"/sobjects/Contact/{person_id}", json={key: value})
        elif deal_id:
            self._request("PATCH", f"/sobjects/Opportunity/{deal_id}", json={key: value})
        else:
            raise ValueError("set_custom_field requires person_id or deal_id")
