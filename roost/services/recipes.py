"""Automation recipe service — user-defined automation with approval gates.

Recipes combine triggers (cron, event, manual) with AI CDR classification
and response template selection. External actions always require human approval.

Risk tiers:
  read_only     — auto-execute, log result
  internal_write — auto-execute, Telegram notification
  external_write — draft → Telegram approval → execute on confirm
"""

import json
import logging
from datetime import datetime, timezone

from roost.database import get_connection
from roost.services import response_templates as tmpl_svc

logger = logging.getLogger("roost.recipes")


# ── Recipe CRUD ─────────────────────────────────────────────────────


def create_recipe(
    *,
    name: str,
    instructions: str,
    description: str = "",
    trigger_type: str = "manual",
    trigger_config: str = "",
    risk_tier: str = "read_only",
    template_ids: list[int] | None = None,
    user_id: str = "",
) -> dict:
    """Create a new automation recipe."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO automation_recipes
               (name, description, trigger_type, trigger_config, risk_tier,
                instructions, template_ids, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, description, trigger_type, trigger_config, risk_tier,
             instructions, json.dumps(template_ids or []), user_id),
        )
        conn.commit()
        return get_recipe(cur.lastrowid)
    finally:
        conn.close()


def get_recipe(recipe_id: int) -> dict:
    """Get a recipe by ID."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM automation_recipes WHERE id = ?", (recipe_id,)
        ).fetchone()
        if not row:
            return {"error": f"Recipe {recipe_id} not found"}
        return _recipe_to_dict(row)
    finally:
        conn.close()


def get_recipe_by_name(name: str) -> dict:
    """Get a recipe by name."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM automation_recipes WHERE name = ?", (name,)
        ).fetchone()
        if not row:
            return {"error": f"Recipe '{name}' not found"}
        return _recipe_to_dict(row)
    finally:
        conn.close()


def list_recipes(
    trigger_type: str = "",
    enabled_only: bool = False,
    user_id: str = "",
) -> list[dict]:
    """List recipes with optional filters."""
    conn = get_connection()
    try:
        query = "SELECT * FROM automation_recipes WHERE 1=1"
        params: list = []

        if trigger_type:
            query += " AND trigger_type = ?"
            params.append(trigger_type)
        if enabled_only:
            query += " AND enabled = 1"

        query += " ORDER BY name"
        rows = conn.execute(query, params).fetchall()
        return [_recipe_to_dict(r) for r in rows]
    finally:
        conn.close()


def update_recipe(recipe_id: int, **kwargs) -> dict:
    """Update a recipe."""
    conn = get_connection()
    try:
        updates = []
        params = []
        for key, value in kwargs.items():
            if value is None:
                continue
            if key == "template_ids" and isinstance(value, list):
                value = json.dumps(value)
            if key == "enabled" and isinstance(value, bool):
                value = 1 if value else 0
            updates.append(f"{key} = ?")
            params.append(value)

        if not updates:
            return get_recipe(recipe_id)

        params.append(recipe_id)
        conn.execute(
            f"UPDATE automation_recipes SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
        return get_recipe(recipe_id)
    finally:
        conn.close()


def delete_recipe(recipe_id: int) -> dict:
    """Delete a recipe and its run history."""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM automation_runs WHERE recipe_id = ?", (recipe_id,))
        conn.execute("DELETE FROM automation_recipes WHERE id = ?", (recipe_id,))
        conn.commit()
        return {"ok": True, "deleted": recipe_id}
    finally:
        conn.close()


# ── Automation Runs ─────────────────────────────────────────────────


def create_run(
    recipe_id: int,
    trigger_data: dict | None = None,
) -> dict:
    """Create a new automation run record."""
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        cur = conn.execute(
            """INSERT INTO automation_runs
               (recipe_id, started_at, trigger_data)
               VALUES (?, ?, ?)""",
            (recipe_id, now, json.dumps(trigger_data or {})),
        )
        conn.commit()
        return {"id": cur.lastrowid, "recipe_id": recipe_id, "status": "running"}
    finally:
        conn.close()


def update_run(run_id: int, **kwargs) -> None:
    """Update a run record."""
    conn = get_connection()
    try:
        updates = []
        params = []
        for key, value in kwargs.items():
            if value is None:
                continue
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            updates.append(f"{key} = ?")
            params.append(value)

        if not updates:
            return

        params.append(run_id)
        conn.execute(
            f"UPDATE automation_runs SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()


def complete_run(run_id: int, status: str = "completed", **kwargs) -> None:
    """Mark a run as completed/failed."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    update_run(run_id, status=status, completed_at=now, **kwargs)

    # Update recipe stats
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT recipe_id FROM automation_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row:
            conn.execute(
                """UPDATE automation_recipes
                   SET last_run = ?, run_count = run_count + 1
                   WHERE id = ?""",
                (now, row["recipe_id"]),
            )
            conn.commit()
    finally:
        conn.close()


def list_runs(
    recipe_id: int | None = None,
    status: str = "",
    limit: int = 20,
) -> list[dict]:
    """List automation runs with optional filters."""
    conn = get_connection()
    try:
        query = "SELECT * FROM automation_runs WHERE 1=1"
        params: list = []

        if recipe_id is not None:
            query += " AND recipe_id = ?"
            params.append(recipe_id)
        if status:
            query += " AND status = ?"
            params.append(status)

        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [_run_to_dict(r) for r in rows]
    finally:
        conn.close()


# ── Recipe Execution ────────────────────────────────────────────────


async def execute_recipe(
    recipe_id: int,
    message: str = "",
    sender: str = "",
    trigger_data: dict | None = None,
) -> dict:
    """Execute a recipe against an inbound message.

    Pipeline:
    1. Load recipe and its linked templates
    2. Run AI CDR classifier (tool-less, sandboxed)
    3. Select best matching template
    4. Fill template variables from extracted fields
    5. Return draft for approval (if external_write) or result

    Returns dict with run_id, classification, selected_template, draft.

    Special case: if `instructions` starts with `RPA_FLOW:<portal>` the
    recipe is treated as a browser-automation flow and dispatched to
    `roost.services.rpa_flows`. The AI CDR pipeline is skipped.
    """
    from roost.extras.messaging_external.services.ai_cdr import classify_message

    recipe = get_recipe(recipe_id)
    if "error" in recipe:
        return recipe

    instructions = (recipe.get("instructions") or "").strip()
    if instructions.upper().startswith("RPA_FLOW:"):
        return await _execute_rpa_recipe(recipe, instructions, trigger_data)

    # Create run record
    run = create_run(recipe_id, trigger_data=trigger_data or {"message": message[:200]})
    run_id = run["id"]

    try:
        # Load templates for this recipe
        template_ids = recipe.get("template_ids", [])
        if template_ids:
            templates = [
                tmpl_svc.get_template(tid) for tid in template_ids
                if "error" not in tmpl_svc.get_template(tid)
            ]
        else:
            templates = tmpl_svc.list_templates(active_only=True)

        # Step 1: AI CDR classification (tool-less, sandboxed)
        classification = await classify_message(
            message=message,
            sender=sender,
            templates=templates,
        )

        update_run(run_id, intent_classified=classification)

        # Step 2: Select best template
        selected = tmpl_svc.select_template(
            intent=classification["intent"],
            urgency=classification["urgency"],
            templates=templates,
        )

        if selected:
            update_run(run_id, template_selected_id=selected["id"])

            # Step 3: Fill template
            fields = classification.get("extracted_fields", {})
            if sender:
                fields.setdefault("name", sender)
            draft = tmpl_svc.fill_template(selected["body"], fields)

            update_run(run_id, draft_output=draft)
        else:
            draft = ""

        # Step 4: Determine action based on risk tier
        risk_tier = recipe.get("risk_tier", "read_only")

        if risk_tier == "external_write":
            # Draft-first: await human approval
            update_run(run_id, status="awaiting_approval")
            result_status = "awaiting_approval"
        elif risk_tier == "internal_write":
            # Auto-execute, notify
            complete_run(run_id, status="completed", final_output=draft)
            if selected:
                tmpl_svc.increment_usage(selected["id"])
            result_status = "completed"
        else:
            # Read-only: just log
            complete_run(run_id, status="completed")
            result_status = "completed"

        return {
            "run_id": run_id,
            "status": result_status,
            "classification": classification,
            "selected_template": selected,
            "draft": draft,
            "risk_tier": risk_tier,
            "recipe_name": recipe["name"],
        }

    except Exception as e:
        logger.exception("Recipe execution error")
        complete_run(run_id, status="failed", actions_taken=[{"error": str(e)}])
        return {"run_id": run_id, "status": "failed", "error": str(e)}


async def _execute_rpa_recipe(
    recipe: dict, instructions: str, trigger_data: dict | None
) -> dict:
    """Dispatch an RPA recipe.

    `instructions` format:
        RPA_FLOW:<portal>            (optional second line: JSON params)
    """
    from roost.extras.rpa.services import rpa_flows, rpa_runs

    parts = instructions.split("\n", 1)
    portal = parts[0].split(":", 1)[1].strip().lower()
    params: dict = {}
    if len(parts) == 2:
        try:
            params = json.loads(parts[1])
        except json.JSONDecodeError:
            logger.warning("Recipe %s: malformed RPA params", recipe.get("name"))

    # Merge trigger-time params (e.g. from a /recipe call)
    if trigger_data and isinstance(trigger_data.get("params"), dict):
        params.update(trigger_data["params"])

    user_id = recipe.get("user_id") or ""
    rpa_run_id = rpa_runs.create_run(
        user_id=user_id, portal_slug=portal, recipe_id=recipe["id"], state=params
    )

    # Track in automation_runs as well so the audit trail is consistent
    auto_run = create_run(recipe["id"], trigger_data={"rpa_run_id": rpa_run_id, **(trigger_data or {})})
    auto_run_id = auto_run["id"]

    try:
        result = await rpa_flows.dispatch(portal, rpa_run_id, user_id, params)
        if "error" in result:
            complete_run(auto_run_id, status="failed", actions_taken=[result])
            return {"run_id": auto_run_id, "rpa_run_id": rpa_run_id, "status": "failed", **result}
        complete_run(auto_run_id, status="completed", final_output=json.dumps(result)[:8000])
        return {"run_id": auto_run_id, "rpa_run_id": rpa_run_id, "status": "completed", "result": result}
    except Exception as e:
        logger.exception("RPA recipe failed")
        rpa_runs.fail(rpa_run_id, str(e))
        complete_run(auto_run_id, status="failed", actions_taken=[{"error": str(e)}])
        return {"run_id": auto_run_id, "rpa_run_id": rpa_run_id, "status": "failed", "error": str(e)}


def approve_run(run_id: int, final_output: str = "") -> dict:
    """Approve an awaiting_approval run and mark as completed."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM automation_runs WHERE id = ? AND status = 'awaiting_approval'",
            (run_id,),
        ).fetchone()
        if not row:
            return {"error": f"Run {run_id} not found or not awaiting approval"}

        complete_run(
            run_id,
            status="completed",
            final_output=final_output or row["draft_output"],
        )

        # Increment template usage
        template_id = row["template_selected_id"]
        if template_id:
            tmpl_svc.increment_usage(template_id)

        return {"ok": True, "run_id": run_id, "status": "completed"}
    finally:
        conn.close()


def skip_run(run_id: int) -> dict:
    """Skip an awaiting_approval run."""
    complete_run(run_id, status="skipped")
    return {"ok": True, "run_id": run_id, "status": "skipped"}


# ── Helpers ─────────────────────────────────────────────────────────


def _recipe_to_dict(row) -> dict:
    d = dict(row)
    if "template_ids" in d:
        try:
            d["template_ids"] = json.loads(d["template_ids"])
        except (json.JSONDecodeError, TypeError):
            d["template_ids"] = []
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    return d


def _run_to_dict(row) -> dict:
    d = dict(row)
    for json_field in ("trigger_data", "intent_classified", "actions_taken"):
        if json_field in d and isinstance(d[json_field], str):
            try:
                d[json_field] = json.loads(d[json_field])
            except (json.JSONDecodeError, TypeError):
                pass
    return d
