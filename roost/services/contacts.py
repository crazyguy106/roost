"""Contacts, entities, roles, and affiliations."""

import json
import logging
import sqlite3
from datetime import datetime

from roost.database import get_connection
from roost.models import (
    RoleDefinition,
    Entity, EntityCreate, EntityUpdate,
    Contact, ContactCreate, ContactUpdate,
    ContactEntity, ContactEntityCreate,
    ContactIdentifier, ContactIdentifierCreate,
)

logger = logging.getLogger("roost.services.contacts")

__all__ = [
    # Roles
    "list_roles",
    "get_role",
    "create_role",
    "update_role",
    # Contacts
    "create_contact",
    "get_contact",
    "get_contact_by_name",
    "get_contact_by_email",
    "list_contacts",
    "update_contact",
    "delete_contact",
    # Contact identifiers
    "set_contact_identifier",
    "remove_contact_identifier",
    "list_contact_identifiers",
    "find_contact_by_identifier",
    # Entities
    "create_entity",
    "get_entity",
    "get_entity_by_name",
    "list_entities",
    "update_entity",
    "delete_entity",
    # Contact-Entity affiliations
    "add_contact_entity",
    "get_contact_entity",
    "list_contact_entities",
    "remove_contact_entity",
    "get_entity_tree",
]


# ── Roles ────────────────────────────────────────────────────────────

def list_roles(active_only: bool = True) -> list[RoleDefinition]:
    """List role definitions, optionally only active ones."""
    conn = get_connection()
    query = "SELECT * FROM role_definitions"
    if active_only:
        query += " WHERE is_active = 1"
    query += " ORDER BY sort_order"
    rows = conn.execute(query).fetchall()
    conn.close()
    return [RoleDefinition(**dict(r)) for r in rows]


def get_role(code: str) -> RoleDefinition | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM role_definitions WHERE code = ?", (code.upper(),)
    ).fetchone()
    conn.close()
    if not row:
        return None
    return RoleDefinition(**dict(row))


def create_role(code: str, label: str, description: str = "") -> RoleDefinition:
    """Create a new role definition."""
    conn = get_connection()
    # Get next sort_order
    row = conn.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 as next_order FROM role_definitions"
    ).fetchone()
    next_order = row["next_order"]
    conn.execute(
        "INSERT INTO role_definitions (code, label, description, sort_order) VALUES (?, ?, ?, ?)",
        (code.upper(), label, description, next_order),
    )
    conn.commit()
    conn.close()
    return get_role(code)


def update_role(code: str, label: str | None = None, description: str | None = None,
                is_active: int | None = None) -> RoleDefinition | None:
    """Update a role definition."""
    updates = {}
    if label is not None:
        updates["label"] = label
    if description is not None:
        updates["description"] = description
    if is_active is not None:
        updates["is_active"] = is_active
    if not updates:
        return get_role(code)

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [code.upper()]
    conn = get_connection()
    conn.execute(f"UPDATE role_definitions SET {set_clause} WHERE code = ?", values)
    conn.commit()
    conn.close()
    return get_role(code)


# ── Contact Identifiers ──────────────────────────────────────────────

def _load_identifiers(conn, contact_id: int) -> list[ContactIdentifier]:
    """Load all identifiers for a contact from an open connection."""
    rows = conn.execute(
        "SELECT * FROM contact_identifiers WHERE contact_id = ? ORDER BY type, is_primary DESC",
        (contact_id,),
    ).fetchall()
    return [ContactIdentifier(**dict(r)) for r in rows]


def _load_identifiers_bulk(conn, contact_ids: list[int]) -> dict[int, list[ContactIdentifier]]:
    """Load identifiers for multiple contacts in one query."""
    if not contact_ids:
        return {}
    placeholders = ",".join("?" for _ in contact_ids)
    rows = conn.execute(
        f"SELECT * FROM contact_identifiers WHERE contact_id IN ({placeholders}) "
        "ORDER BY type, is_primary DESC",
        contact_ids,
    ).fetchall()
    result: dict[int, list[ContactIdentifier]] = {cid: [] for cid in contact_ids}
    for r in rows:
        d = dict(r)
        result[d["contact_id"]].append(ContactIdentifier(**d))
    return result


def set_contact_identifier(
    contact_id: int,
    type: str,
    value: str,
    label: str = "",
    is_primary: int = 0,
) -> ContactIdentifier:
    """Add or update a contact identifier. Upserts on (contact_id, type, value)."""
    conn = get_connection()
    # If marking as primary, clear other primaries of the same type
    if is_primary:
        conn.execute(
            "UPDATE contact_identifiers SET is_primary = 0 WHERE contact_id = ? AND type = ?",
            (contact_id, type),
        )
    try:
        cur = conn.execute(
            "INSERT INTO contact_identifiers (contact_id, type, value, label, is_primary) "
            "VALUES (?, ?, ?, ?, ?)",
            (contact_id, type, value.strip(), label, is_primary),
        )
        conn.commit()
        row_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE contact_identifiers SET label = ?, is_primary = ? "
            "WHERE contact_id = ? AND type = ? AND value = ?",
            (label, is_primary, contact_id, type, value.strip()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM contact_identifiers WHERE contact_id = ? AND type = ? AND value = ?",
            (contact_id, type, value.strip()),
        ).fetchone()
        row_id = row["id"]
    # Also sync to legacy columns for backward compat
    _sync_legacy_columns(conn, contact_id, type, value.strip(), is_primary)
    conn.commit()
    row = conn.execute("SELECT * FROM contact_identifiers WHERE id = ?", (row_id,)).fetchone()
    conn.close()
    return ContactIdentifier(**dict(row))


def _sync_legacy_columns(conn, contact_id: int, id_type: str, value: str, is_primary: int) -> None:
    """Keep legacy email/phone columns in sync when identifiers change."""
    if id_type == "email" and is_primary:
        conn.execute("UPDATE contacts SET email = ?, updated_at = datetime('now') WHERE id = ?",
                      (value, contact_id))
    elif id_type == "phone" and is_primary:
        conn.execute("UPDATE contacts SET phone = ?, updated_at = datetime('now') WHERE id = ?",
                      (value, contact_id))


def remove_contact_identifier(identifier_id: int) -> bool:
    """Remove a contact identifier by its ID."""
    conn = get_connection()
    # Get the identifier before deleting (for legacy sync)
    row = conn.execute("SELECT * FROM contact_identifiers WHERE id = ?", (identifier_id,)).fetchone()
    if not row:
        conn.close()
        return False
    cur = conn.execute("DELETE FROM contact_identifiers WHERE id = ?", (identifier_id,))
    deleted = cur.rowcount > 0
    if deleted and row["is_primary"]:
        # If we deleted a primary, promote the next one of the same type
        next_row = conn.execute(
            "SELECT id FROM contact_identifiers WHERE contact_id = ? AND type = ? LIMIT 1",
            (row["contact_id"], row["type"]),
        ).fetchone()
        if next_row:
            conn.execute("UPDATE contact_identifiers SET is_primary = 1 WHERE id = ?", (next_row["id"],))
        elif row["type"] == "email":
            conn.execute("UPDATE contacts SET email = '', updated_at = datetime('now') WHERE id = ?",
                          (row["contact_id"],))
        elif row["type"] == "phone":
            conn.execute("UPDATE contacts SET phone = '', updated_at = datetime('now') WHERE id = ?",
                          (row["contact_id"],))
    conn.commit()
    conn.close()
    return deleted


def list_contact_identifiers(contact_id: int, type: str | None = None) -> list[ContactIdentifier]:
    """List identifiers for a contact, optionally filtered by type."""
    conn = get_connection()
    if type:
        rows = conn.execute(
            "SELECT * FROM contact_identifiers WHERE contact_id = ? AND type = ? "
            "ORDER BY is_primary DESC, created_at",
            (contact_id, type),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM contact_identifiers WHERE contact_id = ? "
            "ORDER BY type, is_primary DESC, created_at",
            (contact_id,),
        ).fetchall()
    conn.close()
    return [ContactIdentifier(**dict(r)) for r in rows]


def find_contact_by_identifier(type: str, value: str) -> Contact | None:
    """Find a contact by identifier type and value (case-insensitive)."""
    conn = get_connection()
    row = conn.execute(
        """SELECT ci.contact_id FROM contact_identifiers ci
           WHERE ci.type = ? AND LOWER(ci.value) = LOWER(?)
           LIMIT 1""",
        (type, value.strip()),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return get_contact(row["contact_id"])


# ── Contacts ─────────────────────────────────────────────────────────

def create_contact(data: ContactCreate) -> Contact:
    """Create a new contact. Auto-creates identifiers for email/phone if provided."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO contacts (name, email, phone, notes) VALUES (?, ?, ?, ?)",
        (data.name, data.email, data.phone, data.notes),
    )
    conn.commit()
    contact_id = cur.lastrowid
    # Auto-create identifiers for email and phone
    if data.email and data.email.strip():
        conn.execute(
            "INSERT OR IGNORE INTO contact_identifiers (contact_id, type, value, label, is_primary) "
            "VALUES (?, 'email', ?, 'work', 1)",
            (contact_id, data.email.strip()),
        )
    if data.phone and data.phone.strip():
        conn.execute(
            "INSERT OR IGNORE INTO contact_identifiers (contact_id, type, value, label, is_primary) "
            "VALUES (?, 'phone', ?, '', 1)",
            (contact_id, data.phone.strip()),
        )
    conn.commit()
    conn.close()
    return get_contact(contact_id)


def get_contact(contact_id: int) -> Contact | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT c.*,
                  (SELECT e.name FROM contact_entities ce
                   JOIN entities e ON ce.entity_id = e.id
                   WHERE ce.contact_id = c.id AND ce.is_primary = 1
                   LIMIT 1) as entity_name,
                  (SELECT ce.entity_id FROM contact_entities ce
                   WHERE ce.contact_id = c.id AND ce.is_primary = 1
                   LIMIT 1) as entity_id
           FROM contacts c WHERE c.id = ?""",
        (contact_id,),
    ).fetchone()
    if not row:
        conn.close()
        return None
    identifiers = _load_identifiers(conn, contact_id)
    conn.close()
    return Contact(**dict(row), identifiers=identifiers)


def get_contact_by_name(name: str) -> Contact | None:
    """Find a contact by name (case-insensitive partial match)."""
    conn = get_connection()
    row = conn.execute(
        """SELECT c.*,
                  (SELECT e.name FROM contact_entities ce
                   JOIN entities e ON ce.entity_id = e.id
                   WHERE ce.contact_id = c.id AND ce.is_primary = 1
                   LIMIT 1) as entity_name,
                  (SELECT ce.entity_id FROM contact_entities ce
                   WHERE ce.contact_id = c.id AND ce.is_primary = 1
                   LIMIT 1) as entity_id
           FROM contacts c WHERE LOWER(c.name) = LOWER(?)""",
        (name,),
    ).fetchone()
    if not row:
        row = conn.execute(
            """SELECT c.*,
                      (SELECT e.name FROM contact_entities ce
                       JOIN entities e ON ce.entity_id = e.id
                       WHERE ce.contact_id = c.id AND ce.is_primary = 1
                       LIMIT 1) as entity_name,
                      (SELECT ce.entity_id FROM contact_entities ce
                       WHERE ce.contact_id = c.id AND ce.is_primary = 1
                       LIMIT 1) as entity_id
               FROM contacts c WHERE LOWER(c.name) LIKE LOWER(?)
               LIMIT 1""",
            (f"%{name}%",),
        ).fetchone()
    if not row:
        conn.close()
        return None
    identifiers = _load_identifiers(conn, row["id"])
    conn.close()
    return Contact(**dict(row), identifiers=identifiers)


def get_contact_by_email(email: str) -> Contact | None:
    """Find a contact by email (case-insensitive). Searches identifiers table first, falls back to legacy column."""
    conn = get_connection()
    # Search identifiers table (canonical source)
    ci_row = conn.execute(
        "SELECT contact_id FROM contact_identifiers WHERE type = 'email' AND LOWER(value) = LOWER(?) LIMIT 1",
        (email.strip(),),
    ).fetchone()
    if ci_row:
        contact_id = ci_row["contact_id"]
        row = conn.execute(
            """SELECT c.*,
                      (SELECT e.name FROM contact_entities ce
                       JOIN entities e ON ce.entity_id = e.id
                       WHERE ce.contact_id = c.id AND ce.is_primary = 1
                       LIMIT 1) as entity_name,
                      (SELECT ce.entity_id FROM contact_entities ce
                       WHERE ce.contact_id = c.id AND ce.is_primary = 1
                       LIMIT 1) as entity_id
               FROM contacts c WHERE c.id = ?""",
            (contact_id,),
        ).fetchone()
        if row:
            identifiers = _load_identifiers(conn, contact_id)
            conn.close()
            return Contact(**dict(row), identifiers=identifiers)
    # Fallback to legacy column
    row = conn.execute(
        """SELECT c.*,
                  (SELECT e.name FROM contact_entities ce
                   JOIN entities e ON ce.entity_id = e.id
                   WHERE ce.contact_id = c.id AND ce.is_primary = 1
                   LIMIT 1) as entity_name,
                  (SELECT ce.entity_id FROM contact_entities ce
                   WHERE ce.contact_id = c.id AND ce.is_primary = 1
                   LIMIT 1) as entity_id
           FROM contacts c WHERE LOWER(c.email) = LOWER(?)""",
        (email.strip(),),
    ).fetchone()
    if not row:
        conn.close()
        return None
    identifiers = _load_identifiers(conn, row["id"])
    conn.close()
    return Contact(**dict(row), identifiers=identifiers)


def list_contacts(entity_id: int | None = None) -> list[Contact]:
    """List contacts, optionally filtered by entity affiliation."""
    conn = get_connection()
    if entity_id is not None:
        query = """SELECT c.*,
                          (SELECT e.name FROM contact_entities ce2
                           JOIN entities e ON ce2.entity_id = e.id
                           WHERE ce2.contact_id = c.id AND ce2.is_primary = 1
                           LIMIT 1) as entity_name,
                          (SELECT ce2.entity_id FROM contact_entities ce2
                           WHERE ce2.contact_id = c.id AND ce2.is_primary = 1
                           LIMIT 1) as entity_id
                   FROM contacts c
                   JOIN contact_entities ce ON ce.contact_id = c.id
                   WHERE ce.entity_id = ?
                   ORDER BY c.name"""
        rows = conn.execute(query, (entity_id,)).fetchall()
    else:
        query = """SELECT c.*,
                          (SELECT e.name FROM contact_entities ce
                           JOIN entities e ON ce.entity_id = e.id
                           WHERE ce.contact_id = c.id AND ce.is_primary = 1
                           LIMIT 1) as entity_name,
                          (SELECT ce.entity_id FROM contact_entities ce
                           WHERE ce.contact_id = c.id AND ce.is_primary = 1
                           LIMIT 1) as entity_id
                   FROM contacts c ORDER BY c.name"""
        rows = conn.execute(query).fetchall()
    # Bulk-load identifiers
    contact_ids = [r["id"] for r in rows]
    id_map = _load_identifiers_bulk(conn, contact_ids)
    conn.close()
    return [Contact(**dict(r), identifiers=id_map.get(r["id"], [])) for r in rows]


def update_contact(contact_id: int, data: ContactUpdate) -> Contact | None:
    """Update a contact. Also syncs email/phone to identifiers table."""
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return get_contact(contact_id)

    updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [contact_id]

    conn = get_connection()
    conn.execute(f"UPDATE contacts SET {set_clause} WHERE id = ?", values)
    # Sync to identifiers table when email/phone updated via legacy interface
    if "email" in updates and updates["email"]:
        # Upsert primary email identifier
        _upsert_primary_identifier(conn, contact_id, "email", updates["email"].strip())
    if "phone" in updates and updates["phone"]:
        _upsert_primary_identifier(conn, contact_id, "phone", updates["phone"].strip())
    conn.commit()
    conn.close()
    return get_contact(contact_id)


def _upsert_primary_identifier(conn, contact_id: int, id_type: str, value: str) -> None:
    """Set a value as the primary identifier for a type, clearing others."""
    # Clear existing primary for this type
    conn.execute(
        "UPDATE contact_identifiers SET is_primary = 0 WHERE contact_id = ? AND type = ?",
        (contact_id, id_type),
    )
    try:
        conn.execute(
            "INSERT INTO contact_identifiers (contact_id, type, value, label, is_primary) "
            "VALUES (?, ?, ?, '', 1)",
            (contact_id, id_type, value),
        )
    except sqlite3.IntegrityError:
        conn.execute(
            "UPDATE contact_identifiers SET is_primary = 1 WHERE contact_id = ? AND type = ? AND value = ?",
            (contact_id, id_type, value),
        )


def delete_contact(contact_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Entities ─────────────────────────────────────────────────────────

def create_entity(data: EntityCreate) -> Entity:
    """Create a new entity (company/org)."""
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO entities (name, description, notes) VALUES (?, ?, ?)",
        (data.name, data.description, data.notes),
    )
    conn.commit()
    entity_id = cur.lastrowid
    conn.close()
    return get_entity(entity_id)


def get_entity(entity_id: int) -> Entity | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM projects WHERE entity_id = e.id) as project_count,
                  (SELECT COUNT(*) FROM contact_entities WHERE entity_id = e.id) as contact_count
           FROM entities e WHERE e.id = ?""",
        (entity_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Entity(**dict(row))


def get_entity_by_name(name: str) -> Entity | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT e.*,
                  (SELECT COUNT(*) FROM projects WHERE entity_id = e.id) as project_count,
                  (SELECT COUNT(*) FROM contact_entities WHERE entity_id = e.id) as contact_count
           FROM entities e WHERE LOWER(e.name) = LOWER(?)""",
        (name,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return Entity(**dict(row))


def list_entities(status: str | None = None) -> list[Entity]:
    """List all entities."""
    conn = get_connection()
    query = """SELECT e.*,
                      (SELECT COUNT(*) FROM projects WHERE entity_id = e.id) as project_count,
                      (SELECT COUNT(*) FROM contact_entities WHERE entity_id = e.id) as contact_count
               FROM entities e WHERE 1=1"""
    params: list = []
    if status:
        query += " AND e.status = ?"
        params.append(status)
    query += " ORDER BY e.name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [Entity(**dict(r)) for r in rows]


def update_entity(entity_id: int, data: EntityUpdate) -> Entity | None:
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return get_entity(entity_id)

    updates["updated_at"] = datetime.now().isoformat(timespec="seconds")
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [entity_id]

    conn = get_connection()
    conn.execute(f"UPDATE entities SET {set_clause} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return get_entity(entity_id)


def delete_entity(entity_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


# ── Email Contact Harvesting ─────────────────────────────────────────

def _parse_email_addresses(header: str) -> list[tuple[str, str]]:
    """Parse email header into (name, email) pairs. Handles 'Name <email>' and bare emails."""
    import re
    results = []
    if not header:
        return results
    # Split on commas that aren't inside angle brackets
    # First, handle the common "Name <email>" pattern
    for match in re.finditer(
        r'(?:"?([^"<,]*?)"?\s*<([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})>)',
        header,
    ):
        name = match.group(1).strip().strip('"').strip()
        email = match.group(2).lower().strip()
        results.append((name, email))
    # Also catch bare emails not in angle brackets
    found_emails = {r[1] for r in results}
    for match in re.finditer(r'\b([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b', header):
        email = match.group(1).lower().strip()
        if email not in found_emails:
            results.append(("", email))
            found_emails.add(email)
    return results


# Patterns that indicate automated/newsletter senders
_NOISE_PATTERNS = [
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "notifications@", "newsletter@", "mailer@", "mailer-daemon",
    "updates@", "alert@", "digest@", "bounces@",
    "marketing@", "promotions@", "promo@",
    "billing@", "invoice@", "receipt@",
    "unsubscribe", "feedback@",
    "calendar-notification", "calendar.google.com",
    "docs.google.com", "drive-shares",
    "account-security", "accountprotection",
    "daemon@", "postmaster@",
]

# Domains that are almost always automated
_NOISE_DOMAINS = {
    "mail.beehiiv.com", "substack.com", "luma-mail.com",
    "calendar.luma-mail.com", "user.luma-mail.com",
    "send.zapier.com", "pushkin.fm",
}


def _is_noise_email(email: str) -> bool:
    """Check if an email address is likely automated/newsletter."""
    email_lower = email.lower()
    for pattern in _NOISE_PATTERNS:
        if pattern in email_lower:
            return True
    domain = email_lower.split("@", 1)[1] if "@" in email_lower else ""
    if domain in _NOISE_DOMAINS:
        return True
    return False


def harvest_contacts_from_gmail(
    accounts: list[str] | None = None,
    max_messages: int = 500,
    min_interactions: int = 1,
    dry_run: bool = True,
) -> dict:
    """Scan Gmail accounts for contacts and optionally create them.

    Args:
        accounts: Google account emails to scan. None = all connected accounts.
        max_messages: Max messages to scan per account.
        min_interactions: Minimum number of emails to/from an address to include (default 1).
        dry_run: If True, only report what would happen. If False, create contacts.

    Returns:
        Dict with new_contacts, new_identifiers, skipped, noise_filtered counts and details.
    """
    from collections import defaultdict
    from roost.mcp.gmail_helpers import search_messages
    from roost.gmail.client import list_google_accounts

    # Determine which accounts to scan
    if not accounts:
        acct_list = list_google_accounts()
        accounts = [a["account"] for a in acct_list if a["account"] != "(unknown)"]
    if not accounts:
        return {"error": "No Google accounts connected"}

    # Your own email addresses to exclude
    my_emails = set()
    for acct in accounts:
        my_emails.add(acct.lower())
    # Also add common aliases
    conn = get_connection()
    owner_rows = conn.execute(
        "SELECT email FROM users WHERE role = 'owner'"
    ).fetchall()
    for r in owner_rows:
        if r["email"]:
            my_emails.add(r["email"].lower())
    conn.close()

    # Also load all existing email identifiers to find your own addresses
    existing_contacts = list_contacts()
    existing_email_map: dict[str, int] = {}  # email -> contact_id
    for c in existing_contacts:
        for ident in c.identifiers:
            if ident.type == "email":
                existing_email_map[ident.value.lower()] = c.id
        if c.email:
            existing_email_map[c.email.lower()] = c.id

    # Scan all accounts
    people: dict[str, dict] = {}  # email -> {names: set, count: int, accounts: set}

    for acct in accounts:
        try:
            msgs = search_messages("newer_than:365d", max_results=max_messages, account=acct)
        except Exception as e:
            logger.warning("Failed to scan %s: %s", acct, e)
            continue

        for msg in msgs:
            for field in ("from", "to", "cc"):
                header_val = msg.get(field, "")
                if not header_val:
                    continue
                for name, email in _parse_email_addresses(header_val):
                    if email in my_emails:
                        continue
                    if email not in people:
                        people[email] = {"names": set(), "count": 0, "accounts": set()}
                    if name and len(name) > 1:
                        people[email]["names"].add(name)
                    people[email]["count"] += 1
                    people[email]["accounts"].add(acct)

    # Filter
    noise_filtered = []
    candidates = []
    for email, info in people.items():
        if _is_noise_email(email):
            noise_filtered.append(email)
            continue
        if info["count"] < min_interactions:
            continue
        best_name = max(info["names"], key=len) if info["names"] else ""
        if not best_name:
            continue  # Skip addresses with no display name
        candidates.append({
            "email": email,
            "name": best_name,
            "count": info["count"],
            "accounts": list(info["accounts"]),
        })

    # Sort by interaction count descending
    candidates.sort(key=lambda x: x["count"], reverse=True)

    # Categorize: new vs existing
    new_contacts = []
    new_identifiers = []
    already_known = []

    for c in candidates:
        email = c["email"]
        if email in existing_email_map:
            already_known.append(c)
        else:
            # Check if name matches an existing contact (might be a new email for them)
            name_match = get_contact_by_name(c["name"])
            if name_match:
                new_identifiers.append({**c, "contact_id": name_match.id, "contact_name": name_match.name})
            else:
                new_contacts.append(c)

    # Execute if not dry run
    created = []
    identifiers_added = []

    if not dry_run:
        for nc in new_contacts:
            try:
                contact = create_contact(ContactCreate(name=nc["name"], email=nc["email"]))
                created.append({"id": contact.id, "name": nc["name"], "email": nc["email"]})
            except Exception as e:
                logger.warning("Failed to create contact %s: %s", nc["name"], e)

        for ni in new_identifiers:
            try:
                set_contact_identifier(ni["contact_id"], "email", ni["email"], label="", is_primary=0)
                identifiers_added.append({
                    "contact_id": ni["contact_id"],
                    "contact_name": ni["contact_name"],
                    "email": ni["email"],
                })
            except Exception as e:
                logger.warning("Failed to add identifier %s to contact %d: %s",
                               ni["email"], ni["contact_id"], e)

    return {
        "dry_run": dry_run,
        "accounts_scanned": accounts,
        "total_unique_addresses": len(people),
        "noise_filtered": len(noise_filtered),
        "already_known": len(already_known),
        "new_contacts": [{"name": c["name"], "email": c["email"], "interactions": c["count"]} for c in new_contacts],
        "new_contacts_count": len(new_contacts),
        "new_identifiers": [{"contact_name": ni.get("contact_name", ""), "email": ni["email"], "interactions": ni["count"]} for ni in new_identifiers],
        "new_identifiers_count": len(new_identifiers),
        "created": created if not dry_run else [],
        "identifiers_added": identifiers_added if not dry_run else [],
    }


# ── Contact-Entity affiliations ──────────────────────────────────────

def add_contact_entity(data: ContactEntityCreate) -> ContactEntity:
    """Link a contact to an entity (upsert — updates title/primary if already exists)."""
    conn = get_connection()
    # If is_primary, clear other primaries for this contact
    if data.is_primary:
        conn.execute(
            "UPDATE contact_entities SET is_primary = 0 WHERE contact_id = ?",
            (data.contact_id,),
        )
    try:
        cur = conn.execute(
            "INSERT INTO contact_entities (contact_id, entity_id, title, is_primary) VALUES (?, ?, ?, ?)",
            (data.contact_id, data.entity_id, data.title, data.is_primary),
        )
        conn.commit()
        ce_id = cur.lastrowid
    except sqlite3.IntegrityError:
        # Affiliation already exists — update title and is_primary instead
        conn.execute(
            "UPDATE contact_entities SET title = ?, is_primary = ? WHERE contact_id = ? AND entity_id = ?",
            (data.title, data.is_primary, data.contact_id, data.entity_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT id FROM contact_entities WHERE contact_id = ? AND entity_id = ?",
            (data.contact_id, data.entity_id),
        ).fetchone()
        ce_id = row["id"]
    conn.close()
    return get_contact_entity(ce_id)


def get_contact_entity(ce_id: int) -> ContactEntity | None:
    conn = get_connection()
    row = conn.execute(
        """SELECT ce.*, c.name as contact_name, e.name as entity_name
           FROM contact_entities ce
           JOIN contacts c ON ce.contact_id = c.id
           JOIN entities e ON ce.entity_id = e.id
           WHERE ce.id = ?""",
        (ce_id,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return ContactEntity(**dict(row))


def list_contact_entities(
    contact_id: int | None = None,
    entity_id: int | None = None,
) -> list[ContactEntity]:
    """List contact-entity affiliations."""
    conn = get_connection()
    query = """SELECT ce.*, c.name as contact_name, e.name as entity_name
               FROM contact_entities ce
               JOIN contacts c ON ce.contact_id = c.id
               JOIN entities e ON ce.entity_id = e.id
               WHERE 1=1"""
    params: list = []
    if contact_id is not None:
        query += " AND ce.contact_id = ?"
        params.append(contact_id)
    if entity_id is not None:
        query += " AND ce.entity_id = ?"
        params.append(entity_id)
    query += " ORDER BY ce.is_primary DESC, e.name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [ContactEntity(**dict(r)) for r in rows]


def remove_contact_entity(ce_id: int) -> bool:
    conn = get_connection()
    cur = conn.execute("DELETE FROM contact_entities WHERE id = ?", (ce_id,))
    conn.commit()
    deleted = cur.rowcount > 0
    conn.close()
    return deleted


def fetch_google_contacts(
    account: str = "",
    dry_run: bool = True,
) -> dict:
    """Fetch contacts from Google People API and optionally import them.

    Args:
        account: Google account email (empty = default account).
        dry_run: If True, only report what would happen.

    Returns:
        Dict with new_contacts, existing, created counts and details.
    """
    from roost.gmail.client import build_people_service

    service = build_people_service(account=account or None)

    # Load existing contacts for cross-reference
    existing = list_contacts()
    existing_email_map: dict[str, int] = {}
    existing_phone_map: dict[str, int] = {}
    existing_name_map: dict[str, int] = {}
    for c in existing:
        existing_name_map[c.name.lower().strip()] = c.id
        for ident in c.identifiers:
            if ident.type == "email":
                existing_email_map[ident.value.lower()] = c.id
            elif ident.type == "phone":
                existing_phone_map[ident.value.replace(" ", "")] = c.id
        if c.email:
            existing_email_map[c.email.lower()] = c.id
        if c.phone:
            existing_phone_map[c.phone.replace(" ", "")] = c.id

    # Paginate through all contacts
    all_people = []
    page_token = None
    while True:
        results = service.people().connections().list(
            resourceName="people/me",
            pageSize=200,
            personFields="names,emailAddresses,phoneNumbers,organizations",
            pageToken=page_token,
        ).execute()
        connections = results.get("connections", [])
        all_people.extend(connections)
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    new_contacts = []
    already_known = []
    new_identifiers = []
    created = []
    identifiers_added = []

    for person in all_people:
        names = person.get("names", [])
        emails = person.get("emailAddresses", [])
        phones = person.get("phoneNumbers", [])
        orgs = person.get("organizations", [])

        name = names[0].get("displayName", "").strip() if names else ""
        if not name or len(name) < 2:
            continue
        # Skip junk entries (category names, pure numbers)
        _junk_names = {"downloads", "finance", "bucket list", "lbs", "leisure"}
        if name.lower() in _junk_names or name.replace("+", "").replace(" ", "").isdigit():
            continue
        # Require at least one email or phone
        if not emails and not phones:
            continue

        # Check if already known by email or phone
        matched_id = None
        for em in emails:
            addr = em.get("value", "").lower().strip()
            if addr in existing_email_map:
                matched_id = existing_email_map[addr]
                break
        if not matched_id:
            for ph in phones:
                val = ph.get("value", "").replace(" ", "")
                if val in existing_phone_map:
                    matched_id = existing_phone_map[val]
                    break
        if not matched_id and name.lower().strip() in existing_name_map:
            matched_id = existing_name_map[name.lower().strip()]

        if matched_id:
            # Check for new identifiers to add
            new_idents_for = []
            for em in emails:
                addr = em.get("value", "").lower().strip()
                if addr and addr not in existing_email_map:
                    new_idents_for.append({"type": "email", "value": addr, "label": em.get("type", "")})
            for ph in phones:
                val = ph.get("value", "").strip()
                if val and val.replace(" ", "") not in existing_phone_map:
                    new_idents_for.append({"type": "phone", "value": val, "label": ph.get("type", "")})
            if new_idents_for:
                new_identifiers.append({"contact_id": matched_id, "name": name, "identifiers": new_idents_for})
            else:
                already_known.append({"contact_id": matched_id, "name": name})
            continue

        # New contact
        primary_email = emails[0].get("value", "").strip() if emails else ""
        primary_phone = phones[0].get("value", "").strip() if phones else ""
        org_name = orgs[0].get("name", "") if orgs else ""
        org_title = orgs[0].get("title", "") if orgs else ""
        notes_parts = []
        if org_name:
            notes_parts.append(f"Org: {org_name}")
        if org_title:
            notes_parts.append(f"Title: {org_title}")
        notes_parts.append(f"Source: Google Contacts ({account or 'default'})")

        entry = {
            "name": name,
            "email": primary_email,
            "phone": primary_phone,
            "notes": "; ".join(notes_parts),
            "all_emails": [em.get("value", "").strip() for em in emails if em.get("value", "").strip()],
            "all_phones": [ph.get("value", "").strip() for ph in phones if ph.get("value", "").strip()],
        }
        new_contacts.append(entry)

        if not dry_run:
            contact = create_contact(ContactCreate(
                name=name, email=primary_email, phone=primary_phone,
                notes="; ".join(notes_parts),
            ))
            created.append({"id": contact.id, "name": name})
            # Add additional email/phone identifiers beyond the primary
            for em in emails[1:]:
                addr = em.get("value", "").strip()
                if addr:
                    set_contact_identifier(contact.id, "email", addr, label=em.get("type", ""))
            for ph in phones[1:]:
                val = ph.get("value", "").strip()
                if val:
                    set_contact_identifier(contact.id, "phone", val, label=ph.get("type", ""))

    # Add new identifiers to existing contacts
    if not dry_run:
        for ni in new_identifiers:
            for ident in ni["identifiers"]:
                set_contact_identifier(ni["contact_id"], ident["type"], ident["value"], label=ident.get("label", ""))
                identifiers_added.append({
                    "contact_id": ni["contact_id"],
                    "name": ni["name"],
                    "type": ident["type"],
                    "value": ident["value"],
                })

    return {
        "dry_run": dry_run,
        "account": account or "(default)",
        "total_google_contacts": len(all_people),
        "already_known": len(already_known),
        "already_known_list": already_known[:20],
        "new_contacts": new_contacts,
        "new_contacts_count": len(new_contacts),
        "new_identifiers": new_identifiers,
        "new_identifiers_count": sum(len(ni["identifiers"]) for ni in new_identifiers),
        "created": created,
        "identifiers_added": identifiers_added,
    }


def get_entity_tree(entity_id: int) -> dict:
    """Get an entity with its projects and people."""
    entity = get_entity(entity_id)
    if not entity:
        return {}
    from roost.task_service import list_projects
    projects = list_projects(entity_id=entity_id)
    people = list_contact_entities(entity_id=entity_id)
    return {
        "entity": entity,
        "projects": projects,
        "people": people,
    }
