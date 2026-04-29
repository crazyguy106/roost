"""Attio CRM provider.

Wraps Attio's REST API (api.attio.com/v2) using a long-lived API key
stored via the encrypted credentials service.

Docs: https://developers.attio.com/reference

Notes:
- Attio's data model is fully customisable; we target the standard
  "people", "companies", and "deals" objects. Deployments with renamed
  objects can override via env vars (ATTIO_OBJECT_PEOPLE etc.).
- Person/Org/Deal records have stable IDs in the form
  "{workspace_id}/{object_slug}/{record_id}". We store/return the full
  triple so a later sync round-trip is unambiguous.
"""

from __future__ import annotations

import logging
import os
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

logger = logging.getLogger("roost.services.crm.attio")

DEFAULT_BASE_URL = "https://api.attio.com/v2"


def _api_key() -> str:
    # Prefer per-user encrypted credential, fall back to env.
    try:
        from roost.services.credentials import get_credential
        v = get_credential("ATTIO_API_KEY")
        if v:
            return v
    except Exception:
        pass
    return os.getenv("ATTIO_API_KEY", "")


def _val(v: Any) -> Any:
    """Unwrap Attio's {value: ...} or [{value: ...}, ...] payload shape."""
    if isinstance(v, list):
        return [_val(x) for x in v]
    if isinstance(v, dict) and "value" in v:
        return v["value"]
    return v


def _first(v: Any) -> Any:
    if isinstance(v, list):
        return v[0] if v else None
    return v


class AttioProvider(CrmProvider):
    name = "attio"

    OBJ_PEOPLE = os.getenv("ATTIO_OBJECT_PEOPLE", "people")
    OBJ_COMPANIES = os.getenv("ATTIO_OBJECT_COMPANIES", "companies")
    OBJ_DEALS = os.getenv("ATTIO_OBJECT_DEALS", "deals")

    def __init__(self, base_url: str | None = None, api_key: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("ATTIO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._api_key_override = api_key
        self._default_owner_id: str | None = None

    def _default_owner(self) -> str | None:
        """Return the workspace_member_id authorised by the current API key.

        Attio's standard `deals` schema marks `owner` as required. We cache
        it from /self so callers don't have to think about it.
        """
        if self._default_owner_id:
            return self._default_owner_id
        try:
            d = self._request("GET", "/self")
            data = d.get("data", d)
            mid = data.get("authorized_by_workspace_member_id")
            if mid:
                self._default_owner_id = mid
            return self._default_owner_id
        except CrmError:
            return None

    def _key(self) -> str:
        k = self._api_key_override or _api_key()
        if not k:
            raise CrmConfigError("Attio not configured (set ATTIO_API_KEY)")
        return k

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key()}", "Content-Type": "application/json"}

    def _request(self, method: str, path: str, *, json: dict | None = None,
                 params: dict | None = None) -> dict:
        url = f"{self.base_url}{path}"
        try:
            r = httpx.request(method, url, headers=self._headers(), json=json,
                              params=params, timeout=30.0)
        except httpx.HTTPError as e:
            raise CrmError(f"Attio request failed: {e}") from e
        if r.status_code == 401 or r.status_code == 403:
            raise CrmAuthError(f"Attio rejected auth: {r.status_code} {r.text[:200]}")
        if r.status_code == 404:
            raise CrmNotFoundError(f"Attio 404: {path}")
        if r.status_code >= 400:
            raise CrmError(f"Attio {r.status_code}: {r.text[:300]}")
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError as e:
            raise CrmError(f"Attio returned non-JSON: {e}") from e

    # ── Health ─────────────────────────────────────────────────────────

    def test_connection(self) -> dict:
        try:
            data = self._request("GET", "/self")
        except CrmConfigError as e:
            return {"ok": False, "detail": str(e)}
        except CrmError as e:
            return {"ok": False, "detail": str(e)}
        d = data.get("data", data)
        return {
            "ok": True,
            "detail": f"Connected to workspace '{d.get('workspace_name', '?')}'",
            "workspace_id": d.get("workspace_id"),
        }

    # ── Helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _person_from_attio(d: dict) -> Person:
        attrs = d.get("values", {}) or {}
        rid = d.get("id", {}) or {}
        record_id = rid.get("record_id") or rid.get("id") or ""

        name_val = _first(attrs.get("name") or [])
        if isinstance(name_val, dict):
            name = name_val.get("full_name") or name_val.get("value")
        else:
            name = _val(name_val)

        emails = []
        for e in attrs.get("email_addresses") or []:
            v = _val(e)
            if isinstance(v, dict):
                v = v.get("email_address") or v.get("value")
            if v:
                emails.append(v)

        phones = []
        for p in attrs.get("phone_numbers") or []:
            v = _val(p)
            if isinstance(v, dict):
                v = v.get("phone_number") or v.get("original_phone_number") or v.get("value")
            if v:
                phones.append(v)

        org_id = None
        comp = _first(attrs.get("company") or [])
        if comp:
            ref = _val(comp)
            if isinstance(ref, dict):
                org_id = (ref.get("target_record_id")
                          or (ref.get("target_record") or {}).get("record_id"))

        return Person(id=record_id, name=name, emails=emails, phones=phones,
                      organization_id=org_id, raw=d)

    @staticmethod
    def _org_from_attio(d: dict) -> Org:
        attrs = d.get("values", {}) or {}
        rid = (d.get("id") or {}).get("record_id", "")
        name = _val(_first(attrs.get("name") or []))
        if isinstance(name, dict):
            name = name.get("value")
        domains_field = attrs.get("domains") or []
        domain = None
        if domains_field:
            v = _val(_first(domains_field))
            if isinstance(v, dict):
                domain = v.get("domain") or v.get("value")
            else:
                domain = v
        return Org(id=rid, name=name, domain=domain, raw=d)

    @staticmethod
    def _deal_from_attio(d: dict) -> Deal:
        attrs = d.get("values", {}) or {}
        rid = (d.get("id") or {}).get("record_id", "")
        name = _val(_first(attrs.get("name") or []))
        if isinstance(name, dict):
            name = name.get("value")
        stage = _val(_first(attrs.get("stage") or []))
        if isinstance(stage, dict):
            stage = stage.get("status") or stage.get("title") or stage.get("value")
        value_val = _val(_first(attrs.get("value") or []))
        currency = None
        amount = None
        if isinstance(value_val, dict):
            amount = value_val.get("currency_value") or value_val.get("value")
            currency = value_val.get("currency_code")
        elif isinstance(value_val, (int, float)):
            amount = value_val
        return Deal(id=rid, name=name, stage=stage, value=amount, currency=currency, raw=d)

    # ── Person ─────────────────────────────────────────────────────────

    def find_person(self, *, email=None, phone=None):
        filters = []
        if email:
            filters.append({"email_addresses": {"$contains": email}})
        if phone:
            filters.append({"phone_numbers": {"$contains": phone}})
        if not filters:
            return None
        body = {"filter": filters[0] if len(filters) == 1 else {"$or": filters}, "limit": 1}
        data = self._request("POST", f"/objects/{self.OBJ_PEOPLE}/records/query", json=body)
        items = data.get("data") or []
        return self._person_from_attio(items[0]) if items else None

    def get_person(self, person_id: str) -> Person:
        d = self._request("GET", f"/objects/{self.OBJ_PEOPLE}/records/{person_id}")
        return self._person_from_attio(d.get("data") or d)

    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        body = {
            "filter": {"$or": [
                {"name": {"$contains": query}},
                {"email_addresses": {"$contains": query}},
            ]},
            "limit": limit,
        }
        data = self._request("POST", f"/objects/{self.OBJ_PEOPLE}/records/query", json=body)
        return [self._person_from_attio(x) for x in (data.get("data") or [])]

    def create_person(self, *, name=None, emails=None, phones=None, organization_id=None, **extra):
        values: dict[str, Any] = dict(extra)
        if name:
            values["name"] = [{"full_name": name}]
        if emails:
            values["email_addresses"] = [{"email_address": e} for e in emails]
        if phones:
            values["phone_numbers"] = [{"original_phone_number": p} for p in phones]
        if organization_id:
            values["company"] = [{"target_object": self.OBJ_COMPANIES,
                                   "target_record_id": organization_id}]
        body = {"data": {"values": values}}
        data = self._request("POST", f"/objects/{self.OBJ_PEOPLE}/records", json=body)
        return self._person_from_attio(data.get("data") or data)

    def update_person(self, person_id: str, **fields):
        values: dict[str, Any] = {}
        if "name" in fields and fields["name"]:
            values["name"] = [{"full_name": fields["name"]}]
        if "emails" in fields and fields["emails"]:
            values["email_addresses"] = [{"email_address": e} for e in fields["emails"]]
        if "phones" in fields and fields["phones"]:
            values["phone_numbers"] = [{"original_phone_number": p} for p in fields["phones"]]
        # Pass-through for vendor-native attribute keys
        for k, v in fields.items():
            if k in ("name", "emails", "phones"):
                continue
            values[k] = v
        body = {"data": {"values": values}}
        data = self._request("PATCH", f"/objects/{self.OBJ_PEOPLE}/records/{person_id}", json=body)
        return self._person_from_attio(data.get("data") or data)

    # ── Organization ───────────────────────────────────────────────────

    def find_org(self, *, domain=None, name=None):
        if domain:
            body = {"filter": {"domains": {"$contains": domain}}, "limit": 1}
        elif name:
            body = {"filter": {"name": {"$contains": name}}, "limit": 1}
        else:
            return None
        data = self._request("POST", f"/objects/{self.OBJ_COMPANIES}/records/query", json=body)
        items = data.get("data") or []
        return self._org_from_attio(items[0]) if items else None

    def create_org(self, *, name: str, domain=None, **extra):
        values: dict[str, Any] = {"name": [{"value": name}], **extra}
        if domain:
            values["domains"] = [{"domain": domain}]
        body = {"data": {"values": values}}
        data = self._request("POST", f"/objects/{self.OBJ_COMPANIES}/records", json=body)
        return self._org_from_attio(data.get("data") or data)

    # ── Deal ───────────────────────────────────────────────────────────

    def list_deals(self, *, person_id=None, stage=None, limit: int = 50):
        filters: list[dict] = []
        if person_id:
            filters.append({"associated_people": {"target_record_id": person_id}})
        if stage:
            filters.append({"stage": stage})
        body: dict[str, Any] = {"limit": limit}
        if filters:
            body["filter"] = filters[0] if len(filters) == 1 else {"$and": filters}
        data = self._request("POST", f"/objects/{self.OBJ_DEALS}/records/query", json=body)
        return [self._deal_from_attio(x) for x in (data.get("data") or [])]

    def create_deal(self, *, name: str, stage=None, value=None, person_id=None,
                    organization_id=None, **extra):
        # Attio fixes the currency at the attribute level (config.currency.
        # default_currency_code) — record-level value only carries the amount.
        extra.pop("currency", None)
        values: dict[str, Any] = {"name": [{"value": name}], **extra}
        if stage:
            values["stage"] = stage
        if value is not None:
            values["value"] = [{"currency_value": value}]
        if person_id:
            values["associated_people"] = [{"target_object": self.OBJ_PEOPLE,
                                              "target_record_id": person_id}]
        if organization_id:
            values["associated_company"] = [{"target_object": self.OBJ_COMPANIES,
                                              "target_record_id": organization_id}]
        # Attio requires `owner` on deals. Default to the API key's owner.
        if "owner" not in values:
            owner_id = self._default_owner()
            if owner_id:
                values["owner"] = [{"referenced_actor_type": "workspace-member",
                                    "referenced_actor_id": owner_id}]
        body = {"data": {"values": values}}
        data = self._request("POST", f"/objects/{self.OBJ_DEALS}/records", json=body)
        return self._deal_from_attio(data.get("data") or data)

    def update_deal(self, deal_id: str, **fields):
        fields.pop("currency", None)  # see create_deal
        values: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name":
                values["name"] = [{"value": v}]
            elif k == "value":
                values["value"] = [{"currency_value": v}]
            else:
                values[k] = v
        body = {"data": {"values": values}}
        data = self._request("PATCH", f"/objects/{self.OBJ_DEALS}/records/{deal_id}", json=body)
        return self._deal_from_attio(data.get("data") or data)

    def move_deal_stage(self, deal_id: str, stage: str):
        return self.update_deal(deal_id, stage=stage)

    # ── Notes & communications ─────────────────────────────────────────

    def append_note(self, *, person_id=None, deal_id=None, content: str, title=None):
        if person_id:
            parent_object = self.OBJ_PEOPLE
            parent_record_id = person_id
        elif deal_id:
            parent_object = self.OBJ_DEALS
            parent_record_id = deal_id
        else:
            raise ValueError("append_note requires person_id or deal_id")
        body = {"data": {
            "format": "plaintext",
            "parent_object": parent_object,
            "parent_record_id": parent_record_id,
            "title": title or "Roost note",
            "content": content,
        }}
        data = self._request("POST", "/notes", json=body)
        return ((data.get("data") or {}).get("id") or {}).get("note_id", "")

    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at=None, subject=None):
        # Attio doesn't have a generic "communication" object. We log as a
        # note with a structured prefix so it's filterable.
        ts = (occurred_at or datetime.utcnow()).isoformat(timespec="seconds") + "Z"
        title = f"[{channel}.{direction}] {subject or ts}"
        return self.append_note(person_id=person_id, content=content, title=title)

    # ── Custom fields ──────────────────────────────────────────────────

    def set_custom_field(self, *, person_id=None, deal_id=None, key: str, value: Any) -> None:
        if person_id:
            self.update_person(person_id, **{key: value})
        elif deal_id:
            self.update_deal(deal_id, **{key: value})
        else:
            raise ValueError("set_custom_field requires person_id or deal_id")

    # ── Attio-specific extras (namespaced; not on the base ABC) ────────

    def set_ai_attribute(self, *, person_id: str, attribute: str, value: Any) -> None:
        """Push a computed AI attribute to an Attio record. Uses the standard
        update path — Attio treats AI attributes as regular attributes that
        happen to be auto-populated. Manual overrides are preserved."""
        self.update_person(person_id, **{attribute: value})
