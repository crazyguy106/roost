"""HTML page routes for the lead-nurture bundle.

Mirrors the SME-Ops pattern: a `_build_pages_router()` that:
- adds this bundle's `templates/` dir to the core ChoiceLoader
- registers `GET /leads` (gated by LEAD_NURTURE_ENABLED)
- shares helpers (`build_pipeline`, `enrollment_summary`) with `api_leads.py`
  so the rendered cards and the JSON endpoint always agree.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Request

logger = logging.getLogger("roost.extras.lead_nurture.web.pages")

_BUNDLE_DIR = Path(__file__).resolve().parent.parent


# ── Pipeline column definitions ────────────────────────────────────────
#
# Each column is `(key, label, predicate)`. Predicates operate on an
# enrichment dict produced by `_enrich(enrollment)` so we don't have to
# repeatedly parse `fields_json`. The first matching predicate wins (the
# columns are mutually exclusive after enrichment).

COLUMN_KEYS = ["new", "qualifying", "hot", "nurturing", "paused", "closed"]
COLUMN_LABELS = {
    "new": "New",
    "qualifying": "Qualifying",
    "hot": "Hot",
    "nurturing": "Nurturing",
    "paused": "Paused",
    "closed": "Closed",
}


def _enrich(enrollment: dict) -> dict:
    """Add derived attributes used by both the pipeline grouper and the
    card renderer. Mutates a shallow copy, never the input."""
    e = dict(enrollment)
    fields = e.get("fields") or {}
    e["qualify_status"] = fields.get("_qualify_status") or ""
    e["qualify_label"] = fields.get("_qualify_label") or ""
    e["qualify_score"] = fields.get("_qualify_score")
    e["qualify_idx"] = fields.get("_qualify_idx")
    e["qualify_channel"] = fields.get("_qualify_channel") or ""
    e["lead_urgency"] = (fields.get("_lead_urgency") or "").lower()
    e["lead_intent"] = fields.get("_lead_intent") or ""
    e["lead_source"] = fields.get("lead_source") or ""
    return e


def _column_for(e: dict) -> str:
    """Bucket an enriched enrollment into one of the six pipeline columns."""
    status = e.get("status") or ""
    pause_reason = e.get("pause_reason") or ""

    if status in ("exited", "completed"):
        return "closed"

    if status == "paused":
        if pause_reason == "qualifying" and e["qualify_status"] == "in_progress":
            return "qualifying"
        return "paused"

    # status == "active" from here on (or unknown statuses — treat as active)
    if e["qualify_label"] == "hot" or e["lead_urgency"] == "hot":
        return "hot"
    if e["qualify_label"] == "warm" or (e.get("current_step") or 0) >= 1:
        return "nurturing"
    return "new"


def _relative_time(iso_str: str | None) -> str:
    """Best-effort 'N min ago' formatter; falls back to the raw string."""
    if not iso_str:
        return ""
    try:
        # tolerate both 'Z' suffix and naive isoformat strings
        s = iso_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = now - dt
        secs = int(delta.total_seconds())
        if secs < 60:
            return "just now"
        if secs < 3600:
            return f"{secs // 60}m ago"
        if secs < 86400:
            return f"{secs // 3600}h ago"
        return f"{secs // 86400}d ago"
    except Exception:
        return iso_str[:16]


def _enrollment_card(e: dict) -> dict:
    """A compact view used by the dashboard card and the JSON endpoint."""
    name = e.get("contact_name") or e.get("contact_email") or e.get("contact_phone") or "(no name)"
    channel = e.get("channel") or "?"

    # Qualifying progress, e.g. "2/3"
    qualifying_progress = ""
    if e["qualify_status"] == "in_progress":
        # The total comes from the question pack — compute on demand to keep
        # the card decoupled from the qualification module's data.
        try:
            from roost.extras.lead_nurture.services.qualification import (
                QUESTIONS_BY_CADENCE,
            )
            total = len(QUESTIONS_BY_CADENCE.get(e.get("cadence_slug") or "", []))
            idx = int(e.get("qualify_idx") or 0)
            if total:
                qualifying_progress = f"{idx + 1}/{total}"
        except Exception:
            pass

    return {
        "id": e["id"],
        "name": name,
        "email": e.get("contact_email") or "",
        "phone": e.get("contact_phone") or "",
        "channel": channel,
        "cadence_slug": e.get("cadence_slug") or "",
        "status": e.get("status") or "",
        "pause_reason": e.get("pause_reason") or "",
        "current_step": e.get("current_step") or 0,
        "next_run_at": e.get("next_run_at") or "",
        "last_activity": _relative_time(
            e.get("last_step_at") or e.get("updated_at") or e.get("started_at")
        ),
        "qualify_status": e["qualify_status"],
        "qualify_label": e["qualify_label"],
        "qualify_score": e["qualify_score"],
        "qualifying_progress": qualifying_progress,
        "lead_urgency": e["lead_urgency"],
        "lead_intent": e["lead_intent"],
        "column": _column_for(e),
    }


def build_pipeline(limit: int = 500) -> dict:
    """Return `{ columns: [...], totals: {...} }` derived from active
    enrollments. Shared by the HTML page and `/api/leads/pipeline`."""
    from roost.extras.lead_nurture.services.cadences import list_enrollments

    enrollments = list_enrollments(limit=limit)
    enriched = [_enrich(e) for e in enrollments]
    cards = [_enrollment_card(e) for e in enriched]

    grouped: dict[str, list[dict]] = {k: [] for k in COLUMN_KEYS}
    for c in cards:
        grouped[c["column"]].append(c)

    # Closed column collapses to most recent 20
    grouped["closed"] = grouped["closed"][:20]

    columns = [
        {"key": k, "label": COLUMN_LABELS[k], "count": len(grouped[k]), "leads": grouped[k]}
        for k in COLUMN_KEYS
    ]
    totals = {
        "total": len(cards),
        "hot": len(grouped["hot"]),
        "qualifying": len(grouped["qualifying"]),
        "nurturing": len(grouped["nurturing"]),
    }
    return {"columns": columns, "totals": totals}


def enrollment_detail(enrollment_id: int) -> dict | None:
    """Detail payload for `/api/leads/{id}` — full enrollment + qualification Q&A."""
    from roost.extras.lead_nurture.services.cadences import get_enrollment
    enr = get_enrollment(enrollment_id)
    if not enr:
        return None
    e = _enrich(enr)
    card = _enrollment_card(e)

    # Qualification answers replayed against their questions
    qualify_qa: list[dict] = []
    try:
        from roost.extras.lead_nurture.services.qualification import (
            QUESTIONS_BY_CADENCE,
        )
        questions = QUESTIONS_BY_CADENCE.get(e.get("cadence_slug") or "", [])
        answers = (enr.get("fields") or {}).get("_qualify_answers") or {}
        for q in questions:
            qualify_qa.append({
                "key": q["key"],
                "question": q["question"],
                "answer": answers.get(q["key"], ""),
            })
    except Exception:
        logger.exception("failed to render qualification Q&A")

    classification = {
        "intent": (enr.get("fields") or {}).get("_lead_intent") or "",
        "urgency": (enr.get("fields") or {}).get("_lead_urgency") or "",
        "confidence": (enr.get("fields") or {}).get("_lead_confidence"),
        "reasoning": (enr.get("fields") or {}).get("_lead_reasoning") or "",
    }

    return {
        "card": card,
        "enrollment": {
            "id": enr["id"],
            "cadence_slug": enr.get("cadence_slug"),
            "status": enr.get("status"),
            "pause_reason": enr.get("pause_reason") or "",
            "current_step": enr.get("current_step"),
            "next_run_at": enr.get("next_run_at"),
            "last_step_at": enr.get("last_step_at"),
            "started_at": enr.get("started_at"),
            "contact_name": enr.get("contact_name") or "",
            "contact_email": enr.get("contact_email") or "",
            "contact_phone": enr.get("contact_phone") or "",
            "crm_person_id": enr.get("crm_person_id") or "",
            "crm_deal_id": enr.get("crm_deal_id") or "",
        },
        "classification": classification,
        "qualification": {
            "status": e["qualify_status"],
            "label": e["qualify_label"],
            "score": e["qualify_score"],
            "channel": e["qualify_channel"],
            "qa": qualify_qa,
        },
    }


# ── Router builder ─────────────────────────────────────────────────────


def _build_pages_router():
    """Build `/leads` page router with bundle templates on the Jinja path.
    Constructed per-app-create so routes don't leak between test apps."""
    from fastapi import APIRouter, HTTPException
    from jinja2 import ChoiceLoader, FileSystemLoader

    from roost.web.pages import _base_context, templates as core_templates

    bundle_loader = FileSystemLoader(str(_BUNDLE_DIR / "templates"))
    existing = core_templates.env.loader
    if isinstance(existing, ChoiceLoader):
        if bundle_loader not in existing.loaders:
            existing.loaders = list(existing.loaders) + [bundle_loader]
    else:
        core_templates.env.loader = ChoiceLoader([existing, bundle_loader])

    pages = APIRouter()

    def _require_enabled():
        from roost.config import LEAD_NURTURE_ENABLED
        if not LEAD_NURTURE_ENABLED:
            raise HTTPException(status_code=404, detail="Lead-nurture bundle disabled")

    @pages.get("/leads")
    def leads_dashboard(request: Request):
        _require_enabled()
        pipeline = build_pipeline(limit=500)
        return core_templates.TemplateResponse("lead_nurture/dashboard.html", {
            **_base_context(request),
            "active_tab": "leads",
            "page_title": "Leads — Pipeline",
            "pipeline": pipeline,
            "pipeline_json": json.dumps(pipeline),
        })

    return pages
