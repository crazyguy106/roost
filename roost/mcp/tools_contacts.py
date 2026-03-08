"""MCP tools for contact lookup."""

from roost.mcp.server import mcp


@mcp.tool()
def search_contacts(name: str) -> dict:
    """Search for a contact by name (partial, case-insensitive match).

    Returns the contact with their entity affiliation and all project/task assignments.

    Args:
        name: Full or partial name to search for.
    """
    try:
        from roost.task_service import (
            get_contact_by_name,
            list_contact_entities,
            list_assignments_by_contact,
            list_communications,
        )

        contact = get_contact_by_name(name)
        if not contact:
            return {"error": f"No contact found matching '{name}'"}

        affiliations = list_contact_entities(contact_id=contact.id)
        assignments = list_assignments_by_contact(contact.id)
        recent = list_communications(contact.id, limit=5)

        return {
            "contact": _contact_dict(contact),
            "affiliations": [
                {
                    "entity_id": a.entity_id,
                    "entity_name": a.entity_name,
                    "title": a.title,
                    "is_primary": bool(a.is_primary),
                }
                for a in affiliations
            ],
            "project_assignments": [
                {
                    "project_id": pa.project_id,
                    "project_name": pa.project_name,
                    "role": pa.role,
                    "role_label": pa.role_label,
                }
                for pa in assignments["project_assignments"]
            ],
            "task_assignments": [
                {
                    "task_id": ta.task_id,
                    "task_title": ta.task_title,
                    "role": ta.role,
                    "role_label": ta.role_label,
                }
                for ta in assignments["task_assignments"]
            ],
            "recent_communications": [
                {"type": c.comm_type, "subject": c.subject, "occurred_at": c.occurred_at}
                for c in recent
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_contacts(entity_id: int | None = None) -> dict:
    """List all contacts, optionally filtered by entity.

    Args:
        entity_id: Optional entity ID to filter contacts by affiliation.
    """
    try:
        from roost.task_service import list_contacts as _list

        contacts = _list(entity_id=entity_id)
        return {
            "count": len(contacts),
            "contacts": [_contact_dict(c) for c in contacts],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_contact(contact_id: int) -> dict:
    """Get a specific contact by ID with their affiliations.

    Args:
        contact_id: The contact ID.
    """
    try:
        from roost.task_service import (
            get_contact as _get,
            list_contact_entities,
            list_communications,
        )

        contact = _get(contact_id)
        if not contact:
            return {"error": f"Contact {contact_id} not found"}

        affiliations = list_contact_entities(contact_id=contact.id)
        recent = list_communications(contact.id, limit=5)
        result = _contact_dict(contact)
        result["affiliations"] = [
            {
                "entity_id": a.entity_id,
                "entity_name": a.entity_name,
                "title": a.title,
                "is_primary": bool(a.is_primary),
            }
            for a in affiliations
        ]
        result["recent_communications"] = [
            {"type": c.comm_type, "subject": c.subject, "occurred_at": c.occurred_at}
            for c in recent
        ]
        return result
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def create_contact(
    name: str,
    email: str = "",
    phone: str = "",
    notes: str = "",
) -> dict:
    """Create a new contact.

    Args:
        name: Contact's full name (required).
        email: Email address.
        phone: Phone number.
        notes: Free-text notes about the contact.
    """
    try:
        from roost.models import ContactCreate
        from roost.task_service import create_contact as _create

        data = ContactCreate(name=name, email=email, phone=phone, notes=notes)
        contact = _create(data)
        return _contact_dict(contact)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_contact(
    contact_id: int,
    name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    notes: str | None = None,
) -> dict:
    """Update an existing contact.

    Args:
        contact_id: The contact ID.
        name: New name.
        email: New email address.
        phone: New phone number.
        notes: New notes (replaces existing notes).
    """
    try:
        from roost.models import ContactUpdate
        from roost.task_service import update_contact as _update

        data = ContactUpdate(name=name, email=email, phone=phone, notes=notes)
        contact = _update(contact_id, data)
        if not contact:
            return {"error": f"Contact {contact_id} not found"}
        return _contact_dict(contact)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def add_contact_to_entity(
    contact_id: int,
    entity_id: int,
    title: str = "",
    is_primary: bool = False,
) -> dict:
    """Link a contact to an entity (organisation). Upserts if already linked.

    Args:
        contact_id: The contact ID.
        entity_id: The entity (organisation) ID.
        title: Job title or role at the entity.
        is_primary: Whether this is the contact's primary affiliation.
    """
    try:
        from roost.models import ContactEntityCreate
        from roost.services.contacts import add_contact_entity

        data = ContactEntityCreate(
            contact_id=contact_id,
            entity_id=entity_id,
            title=title,
            is_primary=int(is_primary),
        )
        ce = add_contact_entity(data)
        return {
            "id": ce.id,
            "contact_id": ce.contact_id,
            "contact_name": ce.contact_name,
            "entity_id": ce.entity_id,
            "entity_name": ce.entity_name,
            "title": ce.title,
            "is_primary": bool(ce.is_primary),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_contact_entities(
    contact_id: int | None = None,
    entity_id: int | None = None,
) -> dict:
    """List contact-entity affiliations (who works where).

    Args:
        contact_id: Filter by contact ID.
        entity_id: Filter by entity ID.
    """
    try:
        from roost.services.contacts import list_contact_entities as _list

        affiliations = _list(contact_id=contact_id, entity_id=entity_id)
        return {
            "count": len(affiliations),
            "affiliations": [
                {
                    "id": a.id,
                    "contact_id": a.contact_id,
                    "contact_name": a.contact_name,
                    "entity_id": a.entity_id,
                    "entity_name": a.entity_name,
                    "title": a.title,
                    "is_primary": bool(a.is_primary),
                }
                for a in affiliations
            ],
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def remove_contact_from_entity(affiliation_id: int) -> dict:
    """Remove a contact-entity affiliation by its ID.

    Args:
        affiliation_id: The contact_entities row ID (from list_contact_entities).
    """
    try:
        from roost.services.contacts import remove_contact_entity

        deleted = remove_contact_entity(affiliation_id)
        if not deleted:
            return {"error": f"Affiliation {affiliation_id} not found"}
        return {"ok": True, "deleted_id": affiliation_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def set_contact_identifier(
    contact_id: int,
    type: str,
    value: str,
    label: str = "",
    is_primary: bool = False,
) -> dict:
    """Add or update an identifier for a contact (email, phone, microsoft, telegram, linkedin, etc.).

    Upserts on (contact_id, type, value). If is_primary, clears other primaries of the same type.

    Args:
        contact_id: The contact ID.
        type: Identifier type (email, phone, microsoft, google, telegram, linkedin, whatsapp, notion).
        value: The identifier value (email address, phone number, user ID, profile URL, etc.).
        label: Optional label (work, personal, mobile, office).
        is_primary: Whether this is the primary identifier for this type.
    """
    try:
        from roost.services.contacts import set_contact_identifier as _set

        identifier = _set(contact_id, type, value, label, int(is_primary))
        return {
            "id": identifier.id,
            "contact_id": identifier.contact_id,
            "type": identifier.type,
            "value": identifier.value,
            "label": identifier.label,
            "is_primary": bool(identifier.is_primary),
        }
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def remove_contact_identifier(identifier_id: int) -> dict:
    """Remove a contact identifier by its ID.

    Args:
        identifier_id: The identifier row ID (from get_contact or list_contact_identifiers).
    """
    try:
        from roost.services.contacts import remove_contact_identifier as _remove

        deleted = _remove(identifier_id)
        if not deleted:
            return {"error": f"Identifier {identifier_id} not found"}
        return {"ok": True, "deleted_id": identifier_id}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def find_contact_by_identifier(type: str, value: str) -> dict:
    """Find a contact by an identifier (e.g. find by microsoft user ID, telegram ID, etc.).

    Args:
        type: Identifier type (email, phone, microsoft, google, telegram, linkedin, etc.).
        value: The identifier value to search for (case-insensitive).
    """
    try:
        from roost.services.contacts import find_contact_by_identifier as _find

        contact = _find(type, value)
        if not contact:
            return {"error": f"No contact found with {type}={value}"}
        return _contact_dict(contact)
    except Exception as e:
        return {"error": str(e)}


def _contact_dict(contact) -> dict:
    """Convert a Contact model to a plain dict."""
    result = {
        "id": contact.id,
        "name": contact.name,
        "email": contact.email,
        "phone": contact.phone,
        "notes": contact.notes,
        "entity_id": contact.entity_id,
        "entity_name": contact.entity_name,
        "created_at": contact.created_at,
        "updated_at": contact.updated_at,
    }
    if contact.identifiers:
        result["identifiers"] = [
            {
                "id": i.id,
                "type": i.type,
                "value": i.value,
                "label": i.label,
                "is_primary": bool(i.is_primary),
            }
            for i in contact.identifiers
        ]
    return result


@mcp.tool()
def harvest_email_contacts(
    max_messages: int = 500,
    min_interactions: int = 1,
    dry_run: bool = True,
    google_account: str = "",
) -> dict:
    """Scan Gmail for contacts and optionally create them in the contacts database.

    Extracts names and email addresses from From/To/Cc headers across all
    connected Google accounts. Filters out newsletters, noreply addresses,
    and automated senders. Cross-references against existing contacts.

    Run with dry_run=True first to review, then dry_run=False to create.

    Args:
        max_messages: Max messages to scan per account (default 500).
        min_interactions: Minimum emails to/from an address to include (default 1).
        dry_run: If True, only report what would happen. If False, create contacts.
        google_account: Specific Google account to scan (empty = all connected accounts).
    """
    try:
        from roost.services.contacts import harvest_contacts_from_gmail

        accounts = [google_account] if google_account else None
        return harvest_contacts_from_gmail(
            accounts=accounts,
            max_messages=max_messages,
            min_interactions=min_interactions,
            dry_run=dry_run,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def import_google_contacts(
    google_account: str = "",
    dry_run: bool = True,
) -> dict:
    """Import contacts from Google Contacts (People API) into the contacts database.

    Fetches the user's actual Google contact list (names, emails, phones,
    organisations). Cross-references against existing contacts to avoid
    duplicates. Adds new identifiers to existing contacts when found.

    Run with dry_run=True first to review, then dry_run=False to create.

    Args:
        google_account: Google account to fetch from (empty = default account).
        dry_run: If True, only report what would happen. If False, create contacts.
    """
    try:
        from roost.services.contacts import fetch_google_contacts

        return fetch_google_contacts(
            account=google_account,
            dry_run=dry_run,
        )
    except Exception as e:
        return {"error": str(e)}
