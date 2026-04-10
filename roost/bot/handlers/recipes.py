"""Bot handlers for automation recipes and response templates.

Commands:
  /recipe [name]      — List recipes or show a specific one
  /template [name]    — List templates or show a specific one
  /sequence [group]   — Show a template sequence
  /approve <run_id>   — Approve a pending recipe run
  /skip <run_id>      — Skip a pending recipe run
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from roost.bot.security import authorized
from roost.bot.handlers.common import escape_md

logger = logging.getLogger(__name__)


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
        from roost.services.recipes import approve_run
        result = approve_run(recipe_id)  # recipe_id is actually run_id here
        if "error" in result:
            await query.answer(result["error"])
        else:
            await query.answer("Approved!")
            await query.edit_message_text(f"Run #{recipe_id} approved and completed.")
        return

    if action == "skip" and recipe_id:
        from roost.services.recipes import skip_run
        result = skip_run(recipe_id)  # recipe_id is actually run_id here
        if "error" in result:
            await query.answer(result["error"])
        else:
            await query.answer("Skipped.")
            await query.edit_message_text(f"Run #{recipe_id} skipped.")
        return

    await query.answer("Unknown recipe action.")
