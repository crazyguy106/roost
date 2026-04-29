"""Telegram handlers for RPA flows.

Two responsibilities:
  1. When a user has an awaiting_input RPA run, intercept the next plain
     text message and feed it back into the run (registered at group=-1
     so it runs ahead of capture/triage/agent handlers).
  2. /rpa list and /rpa cancel <id> commands.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

from roost.database import get_connection
from roost.services import rpa_runs

logger = logging.getLogger("roost.bot.rpa_input")

try:
    from telegram.ext import ApplicationHandlerStop as _Stop
except ImportError:
    class _Stop(Exception):
        pass


def _resolve_user_id(telegram_id: int) -> str:
    """Map a Telegram user id to a Roost user_id string.

    Looks up `users.telegram_id`. Falls back to the telegram_id itself
    (as a string) for single-user setups where linking hasn't been done.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,)
        ).fetchone()
        if row and row["id"]:
            return str(row["id"])
    except Exception:
        pass
    finally:
        conn.close()
    return str(telegram_id)


async def handle_rpa_input_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If the user has an awaiting RPA run, consume their next message as the answer."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    telegram_id = update.effective_user.id
    user_id = _resolve_user_id(telegram_id)

    run = rpa_runs.find_awaiting_for_user(user_id)
    if not run:
        return  # Let other handlers process it

    if rpa_runs.submit_input(run["id"], text):
        await update.message.reply_text(
            f"Got it — fed into RPA run #{run['id']} ({run['portal_slug']})."
        )
        raise _Stop()


async def cmd_rpa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Top-level /rpa command. Subcommands: list | cancel <id>."""
    if not update.message:
        return
    args = context.args or []
    telegram_id = update.effective_user.id
    user_id = _resolve_user_id(telegram_id)

    sub = (args[0] if args else "list").lower()

    if sub == "list":
        runs = rpa_runs.list_runs(user_id=user_id, limit=10)
        if not runs:
            await update.message.reply_text("No RPA runs yet.")
            return
        lines = ["Recent RPA runs:"]
        for r in runs:
            tag = ""
            if r["status"] == "awaiting_input":
                tag = f" — waiting: {r.get('prompt_text','')[:60]}"
            lines.append(f"#{r['id']} {r['portal_slug']} [{r['status']}]{tag}")
        await update.message.reply_text("\n".join(lines))
        return

    if sub == "cancel":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("Usage: /rpa cancel <id>")
            return
        run_id = int(args[1])
        ok = rpa_runs.cancel(run_id)
        await update.message.reply_text(
            f"RPA run #{run_id} cancelled." if ok else f"Could not cancel run #{run_id}."
        )
        return

    if sub == "new":
        await update.message.reply_text(
            "Let's build a new RPA flow.\n\n"
            "Tell me — in plain English — what you'd like the bot to do, "
            "and the URL of the portal. For example:\n\n"
            "  > Log into my AIA broker portal at https://www.aia.com.sg/portal/login, "
            "wait for the OTP they email me, and download every policy statement on the dashboard.\n\n"
            "I'll inspect the page, draft the flow, ask for any credentials I need, "
            "and walk you through testing it. You won't need to touch any YAML."
        )
        return

    if sub == "run":
        if len(args) < 2:
            await update.message.reply_text("Usage: /rpa run <portal> [param=value ...]")
            return
        portal = args[1]
        params: dict = {}
        for kv in args[2:]:
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k] = v
        try:
            from roost.services import rpa_flows
            run_id = rpa_runs.create_run(user_id=user_id, portal_slug=portal)
            await update.message.reply_text(
                f"Started RPA run #{run_id} for {portal}. I'll ping you if it needs an OTP."
            )
            import asyncio
            async def _go():
                try:
                    await rpa_flows.dispatch(portal, run_id, user_id, params)
                except Exception as e:
                    rpa_runs.fail(run_id, str(e))
            asyncio.create_task(_go())
        except Exception as e:
            await update.message.reply_text(f"Couldn't start: {e}")
        return

    await update.message.reply_text(
        "Usage:\n"
        "  /rpa new                 — guided flow builder (chat with the bot)\n"
        "  /rpa list                — show recent runs\n"
        "  /rpa run <portal> [k=v]  — run a configured flow\n"
        "  /rpa cancel <id>         — cancel a run"
    )
