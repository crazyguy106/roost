"""Local CRM provider — wraps Roost's own contacts/communications tables.

Used when no external CRM is connected. The interface is identical to the
external providers, so recipes and MCP tools work unchanged regardless of
backend.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from roost.models import (
    CommunicationCreate,
    ContactCreate,
    ContactUpdate,
    EntityCreate,
)
from roost.services import contacts as contacts_svc
from roost.services import communications as comms_svc
from roost.services import notes as notes_svc
from roost.extras.crm.services.base import (
    CrmNotFoundError,
    CrmProvider,
    Deal,
    Org,
    Person,
)

logger = logging.getLogger("roost.extras.crm.services.local")


def _to_person(c) -> Person:
    emails = [i.value for i in (c.identifiers or []) if i.type == "email" and i.value]
    if not emails and c.email:
        emails = [c.email]
    phones = [i.value for i in (c.identifiers or []) if i.type == "phone" and i.value]
    if not phones and c.phone:
        phones = [c.phone]
    return Person(
        id=str(c.id),
        name=c.name,
        emails=emails,
        phones=phones,
        organization_id=str(c.entity_id) if c.entity_id else None,
        raw=c.model_dump() if hasattr(c, "model_dump") else dict(c),
    )


def _to_org(e) -> Org:
    return Org(id=str(e.id), name=e.name, raw=e.model_dump() if hasattr(e, "model_dump") else dict(e))


class LocalProvider(CrmProvider):
    name = "local"

    def test_connection(self) -> dict:
        return {"ok": True, "detail": "Local Roost contacts table"}

    # ── Person ─────────────────────────────────────────────────────────

    def find_person(self, *, email=None, phone=None):
        if email:
            c = contacts_svc.find_contact_by_identifier("email", email)
            if c:
                return _to_person(c)
            c = contacts_svc.get_contact_by_email(email)
            if c:
                return _to_person(c)
        if phone:
            c = contacts_svc.find_contact_by_identifier("phone", phone)
            if c:
                return _to_person(c)
        return None

    def get_person(self, person_id: str) -> Person:
        c = contacts_svc.get_contact(int(person_id))
        if not c:
            raise CrmNotFoundError(f"contact {person_id} not found")
        return _to_person(c)

    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        # Best-effort: name match + email match
        out: list[Person] = []
        seen: set[int] = set()
        c = contacts_svc.get_contact_by_name(query)
        if c and c.id not in seen:
            out.append(_to_person(c))
            seen.add(c.id)
        c = contacts_svc.get_contact_by_email(query)
        if c and c.id not in seen:
            out.append(_to_person(c))
            seen.add(c.id)
        for c in contacts_svc.list_contacts():
            if len(out) >= limit:
                break
            if c.id in seen:
                continue
            q = query.lower()
            if q in (c.name or "").lower() or q in (c.email or "").lower():
                out.append(_to_person(c))
                seen.add(c.id)
        return out

    def create_person(self, *, name=None, emails=None, phones=None, organization_id=None, **extra):
        c = contacts_svc.create_contact(ContactCreate(
            name=name or "(unnamed)",
            email=(emails or [""])[0] if emails else "",
            phone=(phones or [""])[0] if phones else "",
            notes=extra.get("notes", ""),
        ))
        # Set additional identifiers
        for e in (emails or [])[1:]:
            contacts_svc.set_contact_identifier(c.id, "email", e)
        for p in (phones or [])[1:]:
            contacts_svc.set_contact_identifier(c.id, "phone", p)
        return _to_person(contacts_svc.get_contact(c.id))

    def update_person(self, person_id: str, **fields):
        # Plural identifiers degrade to (head → scalar slot, tail → identifiers)
        # so callers can use either shape interchangeably with create_person.
        emails = fields.get("emails")
        phones = fields.get("phones")
        email_scalar = fields.get("email")
        phone_scalar = fields.get("phone")
        if emails and email_scalar is None:
            email_scalar = emails[0] if emails else None
        if phones and phone_scalar is None:
            phone_scalar = phones[0] if phones else None
        upd = ContactUpdate(
            name=fields.get("name"),
            email=email_scalar,
            phone=phone_scalar,
            notes=fields.get("notes"),
        )
        cid = int(person_id)
        c = contacts_svc.update_contact(cid, upd)
        if not c:
            raise CrmNotFoundError(f"contact {person_id} not found")
        for e in (emails or [])[1:]:
            contacts_svc.set_contact_identifier(cid, "email", e)
        for p in (phones or [])[1:]:
            contacts_svc.set_contact_identifier(cid, "phone", p)
        return _to_person(contacts_svc.get_contact(cid))

    # ── Organization ───────────────────────────────────────────────────

    def find_org(self, *, domain=None, name=None):
        if name:
            e = contacts_svc.get_entity_by_name(name)
            if e:
                return _to_org(e)
        return None

    def create_org(self, *, name: str, domain=None, **extra):
        e = contacts_svc.create_entity(EntityCreate(
            name=name,
            description=extra.get("description", ""),
        ))
        return _to_org(e)

    # ── Deal — stub. Local has no native deal model yet ────────────────

    def list_deals(self, *, person_id=None, stage=None, limit: int = 50):
        # Roost has lead_pipeline but no first-class "deal". Return empty
        # for now; v2 can map pipeline_leads → Deal.
        return []

    def create_deal(self, *, name: str, stage=None, value=None, person_id=None,
                    organization_id=None, **extra):
        raise NotImplementedError("local provider has no deal model; connect Attio/HubSpot/Zoho")

    def update_deal(self, deal_id: str, **fields):
        raise NotImplementedError("local provider has no deal model")

    def move_deal_stage(self, deal_id: str, stage: str):
        raise NotImplementedError("local provider has no deal model")

    # ── Notes & communications ─────────────────────────────────────────

    def append_note(self, *, person_id=None, deal_id=None, content: str, title=None):
        from roost.models import NoteCreate
        body = (f"# {title}\n\n" if title else "") + content
        if person_id:
            tag = f"contact:{person_id}"
        elif deal_id:
            tag = f"deal:{deal_id}"
        else:
            tag = "crm"
        n = notes_svc.create_note(NoteCreate(title=title or "CRM note", content=body, tag=tag))
        return str(n.id)

    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at=None, subject=None):
        c = comms_svc.log_communication(CommunicationCreate(
            contact_id=int(person_id),
            comm_type=f"{channel}_{direction}",
            subject=subject or "",
            detail=content,
        ))
        return str(c.id)

    # ── Custom fields (stored in contact notes as JSON for local) ──────

    def set_custom_field(self, *, person_id=None, deal_id=None, key: str, value: Any) -> None:
        if not person_id:
            raise NotImplementedError("local: deal custom fields not supported")
        c = contacts_svc.get_contact(int(person_id))
        if not c:
            raise CrmNotFoundError(f"contact {person_id} not found")
        # Append a "key=value" line to notes — minimal but visible in UI
        line = f"\n[{key}] {value} ({datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})"
        contacts_svc.update_contact(c.id, ContactUpdate(notes=(c.notes or "") + line))
