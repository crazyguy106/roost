"""Pipedrive CRM provider.

Uses the v1 REST API at api.pipedrive.com/v1 with an API token query
parameter (legacy auth — fine for self-hosted/personal use). OAuth-on-
behalf-of-org would slot in via header auth later.

Docs: https://developers.pipedrive.com/docs/api/v1
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

logger = logging.getLogger("roost.services.crm.pipedrive")

DEFAULT_BASE_URL = "https://api.pipedrive.com/v1"


def _token() -> str:
    try:
        from roost.services.credentials import get_credential
        v = get_credential("PIPEDRIVE_API_TOKEN")
        if v:
            return v
    except Exception:
        pass
    return os.getenv("PIPEDRIVE_API_TOKEN", "")


class PipedriveProvider(CrmProvider):
    name = "pipedrive"

    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or os.getenv("PIPEDRIVE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")

    def _tok(self) -> str:
        t = _token()
        if not t:
            raise CrmConfigError("Pipedrive not configured (set PIPEDRIVE_API_TOKEN)")
        return t

    def _request(self, method: str, path: str, *, json=None, params=None) -> dict:
        url = f"{self.base_url}{path}"
        params = dict(params or {})
        params["api_token"] = self._tok()
        try:
            r = httpx.request(method, url, json=json, params=params, timeout=30.0)
        except httpx.HTTPError as e:
            raise CrmError(f"Pipedrive request failed: {e}") from e
        if r.status_code in (401, 403):
            raise CrmAuthError(f"Pipedrive rejected auth: {r.status_code} {r.text[:200]}")
        if r.status_code == 404:
            raise CrmNotFoundError(f"Pipedrive 404: {path}")
        if not r.content:
            return {}
        try:
            data = r.json()
        except ValueError as e:
            raise CrmError(f"Pipedrive returned non-JSON: {e}") from e
        if r.status_code >= 400 or data.get("success") is False:
            raise CrmError(f"Pipedrive {r.status_code}: {data.get('error') or r.text[:200]}")
        return data

    def test_connection(self) -> dict:
        try:
            d = self._request("GET", "/users/me")
            name = (d.get("data") or {}).get("name", "?")
            return {"ok": True, "detail": f"Connected as {name}"}
        except CrmError as e:
            return {"ok": False, "detail": str(e)}

    @staticmethod
    def _person_from_pd(d: dict) -> Person:
        emails = [e["value"] for e in (d.get("email") or []) if isinstance(e, dict) and e.get("value")]
        if not emails and isinstance(d.get("email"), str) and d.get("email"):
            emails = [d["email"]]
        phones = [p["value"] for p in (d.get("phone") or []) if isinstance(p, dict) and p.get("value")]
        if not phones and isinstance(d.get("phone"), str) and d.get("phone"):
            phones = [d["phone"]]
        org = d.get("org_id")
        org_id = None
        if isinstance(org, dict):
            org_id = str(org.get("value") or org.get("id") or "")
        elif org is not None:
            org_id = str(org)
        return Person(id=str(d.get("id", "")), name=d.get("name"), emails=emails, phones=phones,
                      organization_id=org_id or None, raw=d)

    @staticmethod
    def _org_from_pd(d: dict) -> Org:
        return Org(id=str(d.get("id", "")), name=d.get("name"),
                   domain=d.get("cc_email") or d.get("website"), raw=d)

    @staticmethod
    def _deal_from_pd(d: dict) -> Deal:
        amount = d.get("value")
        try:
            amount = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount = None
        return Deal(id=str(d.get("id", "")), name=d.get("title"),
                    stage=str(d.get("stage_id")) if d.get("stage_id") is not None else None,
                    value=amount, currency=d.get("currency"), raw=d)

    def find_person(self, *, email=None, phone=None):
        term = email or phone
        if not term:
            return None
        d = self._request("GET", "/persons/search",
                          params={"term": term, "fields": "email,phone", "exact_match": "true",
                                  "limit": 1})
        items = ((d.get("data") or {}).get("items") or [])
        if not items:
            return None
        person_id = (items[0].get("item") or {}).get("id")
        return self.get_person(str(person_id)) if person_id else None

    def get_person(self, person_id: str) -> Person:
        d = self._request("GET", f"/persons/{person_id}")
        data = d.get("data")
        if not data:
            raise CrmNotFoundError(f"Pipedrive person {person_id} not found")
        return self._person_from_pd(data)

    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        d = self._request("GET", "/persons/search", params={"term": query, "limit": limit})
        items = ((d.get("data") or {}).get("items") or [])
        out = []
        for it in items:
            obj = it.get("item") or {}
            if obj.get("id"):
                try:
                    out.append(self.get_person(str(obj["id"])))
                except CrmError:
                    continue
        return out

    def create_person(self, *, name=None, emails=None, phones=None, organization_id=None, **extra):
        body: dict[str, Any] = {"name": name or "(unnamed)", **extra}
        if emails:
            body["email"] = [{"value": e, "primary": i == 0} for i, e in enumerate(emails)]
        if phones:
            body["phone"] = [{"value": p, "primary": i == 0} for i, p in enumerate(phones)]
        if organization_id:
            body["org_id"] = organization_id
        d = self._request("POST", "/persons", json=body)
        return self._person_from_pd(d.get("data") or {})

    def update_person(self, person_id: str, **fields):
        body: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name":
                body["name"] = v
            elif k == "email":
                body["email"] = [{"value": v, "primary": True}]
            elif k == "phone":
                body["phone"] = [{"value": v, "primary": True}]
            elif k == "emails":
                body["email"] = [{"value": e, "primary": i == 0} for i, e in enumerate(v or [])]
            elif k == "phones":
                body["phone"] = [{"value": p, "primary": i == 0} for i, p in enumerate(v or [])]
            else:
                body[k] = v
        d = self._request("PUT", f"/persons/{person_id}", json=body)
        return self._person_from_pd(d.get("data") or {})

    def find_org(self, *, domain=None, name=None):
        term = domain or name
        if not term:
            return None
        d = self._request("GET", "/organizations/search",
                          params={"term": term, "exact_match": "true", "limit": 1})
        items = ((d.get("data") or {}).get("items") or [])
        if not items:
            return None
        oid = (items[0].get("item") or {}).get("id")
        if not oid:
            return None
        full = self._request("GET", f"/organizations/{oid}")
        return self._org_from_pd(full.get("data") or {})

    def create_org(self, *, name: str, domain=None, **extra):
        body: dict[str, Any] = {"name": name, **extra}
        d = self._request("POST", "/organizations", json=body)
        return self._org_from_pd(d.get("data") or {})

    def list_deals(self, *, person_id=None, stage=None, limit: int = 50):
        if person_id:
            d = self._request("GET", f"/persons/{person_id}/deals", params={"limit": limit})
        else:
            params = {"limit": limit}
            if stage:
                params["stage_id"] = stage
            d = self._request("GET", "/deals", params=params)
        return [self._deal_from_pd(x) for x in (d.get("data") or [])]

    def create_deal(self, *, name: str, stage=None, value=None, person_id=None,
                    organization_id=None, **extra):
        body: dict[str, Any] = {"title": name, **extra}
        if stage:
            body["stage_id"] = stage
        if value is not None:
            body["value"] = value
        if person_id:
            body["person_id"] = person_id
        if organization_id:
            body["org_id"] = organization_id
        d = self._request("POST", "/deals", json=body)
        return self._deal_from_pd(d.get("data") or {})

    def update_deal(self, deal_id: str, **fields):
        body: dict[str, Any] = {}
        for k, v in fields.items():
            if k == "name":
                body["title"] = v
            elif k == "stage":
                body["stage_id"] = v
            elif k == "value":
                body["value"] = v
            else:
                body[k] = v
        d = self._request("PUT", f"/deals/{deal_id}", json=body)
        return self._deal_from_pd(d.get("data") or {})

    def move_deal_stage(self, deal_id: str, stage: str):
        return self.update_deal(deal_id, stage=stage)

    def append_note(self, *, person_id=None, deal_id=None, content: str, title=None):
        body: dict[str, Any] = {"content": (f"<b>{title}</b><br/>{content}" if title else content)}
        if person_id:
            body["person_id"] = int(person_id)
        if deal_id:
            body["deal_id"] = int(deal_id)
        d = self._request("POST", "/notes", json=body)
        return str((d.get("data") or {}).get("id", ""))

    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at=None, subject=None):
        ts = (occurred_at or datetime.utcnow())
        body = {
            "subject": subject or f"[{channel}.{direction}]",
            "type": "call" if channel == "call" else "task",
            "done": True,
            "person_id": int(person_id),
            "note": content,
            "due_date": ts.date().isoformat(),
        }
        d = self._request("POST", "/activities", json=body)
        return str((d.get("data") or {}).get("id", ""))

    def set_custom_field(self, *, person_id=None, deal_id=None, key: str, value: Any) -> None:
        if person_id:
            self._request("PUT", f"/persons/{person_id}", json={key: value})
        elif deal_id:
            self._request("PUT", f"/deals/{deal_id}", json={key: value})
        else:
            raise ValueError("set_custom_field requires person_id or deal_id")
