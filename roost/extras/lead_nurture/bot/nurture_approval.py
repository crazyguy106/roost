"""Telegram handlers for the lead-nurture approval gate.

Commands:
  /napprove <enrollment_id>           — Send a held nurture step
  /nskip <enrollment_id> [reason]     — Skip the held step
  /nlist [paused|active]              — Show pending nurture enrollments
  /preapprove <slug> [source=X] [channel=Y] [vertical=Z] [note=…]
                                       — Pre-approve a cadence so future
                                         steps auto-send

Inline buttons on hold notifications: callback_data is `napprove:<id>`,
`nedit:<id>`, or `nskip:<id>`; routed by `handle_nurture_callback`.

Edit flow (FA-edition Phase 1A): tapping ✏️ Edit sends a force-reply
prompt seeded with the current body. The operator's reply is captured by
`handle_nurture_edit_reply` and stored as `body_override`; the held step's
keyboard is re-shown so they can then tap Approve.

These wrap `roost.extras.lead_nurture.services.nurture` and `roost.extras.lead_nurture.services.cadences` so the
operator never has to drop into MCP just to approve a draft.
"""

from __future__ import annotations

import logging

from telegram import ForceReply, Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.services import activity

logger = logging.getLogger(__name__)


_EDIT_KEY = "nedit_enrollment_id"


def _format_enrollment_brief(enr: dict) -> str:
    contact = (
        enr.get("contact_name")
        or enr.get("contact_email")
        or enr.get("contact_phone")
        or "(no contact)"
    )
    return (
        f"#{enr['id']} {enr['cadence_slug']} step {int(enr.get('current_step', 0)) + 1} "
        f"→ {contact} ({enr.get('channel', '?')})"
    )


def _kv_args(args: list[str]) -> dict[str, str]:
    """Parse `key=value` pairs out of a positional arg list. Unknown bare
    tokens are ignored. Used by /preapprove."""
    out: dict[str, str] = {}
    for tok in args:
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k.strip()] = v.strip()
    return out


# ── /napprove ─────────────────────────────────────────────────────────


@authorized
async def cmd_napprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a held nurture step: send it now, advance to the next step."""
    from roost.extras.lead_nurture.services.nurture import approve_pending

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /napprove <enrollment_id>")
        return

    enrollment_id = int(args[0])
    result = approve_pending(enrollment_id)
    if not result.get("ok"):
        msg = result.get("error") or result.get("dispatch", {}).get("detail") or "approve failed"
        await update.message.reply_text(f"Approve #{enrollment_id} failed: {msg}")
        return

    await update.message.reply_text(
        f"✅ Approved #{enrollment_id} — sent and advanced."
    )


# ── /nskip ────────────────────────────────────────────────────────────


@authorized
async def cmd_nskip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip a held nurture step without sending it."""
    from roost.extras.lead_nurture.services.nurture import skip_pending

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /nskip <enrollment_id> [reason]")
        return

    enrollment_id = int(args[0])
    reason = " ".join(args[1:]).strip()
    result = skip_pending(enrollment_id, reason=reason)
    if not result.get("ok"):
        await update.message.reply_text(
            f"Skip #{enrollment_id} failed: {result.get('error', 'unknown')}"
        )
        return

    await update.message.reply_text(f"⏭ Skipped #{enrollment_id}.")


# ── /nlist ────────────────────────────────────────────────────────────


@authorized
async def cmd_nlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List nurture enrollments. Default filter: paused (awaiting approval)."""
    from roost.extras.lead_nurture.services.cadences import list_enrollments

    args = context.args or []
    status = args[0].lower() if args else "paused"
    if status not in ("paused", "active", "completed", "exited", ""):
        await update.message.reply_text(
            "Usage: /nlist [paused|active|completed|exited]"
        )
        return

    rows = list_enrollments(status=status, limit=20)
    if not rows:
        await update.message.reply_text(f"No '{status}' enrollments.")
        return

    lines = [f"*Nurture enrollments — {status}* ({len(rows)})", ""]
    for r in rows:
        lines.append(_format_enrollment_brief(r))
    await update.message.reply_text(
        "\n".join(lines)[:3500], parse_mode="Markdown",
    )


# ── /preapprove ───────────────────────────────────────────────────────


@authorized
async def cmd_preapprove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a pre-approval rule for nurture cadences.

    Usage:
      /preapprove <cadence_slug> [source=X] [channel=Y] [vertical=Z] [note=…]
      /preapprove *  — pre-approve everything (use with care)
    """
    from roost.extras.lead_nurture.services.cadences import create_preapproval

    args = context.args or []
    if not args:
        await update.message.reply_text(
            "Usage: /preapprove <cadence_slug|*> "
            "[source=X] [channel=Y] [vertical=Z] [note=…]"
        )
        return

    slug = args[0]
    kw = _kv_args(args[1:])
    rule = create_preapproval(
        cadence_slug=slug,
        source=kw.get("source", "*"),
        channel=kw.get("channel", "*"),
        vertical=kw.get("vertical", "*"),
        note=kw.get("note", ""),
    )
    await update.message.reply_text(
        f"Pre-approval #{rule['id']} created: cadence={rule['cadence_slug']} "
        f"source={rule['source']} channel={rule['channel']} "
        f"vertical={rule['vertical']}"
    )


# ── Inline button callback ────────────────────────────────────────────


async def handle_nurture_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Route `napprove:<id>` / `nedit:<id>` / `nskip:<id>` button taps.

    Returns True if it handled the callback, False otherwise so the caller
    can keep dispatching to other callback handlers.
    """
    query = update.callback_query
    if not query or not query.data:
        return False
    data = query.data
    if not (
        data.startswith("napprove:")
        or data.startswith("nedit:")
        or data.startswith("nskip:")
    ):
        return False

    action, _, id_str = data.partition(":")
    if not id_str.isdigit():
        await query.answer("Bad callback id", show_alert=True)
        return True
    enrollment_id = int(id_str)
    _from_user = getattr(query, "from_user", None)
    actor_ref = f"tg:{_from_user.id}" if _from_user else ""

    if action == "napprove":
        from roost.extras.lead_nurture.services.nurture import approve_pending
        result = approve_pending(enrollment_id)
        activity.log_action(
            "telegram",
            "nurture.approve",
            entity_type="nurture_enrollment",
            entity_id=enrollment_id,
            ok=bool(result.get("ok")),
            result=result,
            snippet=f"Approved enrollment #{enrollment_id} via Telegram",
            actor_ref=actor_ref,
        )
        if result.get("ok"):
            await query.answer("Approved & sent")
            await query.edit_message_text(
                f"✅ Approved #{enrollment_id} — sent and advanced."
            )
        else:
            await query.answer(
                f"Approve failed: {result.get('error', 'unknown')[:60]}",
                show_alert=True,
            )
        return True

    if action == "nedit":
        from roost.extras.lead_nurture.services.cadences import get_enrollment
        enrollment = get_enrollment(enrollment_id)
        if not enrollment:
            await query.answer("Enrollment not found", show_alert=True)
            return True
        if not (enrollment.get("pause_reason") or "").startswith("awaiting_approval"):
            await query.answer(
                "Not awaiting approval — can't edit", show_alert=True,
            )
            return True
        # Mark this chat as awaiting an edit reply for this enrollment.
        chat_data = getattr(context, "chat_data", None)
        if chat_data is None:
            chat_data = {}
        chat_data[_EDIT_KEY] = enrollment_id

        # Seed the prompt with the current body (override if already edited,
        # else the original body extracted from the hold notification).
        current_body = enrollment.get("body_override") or ""
        prompt = (
            f"✏️ Edit nurture #{enrollment_id}\n\n"
            "Reply to this message with the revised body."
        )
        if current_body:
            prompt += f"\n\nCurrent draft:\n{current_body[:1000]}"
        await query.answer("Reply with the edited body")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=prompt,
            reply_markup=ForceReply(selective=True),
        )
        activity.log_action(
            "telegram",
            "nurture.edit_prompt",
            entity_type="nurture_enrollment",
            entity_id=enrollment_id,
            ok=True,
            snippet=f"Edit prompt sent for #{enrollment_id}",
            actor_ref=actor_ref,
        )
        return True

    # action == "nskip"
    from roost.extras.lead_nurture.services.nurture import skip_pending
    result = skip_pending(enrollment_id, reason="skipped via button")
    activity.log_action(
        "telegram",
        "nurture.skip",
        entity_type="nurture_enrollment",
        entity_id=enrollment_id,
        ok=bool(result.get("ok")),
        result=result,
        snippet=f"Skipped enrollment #{enrollment_id} via Telegram",
        actor_ref=actor_ref,
    )
    if result.get("ok"):
        await query.answer("Skipped")
        await query.edit_message_text(f"⏭ Skipped #{enrollment_id}.")
    else:
        await query.answer(
            f"Skip failed: {result.get('error', 'unknown')[:60]}",
            show_alert=True,
        )
    return True


# ── Force-reply capture for the edit flow ─────────────────────────────


async def handle_nurture_edit_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Capture the operator's reply to an edit prompt.

    Fires only when `chat_data[_EDIT_KEY]` is set (the operator tapped Edit
    on a hold notification). Returns True if it consumed the message so the
    catch-all agent handler doesn't also process it.
    """
    chat_data = getattr(context, "chat_data", None) or {}
    enrollment_id = chat_data.get(_EDIT_KEY)
    if not enrollment_id:
        return False
    msg = getattr(update, "message", None)
    if msg is None or not getattr(msg, "text", None):
        return False

    new_body = msg.text.strip()
    if not new_body:
        await msg.reply_text("Empty edit — keeping previous draft.")
        chat_data.pop(_EDIT_KEY, None)
        return True

    from roost.extras.lead_nurture.services.nurture import apply_draft_edit
    result = apply_draft_edit(int(enrollment_id), body=new_body)
    _from_user = getattr(msg, "from_user", None)
    actor_ref = f"tg:{_from_user.id}" if _from_user else ""
    activity.log_action(
        "telegram",
        "nurture.edit_apply",
        entity_type="nurture_enrollment",
        entity_id=int(enrollment_id),
        ok=bool(result.get("ok")),
        result={"body_len": len(new_body)},
        snippet=f"Edited body for #{enrollment_id} ({len(new_body)} chars)",
        actor_ref=actor_ref,
    )
    chat_data.pop(_EDIT_KEY, None)

    if not result.get("ok"):
        await msg.reply_text(
            f"Edit #{enrollment_id} failed: {result.get('error', 'unknown')}"
        )
        return True

    keyboard = {
        "inline_keyboard": [[
            {"text": "✅ Approve", "callback_data": f"napprove:{enrollment_id}"},
            {"text": "✏️ Edit", "callback_data": f"nedit:{enrollment_id}"},
            {"text": "⏭ Skip", "callback_data": f"nskip:{enrollment_id}"},
        ]]
    }
    await context.bot.send_message(
        chat_id=msg.chat_id,
        text=(
            f"✏️ Draft updated for #{enrollment_id} "
            f"({len(new_body)} chars). Approve to send."
        ),
        reply_markup=keyboard,
    )
    return True
