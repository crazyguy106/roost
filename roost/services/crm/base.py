"""CRM provider interface and shared dataclasses.

Roost talks to many CRMs through one ABC. The lowest-common-denominator
covers Person / Organization / Deal / Note / Activity — the 80% case
that exists in every CRM. Provider-specific power features (Attio AI
attributes, HubSpot workflows, Zoho Blueprint) ship as namespaced
extras alongside the base, so power users aren't constrained.

Concrete implementations live in sibling modules: attio.py, hubspot.py,
zoho.py, salesforce.py, pipedrive.py, local.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


class CrmError(RuntimeError):
    """Base class for CRM provider errors."""


class CrmConfigError(CrmError):
    """Provider not configured (missing API key, OAuth not completed)."""


class CrmNotFoundError(CrmError):
    """Record not found."""


class CrmAuthError(CrmError):
    """Auth rejected (bad token, expired, revoked)."""


@dataclass
class Person:
    id: str
    name: str | None = None
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)
    organization_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Org:
    id: str
    name: str | None = None
    domain: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Deal:
    id: str
    name: str | None = None
    stage: str | None = None
    value: float | None = None
    currency: str | None = None
    person_id: str | None = None
    organization_id: str | None = None
    closed_at: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        if self.closed_at:
            d["closed_at"] = self.closed_at.isoformat()
        return d


class CrmProvider(ABC):
    """Abstract CRM backend. One subclass per vendor.

    Methods may raise CrmConfigError, CrmNotFoundError, CrmAuthError,
    or CrmError. Implementations should never leak vendor SDK exceptions.
    """

    name: str = "abstract"

    # ── Health ─────────────────────────────────────────────────────────

    @abstractmethod
    def test_connection(self) -> dict:
        """Return {ok: bool, detail: str, ...}. Used by /api/settings/test."""

    # ── Person ─────────────────────────────────────────────────────────

    @abstractmethod
    def find_person(self, *, email: str | None = None, phone: str | None = None) -> Person | None:
        """Return the first matching person, or None."""

    @abstractmethod
    def get_person(self, person_id: str) -> Person:
        """Return the person, or raise CrmNotFoundError."""

    @abstractmethod
    def search_people(self, query: str, limit: int = 20) -> list[Person]:
        """Free-text search by name, email, phone, or company."""

    @abstractmethod
    def create_person(self, *, name: str | None = None, emails: list[str] | None = None,
                      phones: list[str] | None = None, organization_id: str | None = None,
                      **extra: Any) -> Person:
        """Create a person. extra may carry vendor-specific attributes."""

    @abstractmethod
    def update_person(self, person_id: str, **fields: Any) -> Person:
        """Patch a person's fields. Vendor-specific keys allowed in fields."""

    # ── Organization ───────────────────────────────────────────────────

    @abstractmethod
    def find_org(self, *, domain: str | None = None, name: str | None = None) -> Org | None: ...

    @abstractmethod
    def create_org(self, *, name: str, domain: str | None = None, **extra: Any) -> Org: ...

    # ── Deal ───────────────────────────────────────────────────────────

    @abstractmethod
    def list_deals(self, *, person_id: str | None = None, stage: str | None = None,
                   limit: int = 50) -> list[Deal]: ...

    @abstractmethod
    def create_deal(self, *, name: str, stage: str | None = None, value: float | None = None,
                    person_id: str | None = None, organization_id: str | None = None,
                    **extra: Any) -> Deal: ...

    @abstractmethod
    def update_deal(self, deal_id: str, **fields: Any) -> Deal: ...

    @abstractmethod
    def move_deal_stage(self, deal_id: str, stage: str) -> Deal: ...

    # ── Notes & communications (the part Roost actually drives) ────────

    @abstractmethod
    def append_note(self, *, person_id: str | None = None, deal_id: str | None = None,
                    content: str, title: str | None = None) -> str:
        """Attach a note to a person or deal. Returns the note ID."""

    @abstractmethod
    def log_communication(self, *, person_id: str, channel: str, direction: str,
                          content: str, occurred_at: datetime | None = None,
                          subject: str | None = None) -> str:
        """Log a comms event. channel: whatsapp|telegram|email|sms|call.
        direction: inbound|outbound. Returns the activity ID."""

    # ── Custom fields (provider-defined keys) ──────────────────────────

    @abstractmethod
    def set_custom_field(self, *, person_id: str | None = None, deal_id: str | None = None,
                         key: str, value: Any) -> None:
        """Set a vendor-defined custom attribute on a person or deal."""
