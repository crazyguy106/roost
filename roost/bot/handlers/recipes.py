"""Bot handlers for automation recipes, templates, schedules, and rollback.

Commands:
  /recipe [name]      — List recipes or show a specific one
  /schedule <text>    — Create a scheduled automation from natural language
  /template [name]    — List templates or show a specific one
  /sequence [group]   — Show a template sequence
  /approve <run_id>   — Approve a pending recipe run
  /skip <run_id>      — Skip a pending recipe run
  /rollback [id]      — List checkpoints or rollback an action
"""

import logging

from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.handlers.common import escape_md
from roost.services import activity

logger = logging.getLogger(__name__)


_RECIPE_EDIT_KEY = "redit_run_id"


@authorized
async def cmd_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recipes or show details of a specific recipe."""
    from roost.services.recipes import list_recipes, get_recipe, get_recipe_by_name

    args = context.args or []
    text = " ".join(args).strip()

    if not text:
        # List all recipes
        recipes = list_recipes()
        if not recipes:
            await update.message.reply_text("No recipes yet. Create one via MCP tools.")
            return

        lines = ["*Automation Recipes:*\n"]
        for r in recipes:
            enabled = "on" if r.get("enabled") else "off"
            tier = r.get("risk_tier", "read_only")
            runs = r.get("run_count", 0)
            lines.append(
                f"  #{r['id']} {escape_md(r['name'])} "
                f"[{tier}] ({enabled}, {runs} runs)"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Show specific recipe
    if text.isdigit():
        recipe = get_recipe(int(text))
    else:
        recipe = get_recipe_by_name(text)

    if "error" in recipe:
        await update.message.reply_text(recipe["error"])
        return

    tier_emoji = {"read_only": "R", "internal_write": "W", "external_write": "X"}
    tier = recipe.get("risk_tier", "read_only")
    enabled = "Enabled" if recipe.get("enabled") else "Disabled"
    template_ids = recipe.get("template_ids", [])

    lines = [
        f"*Recipe #{recipe['id']}:* {escape_md(recipe['name'])}",
        f"Trigger: {recipe.get('trigger_type', 'manual')}",
        f"Risk: {tier} [{tier_emoji.get(tier, '?')}]",
        f"Status: {enabled} | Runs: {recipe.get('run_count', 0)}",
    ]
    if recipe.get("description"):
        lines.append(f"Description: {escape_md(recipe['description'])}")
    if template_ids:
        lines.append(f"Templates: {', '.join(str(t) for t in template_ids)}")

    # Action buttons
    buttons = []
    if recipe.get("enabled"):
        buttons.append(InlineKeyboardButton(
            "Disable", callback_data=f"recipe:disable:{recipe['id']}"))
    else:
        buttons.append(InlineKeyboardButton(
            "Enable", callback_data=f"recipe:enable:{recipe['id']}"))
    buttons.append(InlineKeyboardButton(
        "Runs", callback_data=f"recipe:runs:{recipe['id']}"))

    keyboard = InlineKeyboardMarkup([buttons]) if buttons else None
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


@authorized
async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Create a scheduled automation from natural language.

    Usage: /schedule Every Monday at 9am, summarize my unread emails
    """
    from roost.services.natural_cron import (
        parse_natural_schedule,
        schedule_to_trigger_config,
    )
    from roost.services.recipes import create_recipe, list_recipes

    args = context.args or []
    text = " ".join(args).strip()

    if not text:
        # List existing cron recipes
        recipes = list_recipes(trigger_type="cron", enabled_only=True)
        if not recipes:
            await update.message.reply_text(
                "No scheduled automations yet.\n\n"
                "Usage: /schedule <natural language description>\n"
                "Example: /schedule Every Monday at 9am, summarize my unread emails"
            )
            return

        lines = ["*Scheduled Automations:*\n"]
        for r in recipes:
            config = r.get("trigger_config", "")
            lines.append(f"  #{r['id']} {escape_md(r['name'])} ({config})")
        lines.append("\nUse /recipe <id> for details.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Parse natural language
    parsed = parse_natural_schedule(text)
    trigger_config = schedule_to_trigger_config(parsed)

    # Create the recipe
    recipe = create_recipe(
        name=parsed.get("name", "Scheduled task"),
        instructions=parsed.get("instructions", text),
        description=f"Auto-created from: {text}",
        trigger_type="cron",
        trigger_config=trigger_config,
        risk_tier=parsed.get("risk_tier", "read_only"),
    )

    if "error" in recipe:
        await update.message.reply_text(f"Failed: {recipe['error']}")
        return

    # Format day spec for display
    day_spec = parsed.get("day_spec", "")
    day_display = {
        "": "daily",
        "weekdays": "weekdays",
        "weekends": "weekends",
    }.get(day_spec, f"days {day_spec}")

    lines = [
        f"Scheduled #{recipe['id']}: *{escape_md(recipe['name'])}*",
        f"Time: {parsed['time']} ({day_display})",
        f"Risk: {parsed.get('risk_tier', 'read_only')}",
        f"Instructions: {escape_md(parsed.get('instructions', '')[:200])}",
    ]

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "Disable", callback_data=f"recipe:disable:{recipe['id']}"),
            InlineKeyboardButton(
                "Delete", callback_data=f"recipe:delete:{recipe['id']}"),
        ],
    ])

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


@authorized
async def cmd_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List response templates or show a specific one."""
    from roost.services.response_templates import (
        list_templates, get_template, get_template_by_name,
    )

    args = context.args or []
    text = " ".join(args).strip()

    if not text:
        templates = list_templates(active_only=False)
        if not templates:
            await update.message.reply_text("No templates yet. Create one via MCP tools.")
            return

        lines = ["*Response Templates:*\n"]
        for t in templates:
            active = "active" if t.get("is_active") else "inactive"
            uses = t.get("usage_count", 0)
            tags = ", ".join(t.get("intent_tags", []))
            tag_str = f" [{tags}]" if tags else ""
            lines.append(
                f"  #{t['id']} {escape_md(t['name'])} "
                f"({t['category']}, {active}, {uses} uses){tag_str}"
            )
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    # Show specific template
    if text.isdigit():
        tmpl = get_template(int(text))
    else:
        tmpl = get_template_by_name(text)

    if "error" in tmpl:
        await update.message.reply_text(tmpl["error"])
        return

    active = "Active" if tmpl.get("is_active") else "Inactive"
    tags = ", ".join(tmpl.get("intent_tags", []))

    lines = [
        f"*Template #{tmpl['id']}:* {escape_md(tmpl['name'])}",
        f"Category: {tmpl['category']} | Channel: {tmpl.get('channel', 'any')}",
        f"Status: {active} | Uses: {tmpl.get('usage_count', 0)}",
    ]
    if tags:
        lines.append(f"Tags: {tags}")
    if tmpl.get("subject"):
        lines.append(f"Subject: {escape_md(tmpl['subject'])}")
    if tmpl.get("sequence_group"):
        lines.append(f"Sequence: {tmpl['sequence_group']} (day {tmpl.get('sequence_day', 0)})")

    lines.append(f"\n```\n{tmpl['body'][:500]}\n```")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_sequence(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show templates in a sequence group."""
    from roost.services.response_templates import list_templates

    args = context.args or []
    group = " ".join(args).strip()

    if not group:
        # List all sequence groups
        templates = list_templates(active_only=False)
        groups = {}
        for t in templates:
            sg = t.get("sequence_group", "")
            if sg:
                groups.setdefault(sg, []).append(t)

        if not groups:
            await update.message.reply_text("No template sequences defined.")
            return

        lines = ["*Template Sequences:*\n"]
        for name, tmpls in sorted(groups.items()):
            lines.append(f"  {escape_md(name)} ({len(tmpls)} templates)")
        lines.append("\nUse /sequence <name> for details.")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return

    templates = list_templates(sequence_group=group, active_only=False)
    if not templates:
        await update.message.reply_text(f"No templates in sequence '{group}'.")
        return

    lines = [f"*Sequence: {escape_md(group)}*\n"]
    for t in sorted(templates, key=lambda x: x.get("sequence_day", 0)):
        day = t.get("sequence_day", 0)
        lines.append(f"  Day {day}: #{t['id']} {escape_md(t['name'])}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


@authorized
async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Approve a pending recipe run."""
    from roost.services.recipes import approve_run

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /approve <run_id>")
        return

    run_id = int(args[0])
    result = approve_run(run_id)

    if "error" in result:
        await update.message.reply_text(result["error"])
    else:
        await update.message.reply_text(f"Run #{run_id} approved and completed.")


@authorized
async def cmd_skip_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Skip a pending recipe run."""
    from roost.services.recipes import skip_run

    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /skip <run_id>")
        return

    run_id = int(args[0])
    result = skip_run(run_id)

    if "error" in result:
        await update.message.reply_text(result["error"])
    else:
        await update.message.reply_text(f"Run #{run_id} skipped.")


@authorized
async def cmd_rollback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recent checkpoints or rollback a specific action.

    Usage:
      /rollback        — List recent checkpoints
      /rollback <id>   — Rollback a specific checkpoint
    """
    from roost.services.checkpoints import list_checkpoints, rollback_checkpoint

    args = context.args or []
    text = " ".join(args).strip()

    if text and text.isdigit():
        # Rollback a specific checkpoint
        result = rollback_checkpoint(int(text))
        if "error" in result:
            await update.message.reply_text(f"Rollback failed: {result['error']}")
        else:
            await update.message.reply_text(
                f"Rolled back checkpoint #{text}: "
                f"{result.get('reverse_tool', '?')} executed."
            )
        return

    # List recent checkpoints
    checkpoints = list_checkpoints(limit=10)
    if not checkpoints:
        await update.message.reply_text("No checkpoints yet. Agent actions are recorded automatically.")
        return

    lines = ["*Recent Checkpoints:*\n"]
    for cp in checkpoints:
        rolled = " (rolled back)" if cp.get("rolled_back") else ""
        reversible = "undo" if cp.get("reverse_tool") else "no undo"
        created = cp.get("created_at", "")[:16]
        lines.append(
            f"  #{cp['id']} {escape_md(cp['tool_name'])} "
            f"[{reversible}]{rolled} ({created})"
        )

    lines.append("\nUse /rollback <id> to undo an action.")

    # Add rollback buttons for recent reversible checkpoints
    buttons = []
    for cp in checkpoints[:5]:
        if cp.get("reverse_tool") and not cp.get("rolled_back"):
            buttons.append([InlineKeyboardButton(
                f"Undo #{cp['id']}: {cp['tool_name']}",
                callback_data=f"rollback:{cp['id']}",
            )])

    keyboard = InlineKeyboardMarkup(buttons) if buttons else None
    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)


# ── Callback handlers ──────────────────────────────────────────────


async def handle_recipe_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle recipe:<action>:<id> callback queries."""
    query = update.callback_query
    parts = (query.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    recipe_id = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None

    from roost.services.recipes import update_recipe, list_runs, get_recipe

    if action in ("enable", "disable") and recipe_id:
        enabled = action == "enable"
        update_recipe(recipe_id, enabled=enabled)
        await query.answer(f"Recipe {'enabled' if enabled else 'disabled'}!")
        recipe = get_recipe(recipe_id)
        if "error" not in recipe:
            status = "Enabled" if recipe.get("enabled") else "Disabled"
            await query.edit_message_text(
                f"Recipe #{recipe_id} ({recipe['name']}): {status}")
        return

    if action == "runs" and recipe_id:
        runs = list_runs(recipe_id=recipe_id, limit=5)
        if not runs:
            await query.answer("No runs yet.")
            await query.edit_message_text(f"Recipe #{recipe_id}: no runs yet.")
            return

        lines = [f"*Recent runs for recipe #{recipe_id}:*\n"]
        for r in runs:
            status = r.get("status", "?")
            started = r.get("started_at", "?")[:16]
            lines.append(f"  Run #{r['id']}: {status} ({started})")

        # Add approve/skip buttons for awaiting_approval runs
        buttons = []
        for r in runs:
            if r.get("status") == "awaiting_approval":
                buttons.append([
                    InlineKeyboardButton(
                        f"Approve #{r['id']}", callback_data=f"recipe:approve:{r['id']}"),
                    InlineKeyboardButton(
                        f"Skip #{r['id']}", callback_data=f"recipe:skip:{r['id']}"),
                ])

        keyboard = InlineKeyboardMarkup(buttons) if buttons else None
        await query.answer()
        await query.edit_message_text(
            "\n".join(lines), parse_mode="Markdown", reply_markup=keyboard)
        return

    if action == "approve" and recipe_id:
        # recipe_id is actually run_id here.
        from roost.services.recipes import approve_run
        run_id = recipe_id
        result = approve_run(run_id)
        _from_user = getattr(query, "from_user", None)
        actor_ref = f"tg:{_from_user.id}" if _from_user else ""
        activity.log_action(
            "telegram",
            "recipe.approve",
            entity_type="automation_run",
            entity_id=run_id,
            ok="error" not in result,
            result=result,
            snippet=f"Approved recipe run #{run_id} via Telegram",
            actor_ref=actor_ref,
        )
        if "error" in result:
            await query.answer(result["error"])
        else:
            await query.answer("Approved!")
            await query.edit_message_text(f"Run #{run_id} approved and completed.")
        return

    if action == "edit" and recipe_id:
        from roost.services.recipes import get_run
        run_id = recipe_id
        run = get_run(run_id)
        if not run:
            await query.answer("Run not found", show_alert=True)
            return
        if run.get("status") != "awaiting_approval":
            await query.answer(
                f"Not awaiting approval (status={run.get('status')})",
                show_alert=True,
            )
            return
        chat_data = getattr(context, "chat_data", None)
        if chat_data is None:
            chat_data = {}
        chat_data[_RECIPE_EDIT_KEY] = run_id

        current_draft = run.get("draft_output") or ""
        prompt = (
            f"✏️ Edit recipe run #{run_id}\n\n"
            "Reply to this message with the revised draft."
        )
        if current_draft:
            prompt += f"\n\nCurrent draft:\n{current_draft[:1000]}"
        await query.answer("Reply with the edited draft")
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=prompt,
            reply_markup=ForceReply(selective=True),
        )
        _from_user = getattr(query, "from_user", None)
        actor_ref = f"tg:{_from_user.id}" if _from_user else ""
        activity.log_action(
            "telegram",
            "recipe.edit_prompt",
            entity_type="automation_run",
            entity_id=run_id,
            ok=True,
            snippet=f"Edit prompt sent for run #{run_id}",
            actor_ref=actor_ref,
        )
        return

    if action == "skip" and recipe_id:
        from roost.services.recipes import skip_run
        run_id = recipe_id
        result = skip_run(run_id)
        _from_user = getattr(query, "from_user", None)
        actor_ref = f"tg:{_from_user.id}" if _from_user else ""
        activity.log_action(
            "telegram",
            "recipe.skip",
            entity_type="automation_run",
            entity_id=run_id,
            ok="error" not in result,
            result=result,
            snippet=f"Skipped recipe run #{run_id} via Telegram",
            actor_ref=actor_ref,
        )
        if "error" in result:
            await query.answer(result["error"])
        else:
            await query.answer("Skipped.")
            await query.edit_message_text(f"Run #{run_id} skipped.")
        return

    if action == "delete" and recipe_id:
        from roost.services.recipes import delete_recipe
        result = delete_recipe(recipe_id)
        if "error" in result:
            await query.answer(result["error"])
        else:
            await query.answer("Deleted!")
            await query.edit_message_text(f"Recipe #{recipe_id} deleted.")
        return

    await query.answer("Unknown recipe action.")


# ── Force-reply capture for the recipe edit flow ──────────────────────


async def handle_recipe_edit_reply(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Capture the operator's reply to a recipe edit prompt.

    Fires only when `chat_data[_RECIPE_EDIT_KEY]` is set (operator tapped
    ✏️ Edit on a recipe hold notification). Returns True if it consumed
    the message so the agent catch-all doesn't also handle it.
    """
    chat_data = getattr(context, "chat_data", None) or {}
    run_id = chat_data.get(_RECIPE_EDIT_KEY)
    if not run_id:
        return False
    msg = getattr(update, "message", None)
    if msg is None or not getattr(msg, "text", None):
        return False

    new_draft = msg.text.strip()
    if not new_draft:
        await msg.reply_text("Empty edit — keeping previous draft.")
        chat_data.pop(_RECIPE_EDIT_KEY, None)
        return True

    from roost.services.recipes import apply_run_draft_edit
    result = apply_run_draft_edit(int(run_id), new_draft)
    _from_user = getattr(msg, "from_user", None)
    actor_ref = f"tg:{_from_user.id}" if _from_user else ""
    activity.log_action(
        "telegram",
        "recipe.edit_apply",
        entity_type="automation_run",
        entity_id=int(run_id),
        ok=bool(result.get("ok")),
        result={"draft_len": len(new_draft)},
        snippet=f"Edited draft for run #{run_id} ({len(new_draft)} chars)",
        actor_ref=actor_ref,
    )
    chat_data.pop(_RECIPE_EDIT_KEY, None)

    if not result.get("ok"):
        await msg.reply_text(
            f"Edit run #{run_id} failed: {result.get('error', 'unknown')}"
        )
        return True

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Approve", callback_data=f"recipe:approve:{run_id}"),
        InlineKeyboardButton("✏️ Edit", callback_data=f"recipe:edit:{run_id}"),
        InlineKeyboardButton("⏭ Skip", callback_data=f"recipe:skip:{run_id}"),
    ]])
    await context.bot.send_message(
        chat_id=msg.chat_id,
        text=(
            f"✏️ Draft updated for run #{run_id} "
            f"({len(new_draft)} chars). Approve to send."
        ),
        reply_markup=keyboard,
    )
    return True
