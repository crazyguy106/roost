"""Telegram handler for inbound lead capture.

Command:
  /lead <name> | <phone-or-email> [| <vertical>] [| <free-text notes>]

Pipe-delimited so names with spaces work without quoting. Routes to
`leads.ingest_lead(channel="telegram", source="telegram_promote", ...)`
which dedupes against CRM, enrols in the appropriate cadence, and returns
the enrollment id.

Symmetric with /napprove, /nskip, /nlist, /preapprove — wraps the lead
service so an operator never has to switch to MCP or web to promote a
chat into a tracked lead.
"""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized

logger = logging.getLogger(__name__)

# Loose email check — same shape used elsewhere in lead_nurture.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_lead_command(raw: str) -> dict:
    """Split a `/lead ...` argument string into structured fields.

    Returns a dict with keys: name, contact, vertical, notes, error.
    `error` is set if the required fields are missing.
    """
    # Strip the leading "/lead" if it's still there (depends on whether the
    # caller passed context.args.join or the raw message text).
    body = raw.strip()
    if body.lower().startswith("/lead"):
        body = body[len("/lead"):].strip()

    if not body:
        return {"error": "Usage: /lead <name> | <phone-or-email> [| <vertical>] [| <notes>]"}

    parts = [p.strip() for p in body.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1]:
        return {"error": "Need at least name and a contact (phone or email), separated by `|`."}

    return {
        "name": parts[0],
        "contact": parts[1],
        "vertical": parts[2] if len(parts) > 2 and parts[2] else "generic",
        "notes": parts[3] if len(parts) > 3 and parts[3] else "",
    }


def _classify_contact(contact: str) -> tuple[str, str]:
    """Return ('email'|'phone', normalised_value)."""
    contact = contact.strip()
    if _EMAIL_RE.match(contact):
        return "email", contact
    return "phone", contact


@authorized
async def cmd_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Promote a Telegram conversation into a tracked lead."""
    from roost.extras.lead_nurture.services.leads import ingest_lead

    raw = update.message.text if update.message else ""
    parsed = _parse_lead_command(raw)
    if parsed.get("error"):
        await update.message.reply_text(parsed["error"])
        return

    kind, value = _classify_contact(parsed["contact"])
    kwargs = {
        "channel": "telegram",
        "source": "telegram_promote",
        "name": parsed["name"],
        "vertical": parsed["vertical"],
    }
    if kind == "email":
        kwargs["email"] = value
    else:
        kwargs["phone"] = value
    if parsed["notes"]:
        kwargs["fields"] = {"notes": parsed["notes"]}

    try:
        result = ingest_lead(**kwargs)
    except Exception as e:
        logger.exception("ingest_lead failed in /lead handler")
        await update.message.reply_text(f"Couldn't ingest lead: {e}")
        return

    if not result.get("ok"):
        errs = "; ".join(result.get("errors") or ["unknown error"])
        await update.message.reply_text(f"Lead not ingested: {errs}")
        return

    enrollment_id = result.get("enrollment_id")
    cadence = result.get("cadence_slug") or "—"
    crm_id = result.get("crm_person_id") or "—"
    reply = (
        f"Lead captured ✓\n"
        f"  Enrollment #{enrollment_id}\n"
        f"  Cadence: {cadence}\n"
        f"  CRM person: {crm_id}"
    )
    await update.message.reply_text(reply)
