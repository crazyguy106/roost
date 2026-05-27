"""HubSpot CRM provider.

Wraps the v3 CRM API at api.hubapi.com using a Private App access token.
Docs: https://developers.hubspot.com/docs/api/crm/contacts

Object IDs are HubSpot's numeric record ids (returned as strings).
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

logger = logging.getLogger("roost.extras.crm.services.hubspot")

DEFAULT_BASE_URL = "https://api.hubapi.com"


def _token() -> str:
    try:
        from roost.services.credentials import get_credential
        v = get_credential("HUBSPOT_ACCESS_TOKEN")
        if v:
            return v
    except Exception:
        pass
    return os.getenv("HUBSPOT_ACCESS_TOKEN", "")


class HubspotProvider(CrmProvider):
    name = "hubspot"

    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("HUBSPOT_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._tok_override = token

    def _tok(self) -> str:
        t = self._tok_override or _token()
        if not t:
            raise CrmConfigError("HubSpot not configured (set HUBSPOT_ACCESS_TOKEN)")
        return t

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tok()}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json=None, params=None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            r = httpx.request(method, url, headers=self._headers(), json=json,
                              params=params, timeout=30.0)
        except httpx.HTTPError as e:
            raise CrmError(f"HubSpot request failed: {e}") from e
        if r.status_code in (401, 403):
            raise CrmAuthError(f"HubSpot rejected auth: {r.status_code} {r.text[:200]}")
        if r.status_code == 404:
            raise CrmNotFoundError(f"HubSpot 404: {path}")
        if r.status_code >= 400:
            raise CrmError(f"HubSpot {r.status_code}: {r.text[:300]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError as e:
            raise CrmError(f"HubSpot returned non-JSON: {e}") from e

    def test_connection(self) -> dict:
        try:
            self._request("GET", "/crm/v3/objects/contacts", params={"limit": 1})
            return {"ok": True, "detail": "Connected to HubSpot"}
        except CrmError as e:
            return {"ok": False, "detail": str(e)}

    @staticmethod
    def _person_from_hs(d: dict) -> Person:
        props = d.get("properties", {}) or {}
        name = " ".join(x for x in [props.get("firstname"), props.get("lastname")] if x).strip() or None
        emails = [props["email"]] if props.get("email") else []
        phones = [props["phone"]] if props.get("phone") else []
        return Person(id=str(d.get("id", "")), name=name, emails=emails, phones=phones,
                      organization_id=props.get("associatedcompanyid"), raw=d)

    @staticmethod
    def _org_from_hs(d: dict) -> Org:
        props = d.get("properties", {}) or {}
        return Org(id=str(d.get("id", "")), name=props.get("name"), domain=props.get("domain"), raw=d)

    @staticmethod
    def _deal_from_hs(d: dict) -> Deal:
        props = d.get("properties", {}) or {}
        amount = None
        try:
            amount = float(props["amount"]) if props.get("amount") else None
        except (TypeError, ValueError):
            pass
        return Deal(id=str(d.get("id", "")), name=props.get("dealname"),
                    stage=props.get("dealstage"), value=amount, raw=d)

    # ── Person ─────────────────────────────────────────────────────────

    def find_person(self, *, email=None, phone=None):
        filters = []
        if email:
            filters.append({"propertyName": "email", "operator": "EQ", "value": email})
        if phone:
            filters.append({"propertyName": "phone", "operator": "EQ", "value": phone})
        if not filters:
            return None
        body = {"filterGroups": [{"filters": [f]} for f in filters], "limit": 1,
                "properties": ["firstname", "lastname", "email", "phone", "associatedcompanyid"]}
        data = self._request("POST", "/crm/v3/objects/contacts/search", json=body)
        items = data.get("results") or []
        return self._person_from_hs(items[0]) if items else None

    def get_person(self, person_id: str) -> Person:
        d = self._request("GET", f"/crm/v3/objects/contacts/{person_id}",
                          params={"properties": "firstname,lastname,email,phone,associatedcompanyid"})
        return self._person_from_hs(d)

    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        body = {"query": query, "limit": limit,
                "properties": ["firstname", "lastname", "email", "phone"]}
        data = self._request("POST", "/crm/v3/objects/contacts/search", json=body)
        return [self._person_from_hs(x) for x in (data.get("results") or [])]

    def create_person(self, *, name=None, emails=None, phones=None, organization_id=None, **extra):
        props: dict[str, Any] = dict(extra)
        if name:
            parts = name.split(" ", 1)
            props["firstname"] = parts[0]
            if len(parts) > 1:
                props["lastname"] = parts[1]
        if emails:
            props["email"] = emails[0]
        if phones:
            props["phone"] = phones[0]
        if organization_id:
            props["associatedcompanyid"] = organization_id
        data = self._request("POST", "/crm/v3/objects/contacts", json={"properties": props})
        return self._person_from_hs(data)

    def update_person(self, person_id: str, **fields):
        props = {}
        for k, v in fields.items():
            if k == "name" and v:
                parts = v.split(" ", 1)
                props["firstname"] = parts[0]
                if len(parts) > 1:
                    props["lastname"] = parts[1]
            elif k == "email":
                props["email"] = v
            elif k == "phone":
                props["phone"] = v
            else:
                props[k] = v
        data = self._request("PATCH", f"/crm/v3/objects/contacts/{person_id}",
                             json={"properties": props})
        return self._person_from_hs(data)

    # ── Organization ───────────────────────────────────────────────────

    def find_org(self, *, domain=None, name=None):
        filters = []
        if domain:
            filters.append({"propertyName": "domain", "operator": "EQ", "value": domain})
        if name:
            filters.append({"propertyName": "name", "operator": "EQ", "value": name})
        if not filters:
            return None
        body = {"filterGroups": [{"filters": [f]} for f in filters], "limit": 1,
                "properties": ["name", "domain"]}
        data = self._request("POST", "/crm/v3/objects/companies/search", json=body)
        items = data.get("results") or []
        return self._org_from_hs(items[0]) if items else None

    def create_org(self, *, name: str, domain=None, **extra):
        props = {"name": name, **extra}
        if domain:
            props["domain"] = domain
        data = self._request("POST", "/crm/v3/objects/companies", json={"properties": props})
        return self._org_from_hs(data)

    # ── Deal ───────────────────────────────────────────────────────────

    def list_deals(self, *, person_id=None, stage=None, limit: int = 50):
        if person_id:
            data = self._request("GET", f"/crm/v3/objects/contacts/{person_id}/associations/deals",
                                 params={"limit": limit})
            ids = [r.get("id") or r.get("toObjectId") for r in (data.get("results") or [])]
            return [self.get_deal(i) for i in ids if i]
        body = {"limit": limit, "properties": ["dealname", "dealstage", "amount"]}
        if stage:
            body["filterGroups"] = [{"filters": [
                {"propertyName": "dealstage", "operator": "EQ", "value": stage}]}]
        data = self._request("POST", "/crm/v3/objects/deals/search", json=body)
        return [self._deal_from_hs(x) for x in (data.get("results") or [])]

    def get_deal(self, deal_id: str) -> Deal:
        d = self._request("GET", f"/crm/v3/objects/deals/{deal_id}",
                          params={"properties": "dealname,dealstage,amount"})
        return self._deal_from_hs(d)

    def create_deal(self, *, name: str, stage=None, value=None, person_id=None,
                    organization_id=None, **extra):
        props: dict[str, Any] = {"dealname": name, **extra}
        if stage:
            props["dealstage"] = stage
        if value is not None:
            props["amount"] = str(value)
        body: dict[str, Any] = {"properties": props}
        associations = []
        if person_id:
            associations.append({"to": {"id": person_id},
                                 "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                            "associationTypeId": 3}]})
        if organization_id:
            associations.append({"to": {"id": organization_id},
                                 "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                            "associationTypeId": 5}]})
        if associations:
            body["associations"] = associations
        data = self._request("POST", "/crm/v3/objects/deals", json=body)
        return self._deal_from_hs(data)

    def update_deal(self, deal_id: str, **fields):
        props = {}
        for k, v in fields.items():
            if k == "name":
                props["dealname"] = v
            elif k == "stage":
                props["dealstage"] = v
            elif k == "value":
                props["amount"] = str(v)
            else:
                props[k] = v
        data = self._request("PATCH", f"/crm/v3/objects/deals/{deal_id}", json={"properties": props})
        return self._deal_from_hs(data)

    def move_deal_stage(self, deal_id: str, stage: str):
        return self.update_deal(deal_id, stage=stage)

    # ── Notes & communications ─────────────────────────────────────────

    def append_note(self, *, person_id=None, deal_id=None, content: str, title=None):
        body: dict[str, Any] = {
            "properties": {
                "hs_note_body": (f"<b>{title}</b><br/>{content}" if title else content),
                "hs_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        }
        associations = []
        if person_id:
            associations.append({"to": {"id": person_id},
                                 "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                            "associationTypeId": 202}]})
        if deal_id:
            associations.append({"to": {"id": deal_id},
                                 "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                            "associationTypeId": 214}]})
        if associations:
            body["associations"] = associations
        data = self._request("POST", "/crm/v3/objects/notes", json=body)
        return str(data.get("id", ""))

    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at=None, subject=None):
        ts_iso = (occurred_at or datetime.now(timezone.utc).replace(tzinfo=None)).isoformat(timespec="seconds") + "Z"
        # Use HubSpot's "communications" engagement object (whatsapp/sms/linkedin) or fall back to notes.
        chan_map = {"whatsapp": "WHATS_APP", "sms": "SMS", "linkedin": "LINKEDIN_MESSAGE"}
        comm_channel = chan_map.get(channel.lower())
        if comm_channel:
            body = {
                "properties": {
                    "hs_communication_channel_type": comm_channel,
                    "hs_communication_body": content,
                    "hs_communication_logged_from": "CRM",
                    "hs_timestamp": ts_iso,
                },
                "associations": [{"to": {"id": person_id},
                                  "types": [{"associationCategory": "HUBSPOT_DEFINED",
                                             "associationTypeId": 81}]}],
            }
            data = self._request("POST", "/crm/v3/objects/communications", json=body)
            return str(data.get("id", ""))
        # Fallback: a note with a structured title
        return self.append_note(person_id=person_id, content=content,
                                title=f"[{channel}.{direction}] {subject or ts_iso}")

    def set_custom_field(self, *, person_id=None, deal_id=None, key: str, value: Any) -> None:
        if person_id:
            self.update_person(person_id, **{key: value})
        elif deal_id:
            self.update_deal(deal_id, **{key: value})
        else:
            raise ValueError("set_custom_field requires person_id or deal_id")
