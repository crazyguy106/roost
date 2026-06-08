"""Lead-nurture cadence engine.

Reads `nurture_enrollments` whose `next_run_at` has elapsed, builds the
current step's message from a `response_templates` row, and either:

  * **schedules / sends** the message immediately (if a `cadence_preapprovals`
    rule matches the cadence + source + channel + vertical), or
  * **pauses** the enrollment with `pause_reason='awaiting_approval:<step>'`
    and sends a Telegram notification asking the operator to approve.

After every send (auto or approved) the engine logs a communication to the
CRM via `crm.log_communication` and computes the next step's `next_run_at`.
When the last step fires, the enrollment is marked `completed`.

Channels supported:
  * **email** — queued via `scheduled_emails.schedule_email`
  * **whatsapp** — sent via `whatsapp.send_text_message` (best effort; needs
    24h-window check before the engine actually fires it — see notes inline)
  * **telegram** — broadcast via the bot HTTP API (operator-side test channel)
  * **sms** — sent via `sms.send_sms` (Twilio REST, fail-closed on missing creds)

Driven by a periodic call to `tick()` from a scheduler (APScheduler in the
bot, or a separate `python -m roost.nurture_tick` cron) — not bound to any
process.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from roost.config import TELEGRAM_ALLOWED_USERS, TELEGRAM_BOT_TOKEN
from roost.extras.lead_nurture.services import cadences as cadences_svc
from roost.services import response_templates as templates_svc

logger = logging.getLogger("roost.extras.lead_nurture.services.nurture")


# ── Time helpers ───────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_str(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _step_run_at(step: dict, base: datetime) -> datetime:
    """Compute when `step` should fire, given an enrollment start `base`.

    Step keys honored:
      day_offset (int, required)   — days from base
      hour       (int, optional)   — hour-of-day in `tz` (default UTC)
      minute_offset (int, optional) — minutes added to base (overrides hour)
      tz         (str, optional)   — IANA zone for `hour` (default 'UTC')
    """
    day_offset = int(step.get("day_offset", 0))
    minute_offset = step.get("minute_offset")

    if minute_offset is not None:
        return base + timedelta(days=day_offset, minutes=int(minute_offset))

    hour = step.get("hour")
    if hour is None:
        return base + timedelta(days=day_offset)

    tz_name = step.get("tz", "UTC")
    tz = ZoneInfo(tz_name)
    target_local = (base.astimezone(tz) + timedelta(days=day_offset)).replace(
        hour=int(hour), minute=0, second=0, microsecond=0
    )
    return target_local.astimezone(timezone.utc)


# ── Telegram notification helper ───────────────────────────────────────


def _notify_telegram(text: str, *, reply_markup: dict | None = None) -> None:
    """Best-effort broadcast to TELEGRAM_ALLOWED_USERS. Logs and swallows errors.

    `reply_markup` is the raw Bot API reply_markup dict (for inline keyboards).
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ALLOWED_USERS:
        logger.debug("Telegram not configured — skipping nurture notification")
        return
    try:
        import httpx
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        with httpx.Client(timeout=10) as client:
            for uid in TELEGRAM_ALLOWED_USERS:
                payload: dict = {"chat_id": uid, "text": text}
                if reply_markup is not None:
                    payload["reply_markup"] = reply_markup
                client.post(url, json=payload)
    except Exception:
        logger.exception("Failed to notify Telegram for nurture step")


# ── Message build + dispatch ───────────────────────────────────────────


_AGENT_FIELD_KEYS = (
    "agent_name", "agent_firm", "agent_signoff", "agent_booking_link", "agent_cea_no",
)
_DEFAULT_AGENT_FIELDS = {
    "agent_signoff": "Best regards",
    "property_interest": "your enquiry",
    "preferred_area": "the area you're looking at",
}


def _env_agent_fields() -> dict:
    """Operator identity from .env (config) — the base layer under settings."""
    from roost import config as _cfg
    return {
        "agent_name": _cfg.AGENT_NAME,
        "agent_firm": _cfg.AGENT_FIRM,
        "agent_signoff": _cfg.AGENT_SIGNOFF,
        "agent_booking_link": _cfg.AGENT_BOOKING_LINK,
        "agent_cea_no": _cfg.AGENT_CEA_NO,
    }


def _build_message(
    template_name: str,
    fields: dict,
    channel: str,
    *,
    user_id: str = "",
) -> dict:
    """Resolve the template by name, interpolate fields, return {subject, body}.

    Merges agent identity from settings (`agent_name`, `agent_cea_no`,
    `agent_signoff`) and applies safe defaults for common cadence vars so
    templates degrade gracefully when context is missing.

    Raises ValueError if the template does not exist.
    """
    tmpl = templates_svc.get_template_by_name(template_name)
    if "error" in tmpl:
        raise ValueError(f"template '{template_name}' not found")

    # Operator identity precedence: caller fields > settings page > .env > defaults.
    fields = dict(fields)
    env_identity = _env_agent_fields()
    try:
        from roost.services.settings import get_setting
        uid = int(user_id) if user_id else None
        for key in _AGENT_FIELD_KEYS:
            val = (get_setting(key, user_id=uid) if uid else get_setting(key)) \
                or env_identity.get(key, "")
            if val:
                fields.setdefault(key, val)
    except Exception:
        logger.debug("Could not load agent_* settings; using .env/defaults", exc_info=True)
        for key, val in env_identity.items():
            if val:
                fields.setdefault(key, val)
    for k, v in _DEFAULT_AGENT_FIELDS.items():
        if not fields.get(k):
            fields[k] = v

    subject = templates_svc.fill_template(tmpl.get("subject", ""), fields)
    body = templates_svc.fill_template(tmpl.get("body", ""), fields)
    return {
        "subject": subject,
        "body": body,
        "template_id": tmpl.get("id"),
        "template_name": template_name,
        "channel": channel,
    }


def _dispatch_send(*, enrollment: dict, message: dict, when_utc: datetime) -> dict:
    """Schedule/send through the right channel. Returns {ok, channel, ref, detail}."""
    channel = message["channel"]
    when_str = _utc_str(when_utc)

    if channel == "email":
        from roost.services.scheduled_emails import schedule_email
        to = enrollment.get("contact_email") or ""
        if not to:
            return {"ok": False, "channel": channel, "detail": "no contact_email"}
        try:
            res = schedule_email(
                provider="gmail",
                to=to,
                subject=message["subject"],
                body=message["body"],
                scheduled_at=when_str,
                user_id=int(enrollment["user_id"]) if enrollment.get("user_id") else None,
            )
            return {"ok": True, "channel": channel, "ref": str(res.get("id")), "detail": "queued"}
        except Exception as e:
            logger.exception("schedule_email failed")
            return {"ok": False, "channel": channel, "detail": str(e)}

    if channel == "chatwoot":
        # Inbound from Chatwoot stamps identifier as the WhatsApp phone.
        # Delegate to the WhatsApp service — since FA-G it routes through
        # Chatwoot REST when CHATWOOT_ENABLED=true. The Chatwoot route returns
        # {ok, message_id, via, conversation_id}; Meta-direct returns
        # {ok, messages: [{id}]}. Read both shapes when extracting ref.
        from roost.extras.messaging_external.services.whatsapp import send_text_message
        to = enrollment.get("contact_phone") or ""
        if not to:
            return {"ok": False, "channel": channel, "detail": "no contact_phone"}
        try:
            res = send_text_message(to=to, text=message["body"])
            ref = (
                (res or {}).get("message_id")
                or (res or {}).get("messages", [{}])[0].get("id", "")
            )
            return {"ok": True, "channel": channel, "ref": str(ref), "detail": "sent"}
        except Exception as e:
            logger.exception("chatwoot send failed")
            return {"ok": False, "channel": channel, "detail": str(e)}

    if channel == "whatsapp":
        # NOTE: WhatsApp Cloud API requires the recipient to have messaged us
        # within the last 24h before we can send free-form text. The engine
        # does not check that here — the WhatsApp service will reject if the
        # window is closed, and we'll surface the error in the result.
        from roost.extras.messaging_external.services.whatsapp import send_text_message
        to = enrollment.get("contact_phone") or ""
        if not to:
            return {"ok": False, "channel": channel, "detail": "no contact_phone"}
        try:
            res = send_text_message(to=to, text=message["body"])
            ref = (res or {}).get("messages", [{}])[0].get("id", "")
            return {"ok": True, "channel": channel, "ref": ref, "detail": "sent"}
        except Exception as e:
            logger.exception("whatsapp send failed")
            return {"ok": False, "channel": channel, "detail": str(e)}

    if channel == "telegram":
        # Customer DM via Bot API. Requires the customer to have messaged
        # the bot first (Telegram bots cannot cold-DM). If we have no
        # chat_id on the enrollment, surface the gap so the operator can
        # nudge the contact to start the chat manually.
        from roost.extras.messaging_external.services.telegram_out import (
            send_text_message as telegram_send,
        )
        to = enrollment.get("contact_telegram_chat_id") or ""
        if not to:
            return {
                "ok": False,
                "channel": channel,
                "detail": "no contact_telegram_chat_id",
            }
        try:
            res = telegram_send(chat_id=to, body=message["body"])
        except Exception as e:
            logger.exception("telegram send failed")
            return {"ok": False, "channel": channel, "detail": str(e)}
        if not res.get("ok"):
            return {
                "ok": False,
                "channel": channel,
                "detail": res.get("error") or "send failed",
            }
        return {
            "ok": True,
            "channel": channel,
            "ref": res.get("message_id", ""),
            "detail": "sent",
        }

    if channel == "sms":
        from roost.extras.messaging_external.services.sms import send_sms
        to = enrollment.get("contact_phone") or ""
        if not to:
            return {"ok": False, "channel": channel, "detail": "no contact_phone"}
        try:
            res = send_sms(to=to, body=message["body"])
        except Exception as e:
            logger.exception("sms send failed")
            return {"ok": False, "channel": channel, "detail": str(e)}
        if not res.get("ok"):
            return {"ok": False, "channel": channel, "detail": res.get("error") or "send failed"}
        return {"ok": True, "channel": channel, "ref": res.get("message_id", ""), "detail": "sent"}

    return {"ok": False, "channel": channel, "detail": f"unsupported channel: {channel}"}


# ── Approval-gate decision ─────────────────────────────────────────────


def _is_preapproved(enrollment: dict, cadence: dict) -> dict | None:
    """Return the matching preapproval rule (or None)."""
    return cadences_svc.match_preapproval(
        cadence_slug=enrollment["cadence_slug"],
        source=enrollment.get("source", ""),
        channel=enrollment.get("channel", "email"),
        vertical=cadence.get("vertical", "generic"),
        user_id=enrollment.get("user_id", ""),
    )


# ── Step advancement ───────────────────────────────────────────────────


def _log_to_crm(enrollment: dict, message: dict, dispatch: dict) -> None:
    """Best-effort: append a note + log communication to the CRM."""
    from roost.extras.crm.services import CrmError, get_provider  # lazy: cross-bundle

    person_id = enrollment.get("crm_person_id") or ""
    if not person_id:
        return
    try:
        provider = get_provider()
        provider.log_communication(
            person_id=person_id,
            channel=message["channel"],
            direction="outbound",
            subject=message.get("subject") or message["template_name"],
            content=message.get("body", ""),
        )
    except CrmError as e:
        logger.warning("CRM log_communication failed: %s", e)
    except Exception:
        logger.exception("Unexpected error logging to CRM")


def _complete_enrollment(enrollment_id: int) -> dict:
    return cadences_svc.update_enrollment(
        enrollment_id, status="completed", next_run_at=None,
        last_step_at=_utc_str(_utc_now()),
    )


def _hold_for_approval(enrollment_id: int, *, step_index: int, message: dict) -> dict:
    """Pause the enrollment and notify Telegram for human approval."""
    reason = f"awaiting_approval:{step_index}"
    # Clear any stale overrides from a previous held step — a fresh hold
    # starts from the template body so operator edits don't carry across.
    cadences_svc.update_enrollment(
        enrollment_id,
        status="paused",
        pause_reason=reason,
        last_step_at=_utc_str(_utc_now()),
        body_override=None,
        subject_override=None,
    )
    enrollment = cadences_svc.get_enrollment(enrollment_id)
    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"napprove:{enrollment_id}"},
            {"text": "✏️ Edit", "callback_data": f"nedit:{enrollment_id}"},
            {"text": "⏭ Skip", "callback_data": f"nskip:{enrollment_id}"},
        ]]
    }
    _notify_telegram(
        "🟡 Nurture step awaiting approval\n\n"
        f"Enrollment #{enrollment_id} ({enrollment['cadence_slug']}, step {step_index + 1})\n"
        f"To: {enrollment.get('contact_name') or enrollment.get('contact_email') or enrollment.get('contact_phone')}\n"
        f"Channel: {message['channel']}\n"
        f"Subject: {message.get('subject') or '(none)'}\n"
        f"---\n{(message.get('body') or '')[:500]}\n"
        f"---\n/napprove {enrollment_id}  |  /nskip {enrollment_id}",
        reply_markup=keyboard,
    )
    return enrollment


def apply_draft_edit(
    enrollment_id: int,
    body: str,
    subject: str | None = None,
) -> dict:
    """Stash an operator-edited body (and optional subject) on a held step.

    When the enrollment is later approved, `approve_pending` will use these
    overrides instead of re-rendering the template. Only allowed while the
    enrollment is paused with `awaiting_approval:*`.
    """
    enrollment = cadences_svc.get_enrollment(enrollment_id)
    if not enrollment:
        return {"ok": False, "error": f"enrollment {enrollment_id} not found"}
    if not (enrollment.get("pause_reason") or "").startswith("awaiting_approval"):
        return {
            "ok": False,
            "error": (
                "enrollment is not awaiting approval "
                f"(status={enrollment['status']}, "
                f"pause_reason={enrollment['pause_reason']})"
            ),
        }
    updates: dict = {"body_override": body}
    if subject is not None:
        updates["subject_override"] = subject
    cadences_svc.update_enrollment(enrollment_id, **updates)
    return {"ok": True, "enrollment_id": enrollment_id, "body": body, "subject": subject}


def _schedule_next_step(enrollment: dict, cadence: dict, *, just_ran_index: int) -> dict:
    """Compute next_run_at for the step after `just_ran_index`. Marks completed
    if there are no more steps."""
    steps = cadence["steps"]
    next_index = just_ran_index + 1
    if next_index >= len(steps):
        return _complete_enrollment(enrollment["id"])

    base_dt = datetime.fromisoformat(enrollment["started_at"].replace("Z", "+00:00"))
    if base_dt.tzinfo is None:
        base_dt = base_dt.replace(tzinfo=timezone.utc)
    next_at = _step_run_at(steps[next_index], base_dt)
    return cadences_svc.update_enrollment(
        enrollment["id"],
        current_step=next_index,
        next_run_at=_utc_str(next_at),
        last_step_at=_utc_str(_utc_now()),
        status="active",
        pause_reason="",
    )


def _parse_iso_utc(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _handle_wait_for_reply(
    *,
    enrollment_id: int,
    enrollment: dict,
    cadence: dict,
    step: dict,
    step_index: int,
) -> dict:
    """`wait_for_reply` gate: if the lead has replied since the previous
    step ran, exit the cadence (reply received — handing back to human).

    If no reply yet and `timeout_days` (default 7) has not elapsed since
    this step's scheduled fire, push next_run_at forward and stay active.

    If timeout reached without reply, advance to the next step.
    """
    since_str = (
        enrollment.get("last_step_at")
        or enrollment.get("started_at")
        or ""
    )
    since = _parse_iso_utc(since_str)
    last_inbound = _parse_iso_utc(enrollment.get("last_inbound_at"))

    if last_inbound and since and last_inbound > since:
        cadences_svc.update_enrollment(
            enrollment_id,
            status="exited",
            pause_reason="reply_received",
            next_run_at=None,
            last_step_at=_utc_str(_utc_now()),
        )
        return {
            "ok": True,
            "action": "exited_replied",
            "enrollment_id": enrollment_id,
            "step_index": step_index,
        }

    # No reply yet. The deadline must be anchored to the step's *original*
    # scheduled fire time, not to enrollment["next_run_at"] — otherwise
    # each deferral would push the deadline forward by another
    # `timeout_days` and the gate would never expire.
    timeout_days = int(step.get("timeout_days", 7))
    now = _utc_now()
    started = _parse_iso_utc(enrollment.get("started_at"))
    if timeout_days > 0 and started is not None:
        original_fire_at = _step_run_at(step, started)
        deadline = original_fire_at + timedelta(days=timeout_days)
        if now < deadline:
            cadences_svc.update_enrollment(
                enrollment_id,
                next_run_at=_utc_str(deadline),
                pause_reason=f"waiting_for_reply:{step_index}",
            )
            return {
                "ok": True,
                "action": "waiting_for_reply",
                "enrollment_id": enrollment_id,
                "step_index": step_index,
                "deadline": _utc_str(deadline),
            }

    # Timeout elapsed (or no timeout configured) — advance.
    _schedule_next_step(enrollment, cadence, just_ran_index=step_index)
    return {
        "ok": True,
        "action": "wait_timeout",
        "enrollment_id": enrollment_id,
        "step_index": step_index,
    }


def advance_enrollment(enrollment_id: int) -> dict:
    """Run one step of an enrollment. Honors pre-approval rules.

    Returns: {ok, action, enrollment_id, step_index, dispatch?, error?}
        action ∈ {sent, held_for_approval, completed, error}
    """
    enrollment = cadences_svc.get_enrollment(enrollment_id)
    if not enrollment:
        return {"ok": False, "action": "error", "error": f"enrollment {enrollment_id} not found"}
    if enrollment["status"] != "active":
        return {"ok": False, "action": "error",
                "error": f"enrollment status is {enrollment['status']}, not active"}

    cadence = cadences_svc.get_cadence(enrollment["cadence_id"])
    if not cadence:
        return {"ok": False, "action": "error",
                "error": f"cadence {enrollment['cadence_id']} missing"}

    steps = cadence["steps"]
    step_index = int(enrollment["current_step"])
    if step_index >= len(steps):
        _complete_enrollment(enrollment_id)
        return {"ok": True, "action": "completed", "enrollment_id": enrollment_id}

    step = steps[step_index]
    if step.get("type") == "wait_for_reply":
        return _handle_wait_for_reply(
            enrollment_id=enrollment_id,
            enrollment=enrollment,
            cadence=cadence,
            step=step,
            step_index=step_index,
        )
    channel = step.get("channel") or enrollment.get("channel", "email")
    try:
        message = _build_message(
            step["template"], enrollment.get("fields") or {}, channel,
            user_id=str(enrollment.get("user_id") or ""),
        )
    except ValueError as e:
        cadences_svc.update_enrollment(
            enrollment_id, status="paused", pause_reason=f"template_error: {e}"
        )
        return {"ok": False, "action": "error", "error": str(e),
                "enrollment_id": enrollment_id, "step_index": step_index}

    rule = _is_preapproved(enrollment, cadence)
    if rule is None:
        _hold_for_approval(enrollment_id, step_index=step_index, message=message)
        return {
            "ok": True, "action": "held_for_approval",
            "enrollment_id": enrollment_id, "step_index": step_index,
            "message_preview": {"subject": message["subject"],
                                "body": message["body"][:200]},
        }

    dispatch = _dispatch_send(
        enrollment=enrollment, message=message, when_utc=_utc_now(),
    )
    if not dispatch["ok"]:
        cadences_svc.update_enrollment(
            enrollment_id, status="paused",
            pause_reason=f"dispatch_error: {dispatch.get('detail')}",
        )
        return {"ok": False, "action": "error",
                "error": dispatch.get("detail"),
                "enrollment_id": enrollment_id, "step_index": step_index}

    _log_to_crm(enrollment, message, dispatch)
    _schedule_next_step(enrollment, cadence, just_ran_index=step_index)
    return {
        "ok": True, "action": "sent",
        "enrollment_id": enrollment_id, "step_index": step_index,
        "dispatch": dispatch,
    }


def approve_pending(enrollment_id: int) -> dict:
    """Release a held step: send it now, log, advance to next step."""
    enrollment = cadences_svc.get_enrollment(enrollment_id)
    if not enrollment:
        return {"ok": False, "error": f"enrollment {enrollment_id} not found"}
    if not enrollment["pause_reason"].startswith("awaiting_approval"):
        return {"ok": False,
                "error": f"enrollment is not awaiting approval (status={enrollment['status']}, "
                         f"pause_reason={enrollment['pause_reason']})"}

    cadence = cadences_svc.get_cadence(enrollment["cadence_id"])
    step_index = int(enrollment["current_step"])
    step = cadence["steps"][step_index]
    channel = step.get("channel") or enrollment.get("channel", "email")
    message = _build_message(
        step["template"], enrollment.get("fields") or {}, channel,
        user_id=str(enrollment.get("user_id") or ""),
    )
    # Apply operator-edited overrides (FA-edition Phase 1A). If the operator
    # tapped Edit on the hold notification and force-replied with revised
    # text, that text wins over the template-rendered body/subject.
    body_override = enrollment.get("body_override")
    if body_override:
        message["body"] = body_override
    subject_override = enrollment.get("subject_override")
    if subject_override:
        message["subject"] = subject_override
    dispatch = _dispatch_send(enrollment=enrollment, message=message, when_utc=_utc_now())

    if not dispatch["ok"]:
        cadences_svc.update_enrollment(
            enrollment_id, pause_reason=f"dispatch_error: {dispatch.get('detail')}"
        )
        return {"ok": False, "action": "error", "error": dispatch.get("detail"),
                "enrollment_id": enrollment_id}

    # Re-fetch in case dispatch mutated state.
    enrollment = cadences_svc.get_enrollment(enrollment_id)
    _log_to_crm(enrollment, message, dispatch)
    _schedule_next_step(enrollment, cadence, just_ran_index=step_index)
    return {"ok": True, "action": "sent_after_approval",
            "enrollment_id": enrollment_id, "step_index": step_index,
            "dispatch": dispatch}


def skip_pending(enrollment_id: int, reason: str = "") -> dict:
    """Skip the currently held step without sending. Advance past it."""
    enrollment = cadences_svc.get_enrollment(enrollment_id)
    if not enrollment:
        return {"ok": False, "error": f"enrollment {enrollment_id} not found"}
    cadence = cadences_svc.get_cadence(enrollment["cadence_id"])
    step_index = int(enrollment["current_step"])
    _schedule_next_step(enrollment, cadence, just_ran_index=step_index)
    cadences_svc.update_enrollment(
        enrollment_id, pause_reason=f"skipped: {reason}" if reason else "skipped"
    )
    return {"ok": True, "action": "skipped",
            "enrollment_id": enrollment_id, "step_index": step_index}


def tick(*, now_utc: datetime | None = None, max_per_tick: int = 50) -> dict:
    """Process every enrollment whose next_run_at has elapsed. Idempotent
    enough for a 1-minute cron — each enrollment only advances one step
    per call."""
    now_utc = now_utc or _utc_now()
    due = cadences_svc.list_due_enrollments(now_utc=_utc_str(now_utc), limit=max_per_tick)
    processed: list[dict] = []
    for e in due:
        try:
            processed.append(advance_enrollment(e["id"]))
        except Exception as ex:
            logger.exception("nurture.advance_enrollment failed for %s", e["id"])
            processed.append({"ok": False, "action": "error",
                              "enrollment_id": e["id"], "error": str(ex)})
    return {
        "ticked_at": _utc_str(now_utc),
        "due": len(due),
        "processed": processed,
        "sent": sum(1 for p in processed if p.get("action") == "sent"),
        "held": sum(1 for p in processed if p.get("action") == "held_for_approval"),
        "completed": sum(1 for p in processed if p.get("action") == "completed"),
        "errors": sum(1 for p in processed if not p.get("ok")),
    }
