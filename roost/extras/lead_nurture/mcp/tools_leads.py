"""MCP tools: lead ingestion + nurture cadence control.

Surface for callers (Claude, web, scripts) to:
  * ingest a lead from any channel into the CRM + cadence engine
  * enroll an existing CRM person in a cadence
  * pause / resume / list cadence enrollments
  * approve or skip a held-for-approval step
  * manage pre-approval rules (auto-send vs. Telegram-gate)
  * drive a manual tick of the cadence engine
"""

from __future__ import annotations

from typing import Any

from roost.mcp.server import mcp
from roost.extras.lead_nurture.services import cadences as cadences_svc
from roost.extras.lead_nurture.services import leads as leads_svc
from roost.extras.lead_nurture.services import nurture as nurture_svc


# ── Lead ingestion ─────────────────────────────────────────────────────


@mcp.tool()
def lead_ingest(
    channel: str,
    email: str = "",
    phone: str = "",
    name: str = "",
    org_name: str = "",
    cadence_slug: str = "",
    vertical: str = "generic",
    message_text: str = "",
    fields_json: str = "{}",
    deal_name: str = "",
    deal_stage: str = "Lead",
    source: str = "",
) -> dict:
    """Ingest a lead from any channel into the active CRM + nurture engine.

    Args:
        channel: web_form | whatsapp | email | telegram | manual
        email: lead's email (required if no phone)
        phone: E.164 phone (required if no email)
        name: full name
        org_name: organization / company
        cadence_slug: cadence to enroll in (auto-picked by vertical if empty)
        vertical: property | financial_advisor | generic (drives default cadence)
        message_text: raw inbound text — if present, runs AI CDR classification
        fields_json: JSON string of template variables (overrides defaults)
        deal_name: CRM deal name (defaults to "Lead: <name|email>")
        deal_stage: initial deal stage in the CRM pipeline
        source: free-form source identifier (form name, campaign, etc.)
    """
    import json
    try:
        fields = json.loads(fields_json or "{}")
    except json.JSONDecodeError as e:
        return {"ok": False, "errors": [f"fields_json is not valid JSON: {e}"]}
    return leads_svc.ingest_lead(
        channel=channel,
        email=email,
        phone=phone,
        name=name,
        org_name=org_name,
        cadence_slug=cadence_slug,
        vertical=vertical,
        message_text=message_text,
        fields=fields,
        deal_name=deal_name,
        deal_stage=deal_stage,
        source=source,
    )


# ── Cadence catalog ────────────────────────────────────────────────────


@mcp.tool()
def cadence_list(vertical: str = "", enabled_only: bool = True) -> dict:
    """List available nurture cadences.

    Args:
        vertical: filter by vertical (property | financial_advisor | generic).
        enabled_only: hide disabled cadences (default True).
    """
    out = cadences_svc.list_cadences(vertical=vertical, enabled_only=enabled_only)
    return {"count": len(out), "cadences": out}


@mcp.tool()
def cadence_get(slug: str) -> dict:
    """Get a cadence by slug, including steps. Returns 'error' if not found."""
    c = cadences_svc.get_cadence_by_slug(slug)
    return c or {"error": f"cadence not found: {slug}"}


@mcp.tool()
def cadence_library_status() -> dict:
    """List shipped cadence library files and validation state."""
    return {"library": cadences_svc.list_library()}


# ── Enrollment control ────────────────────────────────────────────────


@mcp.tool()
def cadence_enroll(
    cadence_slug: str,
    crm_person_id: str = "",
    contact_email: str = "",
    contact_phone: str = "",
    contact_name: str = "",
    channel: str = "email",
    fields_json: str = "{}",
    source: str = "manual",
) -> dict:
    """Enroll an existing person in a cadence directly (skips lead_ingest).

    Use this when the CRM record already exists and you just want to start
    the nurture sequence — e.g. re-engaging a dormant deal.
    """
    import json
    try:
        fields = json.loads(fields_json or "{}")
    except json.JSONDecodeError as e:
        return {"error": f"fields_json is not valid JSON: {e}"}
    try:
        return cadences_svc.enroll_lead(
            cadence_slug=cadence_slug,
            crm_person_id=crm_person_id,
            contact_email=contact_email,
            contact_phone=contact_phone,
            contact_name=contact_name,
            channel=channel,
            fields=fields,
            source=source,
        )
    except ValueError as e:
        return {"error": str(e)}


@mcp.tool()
def cadence_list_enrollments(
    status: str = "",
    crm_person_id: str = "",
    cadence_slug: str = "",
    limit: int = 50,
) -> dict:
    """List enrollments, optionally filtered by status / person / cadence."""
    rows = cadences_svc.list_enrollments(
        status=status, crm_person_id=crm_person_id,
        cadence_slug=cadence_slug, limit=limit,
    )
    return {"count": len(rows), "enrollments": rows}


@mcp.tool()
def cadence_pause(enrollment_id: int, reason: str = "") -> dict:
    """Pause an active enrollment. No further steps will fire until resumed."""
    res = cadences_svc.pause_enrollment(enrollment_id, reason=reason)
    return res or {"error": f"enrollment {enrollment_id} not found"}


@mcp.tool()
def cadence_resume(enrollment_id: int) -> dict:
    """Resume a paused enrollment. The next step fires at the next tick if
    its next_run_at has elapsed."""
    res = cadences_svc.resume_enrollment(enrollment_id)
    return res or {"error": f"enrollment {enrollment_id} not found"}


# ── Approval gate ─────────────────────────────────────────────────────


@mcp.tool()
def nurture_approve_pending(enrollment_id: int) -> dict:
    """Release a held-for-approval step: send it now, log, and schedule next.

    Use after reviewing the draft sent to Telegram. If the step send fails,
    the enrollment is left paused with the dispatch error captured in
    pause_reason so you can fix and retry.
    """
    return nurture_svc.approve_pending(enrollment_id)


@mcp.tool()
def nurture_skip_pending(enrollment_id: int, reason: str = "") -> dict:
    """Skip the held step without sending. Enrollment advances to the next step."""
    return nurture_svc.skip_pending(enrollment_id, reason=reason)


# ── Pre-approval rules ────────────────────────────────────────────────


@mcp.tool()
def cadence_preapprove(
    cadence_slug: str = "*",
    source: str = "*",
    channel: str = "*",
    vertical: str = "*",
    note: str = "",
) -> dict:
    """Create a pre-approval rule. Matching enrollments auto-send instead of
    waiting for Telegram approval.

    Use '*' on any axis as a wildcard. The most specific matching rule wins.
    Example: cadence_preapprove(cadence_slug='property_buyer_intro',
    source='whatsapp', channel='whatsapp', note='trusted: WhatsApp buyer flow')
    """
    return cadences_svc.create_preapproval(
        cadence_slug=cadence_slug, source=source,
        channel=channel, vertical=vertical, note=note,
    )


@mcp.tool()
def cadence_list_preapprovals() -> dict:
    """List all pre-approval rules in priority order (most recent first)."""
    rows = cadences_svc.list_preapprovals()
    return {"count": len(rows), "rules": rows}


@mcp.tool()
def cadence_delete_preapproval(preapproval_id: int) -> dict:
    """Remove a pre-approval rule. Future matching sends will require approval again."""
    return cadences_svc.delete_preapproval(preapproval_id)


# ── Engine drive ──────────────────────────────────────────────────────


@mcp.tool()
def nurture_tick(max_per_tick: int = 50) -> dict:
    """Process every due enrollment once. Normally invoked by the scheduler,
    but useful for manual catch-up or dry-runs."""
    return nurture_svc.tick(max_per_tick=max_per_tick)
