"""Generic lead ingestion — one entry point for every channel.

Pipeline:
    1. Dedupe via crm.find_person (email or phone)
    2. Create or update the person in the active CRM provider
    3. Create or attach a deal
    4. (Optional) run AI CDR on inbound message text → intent/urgency/score
    5. Push computed AI fields back to the CRM (Attio: set_ai_attribute;
       others: set_custom_field)
    6. Append a "Lead captured" note summarising the inbound
    7. Enroll the lead in a nurture cadence
    8. Mirror to the local task list (so the operator's task view still works)

Channel adapters (`web_form`, `whatsapp`, `telegram`, `email`) call this
instead of poking contacts/tasks/scheduled_emails directly. Backward-compat
shim lives in `lead_pipeline.ingest_lead`.

When CRM_PROVIDER=local, this still works — the LocalProvider wraps the
Roost contacts table, so dedupe and notes still happen, just locally.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from roost.extras.lead_nurture.services import cadences as cadences_svc

logger = logging.getLogger("roost.extras.lead_nurture.services.leads")


# Default cadence slug per vertical — used when caller doesn't specify one.
DEFAULT_CADENCE_BY_VERTICAL = {
    "property": "property_buyer_intro",
    "financial_advisor": "financial_advisor_intro",
    "generic": "generic_b2b",
}


def _norm_email(s: str) -> str:
    return (s or "").strip().lower()


def _norm_phone(s: str) -> str:
    return (s or "").strip().replace(" ", "")


def _summary_note(
    *, channel: str, source: str, fields: dict, classification: dict | None
) -> str:
    lines = [
        f"Lead captured via {channel}",
        f"Source: {source or '(unspecified)'}",
        f"Captured at: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]
    if classification:
        lines.append(
            f"AI CDR — intent={classification.get('intent')}, "
            f"urgency={classification.get('urgency')}, "
            f"confidence={classification.get('confidence')}"
        )
        if classification.get("reasoning"):
            lines.append(f"Reasoning: {classification['reasoning']}")
    if fields:
        lines.append("Captured fields:")
        for k, v in fields.items():
            if v in ("", None, []):
                continue
            lines.append(f"  - {k}: {v}")
    return "\n".join(lines)


def _push_ai_fields(provider, *, person_id: str, classification: dict) -> None:
    """Try the Attio-native AI attribute path, fall back to custom_field."""
    from roost.extras.crm.services import CrmError  # lazy: cross-bundle
    intent = classification.get("intent") or ""
    urgency = classification.get("urgency") or ""
    confidence = classification.get("confidence")
    pairs = [
        ("ai_intent", intent),
        ("ai_urgency", urgency),
        ("ai_confidence", confidence),
    ]
    for key, value in pairs:
        if value in ("", None):
            continue
        try:
            if hasattr(provider, "set_ai_attribute"):
                provider.set_ai_attribute(
                    person_id=person_id, attribute=key, value=value
                )
            else:
                provider.set_custom_field(
                    person_id=person_id, key=key, value=value
                )
        except CrmError as e:
            # Custom AI attributes may not be configured in the workspace yet —
            # log once per call but don't fail the whole ingest.
            logger.warning(
                "CRM does not accept attribute '%s' (%s) — skipping. "
                "Add the attribute to your CRM schema if you want AI scores synced.",
                key, e,
            )
        except Exception:
            logger.exception("Unexpected error pushing AI attribute '%s'", key)


def _classify_if_text(message_text: str) -> dict | None:
    """Run AI CDR on inbound text. Returns None if disabled or no text."""
    if not message_text or not message_text.strip():
        return None
    try:
        from roost.extras.messaging_external.services.ai_cdr import classify_message_sync
    except ImportError:
        logger.debug("ai_cdr.classify_message_sync unavailable — skipping classification")
        return None
    try:
        result = classify_message_sync(message_text)
        return result if isinstance(result, dict) else None
    except Exception:
        logger.exception("AI CDR classification failed; continuing without")
        return None


def _notify_hot_lead(
    *,
    name: str,
    email: str,
    phone: str,
    channel: str,
    classification: dict,
    crm_person_id: str | None,
    crm_deal_id: str | None,
    enrollment_id: int | None,
) -> None:
    """Best-effort multi-channel alert when a lead is classified as hot.

    Fires a Telegram message to TELEGRAM_ALLOWED_USERS and an email to
    OPERATOR_EMAIL (if configured + Gmail available). Failures are
    swallowed — the lead is still ingested even if alerts can't send.
    """
    from roost.config import (
        OPERATOR_EMAIL,
        TELEGRAM_ALLOWED_USERS,
        TELEGRAM_BOT_TOKEN,
    )

    intent = classification.get("intent") or "unknown"
    confidence = classification.get("confidence") or 0.0
    reasoning = (classification.get("reasoning") or "").strip()
    contact_display = name or email or phone or "(unknown contact)"

    subject = f"🔥 Hot lead: {contact_display}"
    lines = [
        "Hot lead detected by AI CDR.",
        "",
        f"Contact: {contact_display}",
        f"Channel: {channel}",
        f"Email: {email or '(none)'}",
        f"Phone: {phone or '(none)'}",
        f"Intent: {intent}",
        f"Confidence: {confidence:.0%}" if isinstance(confidence, (int, float)) else f"Confidence: {confidence}",
    ]
    if reasoning:
        lines.append(f"Reasoning: {reasoning}")
    if crm_person_id:
        lines.append(f"CRM person id: {crm_person_id}")
    if crm_deal_id:
        lines.append(f"CRM deal id: {crm_deal_id}")
    if enrollment_id is not None:
        lines.append(f"Enrollment: #{enrollment_id}")
    body = "\n".join(lines)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USERS:
        try:
            import httpx
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            with httpx.Client(timeout=10) as client:
                for uid in TELEGRAM_ALLOWED_USERS:
                    client.post(url, json={"chat_id": uid, "text": f"{subject}\n\n{body}"})
        except Exception:
            logger.exception("Failed to send hot-lead Telegram alert")

    if OPERATOR_EMAIL:
        try:
            from roost.gmail.service import send_email as gmail_send
            gmail_send(to=OPERATOR_EMAIL, subject=subject, body=body)
        except Exception:
            logger.exception("Failed to send hot-lead email alert")


def _mirror_to_local_task(
    *, name: str, email: str, org_name: str, summary: str, vertical: str
) -> int | None:
    """Best-effort: create a task in the Lead Pipeline project so the operator
    sees it in the task view. Failures are non-fatal."""
    try:
        from roost.extras.lead_nurture.services.lead_pipeline import _get_or_create_pipeline_project
        from roost.services.tasks import create_task
        from roost.models import Priority, TaskCreate, TaskStatus
        project_id = _get_or_create_pipeline_project()
        title_org = f" from {org_name}" if org_name else ""
        task = create_task(
            TaskCreate(
                title=f"Lead: {name or email or '(no name)'}{title_org} [{vertical}]",
                description=summary,
                status=TaskStatus.TODO,
                priority=Priority.MEDIUM,
                project_id=project_id,
                context_note=f"Auto-created by leads.ingest_lead ({vertical}).",
            ),
            source="leads",
        )
        return task.id
    except Exception:
        logger.exception("Failed to mirror lead to local task list")
        return None


def ingest_lead(
    *,
    channel: str,
    email: str = "",
    phone: str = "",
    telegram_chat_id: str = "",
    name: str = "",
    org_name: str = "",
    org_domain: str = "",
    cadence_slug: str = "",
    vertical: str = "generic",
    fields: dict | None = None,
    message_text: str = "",
    deal_name: str = "",
    deal_value: float | None = None,
    deal_stage: str = "Lead",
    source: str = "",
    user_id: str = "",
    mirror_local_task: bool = True,
    qualifying_identifier: str = "",
) -> dict:
    """Ingest a lead from any channel. Returns:

        {
          "ok": bool,
          "channel": str,
          "crm_provider": str,
          "crm_person_id": str | None,
          "crm_deal_id": str | None,
          "enrollment_id": int | None,
          "cadence_slug": str | None,
          "classification": dict | None,
          "local_task_id": int | None,
          "errors": [str, ...],
        }
    """
    from roost.extras.crm.services import CrmConfigError, CrmError, get_provider  # lazy: cross-bundle

    fields = dict(fields or {})
    email = _norm_email(email)
    phone = _norm_phone(phone)
    telegram_chat_id = (telegram_chat_id or "").strip()
    if not (email or phone or telegram_chat_id):
        return {"ok": False, "errors": ["email, phone, or telegram_chat_id is required"]}

    # Pick a cadence: explicit > vertical default > generic_b2b
    if not cadence_slug:
        cadence_slug = DEFAULT_CADENCE_BY_VERTICAL.get(vertical, "generic_b2b")

    cadence = cadences_svc.get_cadence_by_slug(cadence_slug, user_id=user_id)
    if not cadence:
        return {
            "ok": False,
            "errors": [f"cadence not found: {cadence_slug}"],
            "cadence_slug": cadence_slug,
        }

    # Default template variables — caller's `fields` overrides.
    default_fields = {
        "name": name or (email.split("@")[0] if email else ""),
        "first_name": (name.split()[0] if name else "") or
                      (email.split("@")[0] if email else "there"),
        "email": email,
        "phone": phone,
        "org_name": org_name,
        "lead_source": source or channel,
    }
    for k, v in default_fields.items():
        fields.setdefault(k, v)

    errors: list[str] = []
    classification: dict | None = None
    crm_person_id: str | None = None
    crm_deal_id: str | None = None

    # 1+2. CRM person dedupe + create/update
    try:
        provider = get_provider()
    except CrmConfigError as e:
        return {
            "ok": False,
            "errors": [f"CRM not configured: {e}"],
            "channel": channel,
            "cadence_slug": cadence_slug,
        }

    crm_provider_name = getattr(provider, "name", "unknown")

    # Telegram-only contacts have no email or phone — no CRM provider can
    # dedupe or create them. Skip the CRM block entirely; the enrollment
    # itself stores the chat_id so STOP, mark_inbound, and cadence dispatch
    # all still work locally.
    if email or phone:
        try:
            existing = provider.find_person(
                email=email or None, phone=phone or None
            )
            if existing:
                crm_person_id = existing.id
                # Update name if it was empty in the CRM and we now have one
                if name and not (existing.name or "").strip():
                    try:
                        provider.update_person(existing.id, name=name)
                    except CrmError as e:
                        logger.warning("Could not update name on existing person: %s", e)
            else:
                person = provider.create_person(
                    name=name or None,
                    emails=[email] if email else None,
                    phones=[phone] if phone else None,
                )
                crm_person_id = person.id
        except CrmError as e:
            errors.append(f"CRM person upsert failed: {e}")
            logger.exception("CRM person upsert failed for %s/%s", email, phone)

    # 3. AI CDR on the inbound text, if any. Runs BEFORE deal-create so the
    # hot-lead branch can override deal_stage to "Hot Lead" before the deal
    # row is written. Extracted fields (property_interest, preferred_area,
    # budget, etc.) are also merged into the cadence `fields` for template
    # interpolation.
    classification = _classify_if_text(message_text)
    if classification:
        extracted = classification.get("extracted_fields") or {}
        if isinstance(extracted, dict):
            for k, v in extracted.items():
                if v in ("", None):
                    continue
                fields.setdefault(k, v)
        # Hot-lead branch: bump deal stage. Telegram + email alert fire
        # after enrollment so the message can include enrollment_id.
        if (classification.get("urgency") or "").lower() == "hot":
            deal_stage = "Hot Lead"

    # Persist the classification on the enrollment so the /leads dashboard
    # (and any downstream consumer without Attio access) can see the score.
    if classification:
        fields.setdefault("_lead_intent", classification.get("intent", ""))
        fields.setdefault("_lead_urgency", classification.get("urgency", ""))
        fields.setdefault("_lead_confidence", classification.get("confidence", 0.0))
        fields.setdefault("_lead_reasoning", classification.get("reasoning", ""))

    # 4. Optional deal (now uses hot-branch-overridden stage if applicable)
    # The `local` provider raises NotImplementedError (it has no deal model);
    # catch it alongside CrmError so the rest of the pipeline — enrollment,
    # cadence dispatch, hot-lead alert — still runs without a real CRM.
    if crm_person_id:
        try:
            deal = provider.create_deal(
                name=deal_name or f"Lead: {name or email or phone}",
                stage=deal_stage,
                value=deal_value,
                person_id=crm_person_id,
            )
            crm_deal_id = deal.id
        except (CrmError, NotImplementedError) as e:
            errors.append(f"CRM deal create failed: {e}")
            logger.warning("CRM deal create skipped: %s", e)

    # 5. Push AI attributes (best-effort)
    if crm_person_id and classification:
        _push_ai_fields(provider, person_id=crm_person_id, classification=classification)

    # 6. Append a summary note
    summary = _summary_note(
        channel=channel, source=source, fields=fields, classification=classification
    )
    if crm_person_id:
        try:
            provider.append_note(
                person_id=crm_person_id,
                content=summary,
                title=f"Lead captured ({channel})",
            )
        except CrmError as e:
            errors.append(f"CRM note append failed: {e}")
            logger.warning("CRM note append skipped: %s", e)

    # 7. Enroll in cadence
    enrollment_id: int | None = None
    try:
        enrollment = cadences_svc.enroll_lead(
            cadence_slug=cadence_slug,
            crm_person_id=crm_person_id or "",
            crm_deal_id=crm_deal_id or "",
            contact_email=email,
            contact_phone=phone,
            contact_telegram_chat_id=telegram_chat_id,
            contact_name=name,
            channel=cadence["steps"][0].get("channel", "email") if cadence["steps"] else "email",
            fields=fields,
            source=source or channel,
            user_id=user_id,
        )
        enrollment_id = enrollment["id"]
    except Exception as e:
        errors.append(f"cadence enrollment failed: {e}")
        logger.exception("Cadence enrollment failed for %s", cadence_slug)

    # 8. Mirror to local task list
    local_task_id = None
    if mirror_local_task:
        local_task_id = _mirror_to_local_task(
            name=name, email=email, org_name=org_name,
            summary=summary, vertical=vertical,
        )

    # 9. Hot-lead alert (after enrollment so we can reference enrollment_id)
    if classification and (classification.get("urgency") or "").lower() == "hot":
        _notify_hot_lead(
            name=name, email=email, phone=phone, channel=channel,
            classification=classification,
            crm_person_id=crm_person_id, crm_deal_id=crm_deal_id,
            enrollment_id=enrollment_id,
        )

    # 10. Start qualification dialog over the inbound channel, if a
    # question pack exists for this cadence AND the channel is addressable.
    # Picks identifier: explicit param > phone (whatsapp/wechat) >
    # telegram_chat_id (telegram). Skips silently otherwise.
    qualification_started = False
    if enrollment_id is not None:
        if qualifying_identifier:
            ident = qualifying_identifier
        elif channel == "telegram":
            ident = telegram_chat_id
        elif channel in ("whatsapp", "wechat"):
            ident = phone
        else:
            ident = ""
        if channel in ("whatsapp", "wechat", "telegram") and ident:
            try:
                from roost.extras.lead_nurture.services import qualification
                q_result = qualification.start_qualification_if_needed(
                    enrollment_id=enrollment_id,
                    cadence_slug=cadence_slug,
                    channel=channel,
                    identifier=ident,
                    contact_name=name,
                )
                qualification_started = bool(q_result.get("started"))
            except Exception:
                logger.exception(
                    "qualification start failed for enrollment %s",
                    enrollment_id,
                )

    return {
        "ok": not errors,
        "channel": channel,
        "crm_provider": crm_provider_name,
        "crm_person_id": crm_person_id,
        "crm_deal_id": crm_deal_id,
        "enrollment_id": enrollment_id,
        "cadence_slug": cadence_slug,
        "classification": classification,
        "local_task_id": local_task_id,
        "qualification_started": qualification_started,
        "errors": errors,
    }
