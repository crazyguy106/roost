"""Lead pipeline service for framework assessment leads."""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from roost.models import ContactCreate, TaskCreate, Priority, TaskStatus

_logger = logging.getLogger("roost.extras.lead_nurture.services.lead_pipeline")

# Pipeline project name — auto-created on first use
PIPELINE_PROJECT = "Lead Pipeline"

# Follow-up email templates
EMAIL_TEMPLATES = {
    "day0": {
        "subject": "Your Assessment Report",
        "body": """Hi {name},

Thank you for completing the assessment.

Here's a summary of your results:

Organisation: {org_name}
Risk Score: {risk_score}/100 ({risk_level})
Key Factors: {risk_factors}

If you'd like to discuss what your score means for your organisation, we're happy to arrange a brief conversation.

Best regards""",
    },
    "day3": {
        "subject": "What your risk score of {risk_score} means",
        "body": """Hi {name},

A few days ago you completed the assessment and scored {risk_score}/100 ({risk_level}).

Here's what that means in practice:

- If your Health Check had Red items, those are controls that aren't in place. Each one is an open door.
- Your 90-Day Roadmap from the assessment is a good starting point. The Quick Wins (Week 1-2) are designed to close the biggest gaps with the least effort.

Happy to discuss further if useful.

Best regards""",
    },
    "day7": {
        "subject": "Follow-up: your assessment results",
        "body": """Hi {name},

Following up on your recent assessment (risk score: {risk_score}/100).

Would you be interested in discussing the results and next steps? If so, just reply and we'll arrange a time.

Best regards""",
    },
}

SG_TZ = ZoneInfo("Asia/Singapore")


def _get_or_create_pipeline_project() -> int:
    """Get or create the pipeline project. Returns project ID."""
    from roost.services.projects import list_projects, create_project
    from roost.models import ProjectCreate

    projects = list_projects()
    for p in projects:
        if p.name == PIPELINE_PROJECT:
            return p.id

    proj = create_project(
        ProjectCreate(name=PIPELINE_PROJECT, description="Leads from framework assessment"),
        source="lead_pipeline",
    )
    _logger.info("Created pipeline project: %d", proj.id)
    return proj.id


def _format_template(template: dict, lead_data: dict) -> dict:
    """Format an email template with lead data."""
    factors = ", ".join(lead_data.get("risk_factors", [])[:3])
    if not factors:
        factors = "See your full assessment report"

    ctx = {
        "name": lead_data.get("name") or "there",
        "org_name": lead_data.get("org_name") or "your organisation",
        "risk_score": lead_data.get("risk_score", "?"),
        "risk_level": lead_data.get("risk_level", "Unknown"),
        "risk_factors": factors,
        "city": "Singapore",  # default, can be overridden
    }

    return {
        "subject": template["subject"].format(**ctx),
        "body": template["body"].format(**ctx),
    }


def ingest_lead(lead_data: dict) -> dict:
    """Process an inbound lead from the framework assessment form.

    Backward-compat shim — delegates to `roost.extras.lead_nurture.services.leads.ingest_lead`,
    which is now the canonical multi-channel entry point. Behavior preserved:

      - Dedupes by email (CRM `find_person`).
      - Mirrors a task into the local "Lead Pipeline" project.
      - Enrolls the lead in the seeded `framework_assessment` cadence so the
        original day-0 / day-3 / day-7 emails fire from the nurture engine
        instead of being scheduled inline here.

    Returns the same `{contact_id, task_id, emails_scheduled}` shape callers
    expect, plus extra keys (`crm_person_id`, `enrollment_id`) for new code.
    The `emails_scheduled` list is now empty at ingest time — the cadence
    engine schedules each step on its tick.
    """
    from roost.extras.lead_nurture.services.leads import ingest_lead as _leads_ingest

    email = lead_data.get("email", "").strip().lower()
    name = lead_data.get("name", "").strip()
    if not email:
        return {"error": "Email is required"}

    # Build template variables for the seeded framework_assessment templates.
    factors = ", ".join(lead_data.get("risk_factors", [])[:3]) \
        or "See your full assessment report"
    fields = {
        "name": name or "there",
        "first_name": (name.split()[0] if name else "") or "there",
        "org_name": lead_data.get("org_name") or "your organisation",
        "risk_score": lead_data.get("risk_score", "?"),
        "risk_level": lead_data.get("risk_level", "Unknown"),
        "risk_factors": factors,
    }

    result = _leads_ingest(
        channel="web_form",
        email=email,
        name=name,
        org_name=lead_data.get("org_name") or "",
        cadence_slug="framework_assessment",
        vertical="generic",
        fields=fields,
        deal_name=f"Framework assessment lead: {name or email}",
        deal_stage="Lead",
        source="framework_assessment",
    )

    # Translate to the legacy return shape.
    return {
        "contact_id": result.get("crm_person_id"),
        "task_id": result.get("local_task_id"),
        "emails_scheduled": [],   # cadence engine schedules each step on tick
        "enrollment_id": result.get("enrollment_id"),
        "crm_person_id": result.get("crm_person_id"),
        "crm_deal_id": result.get("crm_deal_id"),
        "errors": result.get("errors", []),
    }


def _legacy_ingest_lead(lead_data: dict) -> dict:
    """Original inline implementation, kept for reference and rollback.
    Not called from the live path. To restore old behavior, swap `ingest_lead`
    above with this body and remove the cadence enrollment.
    """
    from roost.services.contacts import (
        create_contact,
        find_contact_by_identifier,
        update_contact,
    )
    from roost.services.tasks import create_task
    from roost.services.scheduled_emails import schedule_email

    email = lead_data.get("email", "").strip().lower()
    name = lead_data.get("name", "").strip()
    if not email:
        return {"error": "Email is required"}

    # 1. Create or update contact
    existing = find_contact_by_identifier("email", email)
    if existing:
        contact_id = existing.id
        # Append assessment info to notes
        note_line = (
            f"\n[{datetime.now(SG_TZ).strftime('%Y-%m-%d')}] "
            f"Framework assessment: {lead_data.get('org_name', '')} — "
            f"Risk {lead_data.get('risk_score', '?')}/100 ({lead_data.get('risk_level', '')})"
        )
        current_notes = existing.notes or ""
        from roost.models import ContactUpdate
        update_contact(contact_id, ContactUpdate(notes=current_notes + note_line))
        _logger.info("Updated existing contact %d (%s) with assessment data", contact_id, email)
    else:
        notes = (
            f"Source: framework assessment\n"
            f"Org: {lead_data.get('org_name', '')}\n"
            f"Industries: {', '.join(lead_data.get('industries', []))}\n"
            f"Revenue: {lead_data.get('revenue', '')}\n"
            f"AI Profile: {lead_data.get('ai_profile', '')}\n"
            f"Risk Score: {lead_data.get('risk_score', '?')}/100 ({lead_data.get('risk_level', '')})\n"
            f"Sectors: {', '.join(lead_data.get('sectors', ['base']))}"
        )
        contact = create_contact(ContactCreate(
            name=name or email.split("@")[0],
            email=email,
            notes=notes,
        ))
        contact_id = contact.id
        _logger.info("Created new contact %d (%s) from framework lead", contact_id, email)

    # 2. Create pipeline task
    project_id = _get_or_create_pipeline_project()
    risk_score = lead_data.get("risk_score", "?")
    risk_level = lead_data.get("risk_level", "Unknown")
    org = lead_data.get("org_name", "")
    org_label = f" from {org}" if org else ""

    task = create_task(
        TaskCreate(
            title=f"Lead: {name or email}{org_label} — Risk {risk_score} ({risk_level})",
            description=(
                f"**Contact:** {name} <{email}>\n"
                f"**Organisation:** {org}\n"
                f"**Risk Score:** {risk_score}/100 ({risk_level})\n"
                f"**Industries:** {', '.join(lead_data.get('industries', []))}\n"
                f"**AI Profile:** {lead_data.get('ai_profile', '')}\n"
                f"**Sectors:** {', '.join(lead_data.get('sectors', ['base']))}\n"
                f"**Risk Factors:** {', '.join(lead_data.get('risk_factors', []))}\n\n"
                f"Pipeline stage: Lead\n"
                f"Contact ID: {contact_id}"
            ),
            status=TaskStatus.TODO,
            priority=Priority.HIGH if int(risk_score or 0) >= 50 else Priority.MEDIUM,
            deadline=datetime.now(SG_TZ) + timedelta(days=3),
            project_id=project_id,
            context_note=f"Auto-created from framework assessment. Contact #{contact_id}.",
        ),
        source="lead_pipeline",
    )
    _logger.info("Created pipeline task %d for lead %s", task.id, email)

    # 3. Schedule follow-up emails
    emails_scheduled = []
    for day_key, delay_days in [("day0", 0), ("day3", 3), ("day7", 7)]:
        template = EMAIL_TEMPLATES[day_key]
        formatted = _format_template(template, lead_data)

        if delay_days == 0:
            # Send immediately (schedule 2 min from now to avoid race)
            send_time = datetime.now(SG_TZ) + timedelta(minutes=2)
        else:
            # Schedule at 9 AM SGT on the target day
            send_time = (datetime.now(SG_TZ) + timedelta(days=delay_days)).replace(
                hour=9, minute=0, second=0, microsecond=0
            )

        send_utc = send_time.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            result = schedule_email(
                provider="gmail",
                to=email,
                subject=formatted["subject"],
                body=formatted["body"],
                scheduled_at=send_utc,
            )
            emails_scheduled.append({
                "template": day_key,
                "scheduled_at": send_utc,
                "id": result.get("id"),
            })
        except Exception as e:
            _logger.error("Failed to schedule %s email for %s: %s", day_key, email, e)

    _logger.info("Scheduled %d follow-up emails for %s", len(emails_scheduled), email)

    return {
        "contact_id": contact_id,
        "task_id": task.id,
        "emails_scheduled": emails_scheduled,
    }
