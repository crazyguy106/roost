"""Channel-aware lead qualification flow.

After `ingest_lead` enrolls a new lead, this module asks 3-5 qualifying
questions back over the inbound channel (WhatsApp / WeChat / Telegram),
records the answers in `nurture_enrollments.fields_json`, scores them,
and routes to hot / warm / cold:

    hot   → notify operator (Telegram + email), resume cadence
    warm  → resume cadence (default nurture flow)
    cold  → exit enrollment

State keys written to `fields_json` (all prefixed `_qualify_*` so they
don't collide with template variables used by the cadence engine):

    _qualify_status      "in_progress" | "done" | "exited_cold" | "send_failed"
    _qualify_idx         0-based question cursor
    _qualify_channel     "whatsapp" | "wechat" | "telegram"
    _qualify_identifier  phone (whatsapp) | openid (wechat) | chat_id (telegram)
    _qualify_answers     {question_key: answer_text}
    _qualify_cadence_slug
    _qualify_score       float 0..1 (set after finalise)
    _qualify_label       "hot" | "warm" | "cold" (set after finalise)

Question packs live in this module as `QUESTIONS_BY_CADENCE` so we can
iterate on copy without a DB schema change. Future iterations may move
them into the cadence YAML.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from roost.database import get_connection
from roost.extras.lead_nurture.services.cadences.store import (
    _enrollment_row_to_dict,
    resume_enrollment,
    update_enrollment,
)

logger = logging.getLogger("roost.lead_nurture.qualification")


# ── Question packs ─────────────────────────────────────────────────────
# Per-cadence question packs. Each item:
#   key:           short slug; becomes the field name in _qualify_answers
#   question:      text sent to the lead on their channel
#   weight:        relative contribution to score (sum need not be 1.0)
#   hot_keywords:  substrings (lowercased, .lower() match) → +weight * 1.0
#   warm_keywords: substrings → +weight * 0.5
# A bare keyword match is enough — we don't NLP these, agent-author chooses
# distinctive words. Both lists are optional.

QUESTIONS_BY_CADENCE: dict[str, list[dict[str, Any]]] = {
    "property_buyer_intro": [
        {
            "key": "timeline",
            "question": (
                "Thanks for reaching out! Quick question to help me find the right "
                "places for you — when are you hoping to move in by? "
                "(e.g. 'this month', 'in 3 months', 'no rush')"
            ),
            "weight": 0.4,
            "hot_keywords": [
                "this week", "this month", "asap", "immediately", "urgent",
                "right now", "next week", "1 month", "one month",
            ],
            "warm_keywords": [
                "next month", "2 months", "3 months", "soon", "this year",
                "in a few months",
            ],
        },
        {
            "key": "budget_band",
            "question": (
                "What's your rough budget? (e.g. 'under 1M', '1-2M', '2-5M', "
                "or 'flexible')"
            ),
            "weight": 0.35,
            "hot_keywords": [
                "2m", "3m", "4m", "5m", "6m", "7m", "8m", "9m", "10m",
                "2 million", "3 million", "flexible", "no limit", "no budget",
            ],
            "warm_keywords": [
                "1m", "1.5m", "1.2m", "1.8m", "800k", "900k", "1-2m",
                "1 to 2", "1 million", "750k",
            ],
        },
        {
            "key": "mortgage_status",
            "question": (
                "Have you spoken to a bank about an in-principle approval (IPA) "
                "yet? (yes / not yet / paying cash)"
            ),
            "weight": 0.25,
            "hot_keywords": [
                "yes", "approved", "ipa", "in-principle", "in principle",
                "cash", "paying cash", "no mortgage needed",
            ],
            "warm_keywords": [
                "looking into", "next week", "soon", "this week", "planning to",
            ],
        },
    ],
}


# ── Send dispatcher (channel-aware) ────────────────────────────────────


def _send_question(channel: str, identifier: str, text: str) -> dict:
    """Send `text` to the lead on their channel. Returns adapter response
    dict; on failure returns `{"error": ...}`. Never raises."""
    try:
        if channel == "whatsapp":
            from roost.extras.messaging_external.services.whatsapp import (
                send_text_message,
            )
            return send_text_message(identifier, text)
        if channel == "wechat":
            from roost.extras.messaging_external.services.wechat import (
                send_text_message,
            )
            return send_text_message(identifier, text)
        if channel == "telegram":
            from roost.config import TELEGRAM_BOT_TOKEN
            if not TELEGRAM_BOT_TOKEN:
                return {"error": "telegram bot token not configured"}
            import httpx
            with httpx.Client(timeout=10) as client:
                r = client.post(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                    json={"chat_id": identifier, "text": text},
                )
                if r.status_code >= 300:
                    return {
                        "error": f"telegram api {r.status_code}",
                        "details": r.text[:200],
                    }
                return {"ok": True}
        return {"error": f"unknown qualification channel: {channel}"}
    except Exception as e:  # noqa: BLE001 — never block ingest on send failure
        logger.exception("qualification send failed on %s", channel)
        return {"error": str(e)}


# ── Start ──────────────────────────────────────────────────────────────


def start_qualification_if_needed(
    *,
    enrollment_id: int,
    cadence_slug: str,
    channel: str,
    identifier: str,
    contact_name: str = "",
) -> dict:
    """If there's a question pack for `cadence_slug` AND we have a valid
    addressable channel+identifier, send question 1 and pause the
    enrollment with `pause_reason='qualifying'`.

    Returns `{"started": bool, "reason": str, ...}`. Never raises.
    """
    questions = QUESTIONS_BY_CADENCE.get(cadence_slug)
    if not questions:
        return {"started": False, "reason": "no_questions"}
    if not identifier or channel not in ("whatsapp", "wechat", "telegram"):
        return {"started": False, "reason": "no_addressable_channel"}

    first_q = questions[0]["question"]
    first_name = (contact_name or "").strip().split()[0] if contact_name else ""
    if first_name and not first_q.lower().startswith("hi"):
        first_q = f"Hi {first_name}! {first_q}"
    elif first_name:
        # Replace generic opener with personalised one
        first_q = first_q.replace("Thanks for reaching out!", f"Hi {first_name}! Thanks for reaching out.", 1)

    send_result = _send_question(channel, identifier, first_q)
    if "error" in send_result:
        logger.warning(
            "qualification first-question send failed (%s) — leaving cadence to proceed: %s",
            channel, send_result.get("error"),
        )
        return {
            "started": False,
            "reason": "send_failed",
            "error": send_result.get("error"),
        }

    # Load enrollment, write state, pause.
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM nurture_enrollments WHERE id = ?", (enrollment_id,)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"started": False, "reason": "no_enrollment"}

    enr = _enrollment_row_to_dict(row)
    fields = dict(enr.get("fields") or {})
    fields["_qualify_status"] = "in_progress"
    fields["_qualify_idx"] = 0
    fields["_qualify_channel"] = channel
    fields["_qualify_identifier"] = str(identifier)
    fields["_qualify_answers"] = {}
    fields["_qualify_cadence_slug"] = cadence_slug

    update_enrollment(
        enrollment_id,
        status="paused",
        pause_reason="qualifying",
        fields=fields,
    )
    logger.info(
        "Started qualification for enrollment %d via %s (%s questions)",
        enrollment_id, channel, len(questions),
    )
    return {"started": True, "question_count": len(questions)}


# ── Lookup ─────────────────────────────────────────────────────────────


def find_active_session(channel: str, identifier: str) -> dict | None:
    """Find a paused enrollment in qualifying state for this (channel, identifier).

    Scans recent paused enrollments and filters in Python (no JSON1
    dependency needed — SQLite builds vary). Returns the enrollment dict
    or None.
    """
    if not identifier or not channel:
        return None
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM nurture_enrollments "
            "WHERE pause_reason = 'qualifying' AND status = 'paused' "
            "ORDER BY id DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()
    needle = str(identifier)
    for row in rows:
        try:
            data = json.loads(row["fields_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            data.get("_qualify_channel") == channel
            and str(data.get("_qualify_identifier")) == needle
            and data.get("_qualify_status") == "in_progress"
        ):
            return _enrollment_row_to_dict(row)
    return None


# ── Process answer ─────────────────────────────────────────────────────


def process_answer(channel: str, identifier: str, text: str) -> dict:
    """Record the lead's reply, ask the next question or finalise.

    Returns:
        {"handled": False}                              — no active session
        {"handled": True, "done": False}                — next question sent
        {"handled": True, "done": True, "label": ...}   — finalised
        {"handled": True, "done": True, "error": ...}   — gave up mid-flow
    """
    enr = find_active_session(channel, identifier)
    if not enr:
        return {"handled": False}

    fields = dict(enr.get("fields") or {})
    cadence_slug = fields.get("_qualify_cadence_slug") or ""
    questions = QUESTIONS_BY_CADENCE.get(cadence_slug, [])
    if not questions:
        return {"handled": False}

    idx = int(fields.get("_qualify_idx", 0))
    if idx >= len(questions):
        return {"handled": False}

    current_q = questions[idx]
    answers = dict(fields.get("_qualify_answers") or {})
    answers[current_q["key"]] = (text or "").strip()
    fields["_qualify_answers"] = answers
    next_idx = idx + 1

    if next_idx >= len(questions):
        # All questions answered — score + finalise.
        score_result = _score(answers, questions)
        fields["_qualify_score"] = score_result["score"]
        fields["_qualify_label"] = score_result["label"]
        fields["_qualify_status"] = "done"
        fields["_qualify_idx"] = next_idx
        _finalise(enr, fields, score_result, channel, identifier)
        return {
            "handled": True,
            "done": True,
            "label": score_result["label"],
            "score": score_result["score"],
        }

    # More questions to ask.
    fields["_qualify_idx"] = next_idx
    next_q_text = questions[next_idx]["question"]
    send_result = _send_question(channel, identifier, next_q_text)
    if "error" in send_result:
        # Can't continue — abort qualification, resume cadence so something
        # still happens for this lead.
        fields["_qualify_status"] = "send_failed"
        update_enrollment(enr["id"], fields=fields)
        resume_enrollment(enr["id"])
        return {"handled": True, "done": True, "error": send_result.get("error")}

    update_enrollment(enr["id"], fields=fields)
    return {"handled": True, "done": False, "next_idx": next_idx}


# ── Scoring ────────────────────────────────────────────────────────────


def _score(answers: dict, questions: list[dict]) -> dict:
    """Keyword-weighted score, normalised 0..1, with hot/warm/cold label."""
    total_weight = sum(float(q.get("weight", 1.0)) for q in questions) or 1.0
    raw = 0.0
    for q in questions:
        ans = (answers.get(q["key"]) or "").lower().strip()
        if not ans:
            continue
        weight = float(q.get("weight", 1.0))
        hot_kw = [k.lower() for k in q.get("hot_keywords", []) if k]
        warm_kw = [k.lower() for k in q.get("warm_keywords", []) if k]
        if any(kw in ans for kw in hot_kw):
            raw += weight * 1.0
        elif any(kw in ans for kw in warm_kw):
            raw += weight * 0.5
    score = raw / total_weight
    if score >= 0.7:
        label = "hot"
    elif score >= 0.4:
        label = "warm"
    else:
        label = "cold"
    return {"score": round(score, 3), "label": label}


# ── Finalise ───────────────────────────────────────────────────────────


def _finalise(
    enrollment: dict,
    fields: dict,
    score_result: dict,
    channel: str,
    identifier: str,
) -> None:
    """Persist final state, send a closing message to the lead, and route
    to hot / warm / cold.

    - hot  : notify operator (skip if AI CDR already flagged hot), confirm
             to client, resume cadence so subsequent steps still send.
    - warm : confirm to client, resume cadence.
    - cold : confirm + exit enrollment (status='exited').
    """
    enrollment_id = enrollment["id"]
    label = score_result["label"]

    if label == "hot":
        already_hot = (fields.get("_lead_urgency") or "").lower() == "hot"
        if not already_hot:
            try:
                from roost.extras.lead_nurture.services.leads import _notify_hot_lead
                _notify_hot_lead(
                    name=enrollment.get("contact_name") or "",
                    email=enrollment.get("contact_email") or "",
                    phone=enrollment.get("contact_phone") or "",
                    channel=channel,
                    classification={
                        "intent": "qualified_via_questions",
                        "urgency": "hot",
                        "confidence": score_result["score"],
                        "reasoning": (
                            f"Qualified via {len(fields.get('_qualify_answers') or {})} "
                            f"questions; score={score_result['score']}"
                        ),
                    },
                    crm_person_id=enrollment.get("crm_person_id") or "",
                    crm_deal_id=enrollment.get("crm_deal_id") or "",
                    enrollment_id=enrollment_id,
                )
            except Exception:
                logger.exception("hot-lead notify from qualification failed")
        _send_question(
            channel, identifier,
            "Thanks! Your agent will reach out shortly to schedule a viewing.",
        )
        update_enrollment(enrollment_id, fields=fields)
        resume_enrollment(enrollment_id)
        return

    if label == "warm":
        _send_question(
            channel, identifier,
            "Thanks for sharing — we'll send some matching listings and check "
            "back in a few days.",
        )
        update_enrollment(enrollment_id, fields=fields)
        resume_enrollment(enrollment_id)
        return

    # cold
    _send_question(
        channel, identifier,
        "Thanks for your interest — we'll stay in touch when something "
        "matches your criteria.",
    )
    fields["_qualify_status"] = "exited_cold"
    update_enrollment(
        enrollment_id,
        fields=fields,
        status="exited",
        pause_reason="qualified_cold",
    )
