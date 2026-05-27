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

# Punctuation to strip before validating a phone number — matches what
# users actually type ("+65 9123-4567", "(65) 9123 4567", "+65.9123.4567").
_PHONE_PUNCT_RE = re.compile(r"[\s()\-. ]+")

# After stripping, a plausible phone is an optional leading '+' followed
# by 8 to 15 digits. ITU-T E.164 caps the subscriber portion at 15; 8 is
# the local-number floor for SG/MY/HK landlines.
_PHONE_RE = re.compile(r"^\+?\d{8,15}$")


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
    """Classify a free-form contact string.

    Returns ('email'|'phone'|'unknown', normalised_value).

    Email branch returns the input unchanged. Phone branch strips
    formatting punctuation and validates against an E.164-ish shape
    (optional '+', 8-15 digits). Anything else returns 'unknown' so the
    caller can prompt the user to fix it rather than silently storing
    junk as a phone number.
    """
    contact = (contact or "").strip()
    if not contact:
        return "unknown", contact
    if _EMAIL_RE.match(contact):
        return "email", contact

    candidate = _PHONE_PUNCT_RE.sub("", contact)
    if _PHONE_RE.match(candidate):
        return "phone", candidate

    return "unknown", contact


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
    if kind == "unknown":
        await update.message.reply_text(
            f"Couldn't parse `{parsed['contact']}` as an email or phone "
            f"number. Examples: alice@example.com  or  +6591234567"
        )
        return
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
