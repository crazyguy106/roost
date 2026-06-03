"""Telegram handlers for the Guardian draft queue.

When `guardian_gate` parks a money-moving tool call as a pending draft,
it pushes a Telegram notification with ✅ Approve / ❌ Reject inline
buttons (see `roost.services.guardian._notify_telegram_about_draft`).

This module provides:
- `/gdrafts` — list pending drafts (operator view).
- `handle_guardian_draft_callback` — handle button taps, dispatching
  `approve_draft` / `reject_draft` and ack-ing the user.

Audit-logged via `roost.services.activity.log_action`.
"""

from __future__ import annotations

import json
import logging

from roost.services import activity
from roost.services import guardian as guardian_svc

logger = logging.getLogger(__name__)


async def cmd_gdrafts(update, context) -> None:
    """List pending Guardian drafts."""
    drafts = guardian_svc.list_pending_drafts(limit=20)
    if not drafts:
        await update.message.reply_text("No pending Guardian drafts.")
        return

    lines = [f"Pending Guardian drafts ({len(drafts)}):"]
    for d in drafts:
        args_preview = json.dumps(d.get("args", {}), default=str)[:120]
        lines.append(
            f"#{d['id']} — {d['tool_name']}\n  args: {args_preview}"
        )
    await update.message.reply_text("\n".join(lines))


async def handle_guardian_draft_callback(update, context) -> None:
    """Handle taps on Guardian draft inline keyboards.

    Callback data: `gdraft:approve:<id>` or `gdraft:reject:<id>`.
    """
    query = update.callback_query
    data = query.data or ""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "gdraft":
        await query.answer()
        return

    action = parts[1]
    try:
        draft_id = int(parts[2])
    except ValueError:
        await query.answer("Invalid draft id", show_alert=True)
        return

    _from_user = getattr(query, "from_user", None)
    actor_ref = f"tg:{_from_user.id}" if _from_user else ""

    if action == "approve":
        result = guardian_svc.approve_draft(draft_id)
        status = result.get("status", "")
        if result.get("ok"):
            await query.answer(f"✅ Approved & executed #{draft_id}")
            snippet = f"approved draft #{draft_id}"
        else:
            await query.answer(
                f"Approve failed: {status}", show_alert=True,
            )
            snippet = f"approve draft #{draft_id} failed: {status}"
        activity.log_action(
            actor="telegram",
            action="guardian.approve",
            entity_type="guardian_draft",
            entity_id=draft_id,
            ok=bool(result.get("ok")),
            result=result,
            snippet=snippet,
            actor_ref=actor_ref,
        )
        return

    if action == "reject":
        result = guardian_svc.reject_draft(
            draft_id, reason=f"rejected via telegram by {actor_ref}",
        )
        if result.get("ok"):
            await query.answer(f"❌ Rejected #{draft_id}")
            snippet = f"rejected draft #{draft_id}"
        else:
            await query.answer(
                f"Reject failed: {result.get('status', '')}",
                show_alert=True,
            )
            snippet = (
                f"reject draft #{draft_id} failed: "
                f"{result.get('status', '')}"
            )
        activity.log_action(
            actor="telegram",
            action="guardian.reject",
            entity_type="guardian_draft",
            entity_id=draft_id,
            ok=bool(result.get("ok")),
            result=result,
            snippet=snippet,
            actor_ref=actor_ref,
        )
        return

    await query.answer()
