"""MCP tools for response templates, automation recipes, and AI CDR."""

from roost.mcp.server import mcp


# ── Response Templates ─────────────────────────────────────────────


@mcp.tool()
def create_response_template(
    name: str,
    body: str,
    category: str = "general",
    intent_tags: list[str] | None = None,
    subject: str = "",
    channel: str = "any",
    sequence_group: str = "",
    sequence_day: int = 0,
) -> dict:
    """Create a canned response template with {{variable}} placeholders.

    Templates are pre-written messages in the user's voice. AI selects the
    best match based on intent classification of inbound messages.

    Args:
        name: Unique template name (e.g. 'greeting_buyer').
        body: Template body with {{variable}} placeholders (e.g. 'Hi {{name}}, thanks for...').
        category: One of: greeting, qualification, nurture, re_engagement, closing, general.
        intent_tags: List of intent tags this template matches (e.g. ['buying_enquiry', 'pricing_enquiry']).
        subject: Optional subject line (for email templates).
        channel: Target channel: whatsapp, email, telegram, or any.
        sequence_group: Group name for multi-step sequences (e.g. 'buyer_nurture').
        sequence_day: Day number in the sequence (0 = standalone).
    """
    try:
        from roost.services.response_templates import create_template
        return create_template(
            name=name,
            body=body,
            category=category,
            intent_tags=intent_tags,
            subject=subject,
            channel=channel,
            sequence_group=sequence_group,
            sequence_day=sequence_day,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_response_templates(
    category: str = "",
    channel: str = "",
    active_only: bool = True,
    sequence_group: str = "",
) -> dict:
    """List response templates with optional filters.

    Args:
        category: Filter by category (greeting, qualification, nurture, etc.).
        channel: Filter by channel (whatsapp, email, telegram, any).
        active_only: Only return active templates (default True).
        sequence_group: Filter by sequence group name.
    """
    try:
        from roost.services.response_templates import list_templates
        templates = list_templates(
            category=category,
            channel=channel,
            active_only=active_only,
            sequence_group=sequence_group,
        )
        return {"count": len(templates), "templates": templates}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_response_template(template_id: int) -> dict:
    """Get a response template by ID.

    Args:
        template_id: The template ID.
    """
    try:
        from roost.services.response_templates import get_template
        return get_template(template_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_response_template(
    template_id: int,
    name: str = "",
    body: str = "",
    category: str = "",
    intent_tags: list[str] | None = None,
    subject: str = "",
    channel: str = "",
    is_active: bool | None = None,
) -> dict:
    """Update a response template.

    Only provided fields are updated; omitted fields are unchanged.

    Args:
        template_id: The template ID to update.
        name: New template name.
        body: New template body.
        category: New category.
        intent_tags: New intent tags list.
        subject: New subject line.
        channel: New channel.
        is_active: Set active/inactive.
    """
    try:
        from roost.services.response_templates import update_template
        kwargs = {}
        if name:
            kwargs["name"] = name
        if body:
            kwargs["body"] = body
        if category:
            kwargs["category"] = category
        if intent_tags is not None:
            kwargs["intent_tags"] = intent_tags
        if subject:
            kwargs["subject"] = subject
        if channel:
            kwargs["channel"] = channel
        if is_active is not None:
            kwargs["is_active"] = is_active
        return update_template(template_id, **kwargs)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def delete_response_template(template_id: int) -> dict:
    """Delete a response template.

    Args:
        template_id: The template ID to delete.
    """
    try:
        from roost.services.response_templates import delete_template
        return delete_template(template_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def fill_response_template(template_id: int, fields: dict) -> dict:
    """Fill a template's {{variable}} placeholders with provided values.

    Returns the filled text. Unfilled placeholders are removed.

    Args:
        template_id: The template ID to fill.
        fields: Dict of variable names to values (e.g. {"name": "John", "budget": "500k"}).
    """
    try:
        from roost.services.response_templates import get_template, fill_template
        tmpl = get_template(template_id)
        if "error" in tmpl:
            return tmpl
        filled = fill_template(tmpl["body"], fields)
        return {"template_id": template_id, "template_name": tmpl["name"], "filled": filled}
    except Exception as e:
        return {"error": str(e)}


# ── Automation Recipes ─────────────────────────────────────────────


@mcp.tool()
def create_recipe(
    name: str,
    instructions: str,
    description: str = "",
    trigger_type: str = "manual",
    trigger_config: str = "",
    risk_tier: str = "read_only",
    template_ids: list[int] | None = None,
) -> dict:
    """Create an automation recipe — a user-defined automation rule.

    Recipes combine triggers with AI CDR classification and template selection.
    External actions always require human approval via Telegram.

    Risk tiers:
    - read_only: auto-execute, log result
    - internal_write: auto-execute, send Telegram notification
    - external_write: draft first, await Telegram approval before executing

    Args:
        name: Recipe name (e.g. 'Qualify WhatsApp leads').
        instructions: Natural language instructions for what this recipe does.
        description: Optional longer description.
        trigger_type: One of: manual, cron, event.
        trigger_config: Trigger-specific config (cron expression, event name, etc.).
        risk_tier: One of: read_only, internal_write, external_write.
        template_ids: List of template IDs this recipe can use. Empty = all active templates.
    """
    try:
        from roost.services.recipes import create_recipe as _create
        return _create(
            name=name,
            instructions=instructions,
            description=description,
            trigger_type=trigger_type,
            trigger_config=trigger_config,
            risk_tier=risk_tier,
            template_ids=template_ids,
        )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_recipes(
    trigger_type: str = "",
    enabled_only: bool = False,
) -> dict:
    """List automation recipes with optional filters.

    Args:
        trigger_type: Filter by trigger type (manual, cron, event).
        enabled_only: Only return enabled recipes.
    """
    try:
        from roost.services.recipes import list_recipes as _list
        recipes = _list(trigger_type=trigger_type, enabled_only=enabled_only)
        return {"count": len(recipes), "recipes": recipes}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_recipe(recipe_id: int) -> dict:
    """Get an automation recipe by ID.

    Args:
        recipe_id: The recipe ID.
    """
    try:
        from roost.services.recipes import get_recipe as _get
        return _get(recipe_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_recipe(
    recipe_id: int,
    name: str = "",
    instructions: str = "",
    description: str = "",
    trigger_type: str = "",
    trigger_config: str = "",
    risk_tier: str = "",
    template_ids: list[int] | None = None,
    enabled: bool | None = None,
) -> dict:
    """Update an automation recipe.

    Only provided fields are updated; omitted fields are unchanged.

    Args:
        recipe_id: The recipe ID to update.
        name: New recipe name.
        instructions: New instructions.
        description: New description.
        trigger_type: New trigger type.
        trigger_config: New trigger config.
        risk_tier: New risk tier.
        template_ids: New template ID list.
        enabled: Enable or disable the recipe.
    """
    try:
        from roost.services.recipes import update_recipe as _update
        kwargs = {}
        if name:
            kwargs["name"] = name
        if instructions:
            kwargs["instructions"] = instructions
        if description:
            kwargs["description"] = description
        if trigger_type:
            kwargs["trigger_type"] = trigger_type
        if trigger_config:
            kwargs["trigger_config"] = trigger_config
        if risk_tier:
            kwargs["risk_tier"] = risk_tier
        if template_ids is not None:
            kwargs["template_ids"] = template_ids
        if enabled is not None:
            kwargs["enabled"] = enabled
        return _update(recipe_id, **kwargs)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def delete_recipe(recipe_id: int) -> dict:
    """Delete an automation recipe and its run history.

    Args:
        recipe_id: The recipe ID to delete.
    """
    try:
        from roost.services.recipes import delete_recipe as _delete
        return _delete(recipe_id)
    except Exception as e:
        return {"error": str(e)}


# ── Recipe Execution ───────────────────────────────────────────────


@mcp.tool()
def execute_recipe(
    recipe_id: int,
    message: str = "",
    sender: str = "",
) -> dict:
    """Execute a recipe against an inbound message using the AI CDR pipeline.

    Pipeline:
    1. Sanitize inbound message (strip prompt injection patterns)
    2. Frame content with data delimiters (untrusted content warning)
    3. Classify via tool-less AI call (intent, urgency, extracted fields)
    4. Select best matching template
    5. Fill template variables from extracted fields
    6. Return draft (external_write) or auto-execute (read_only/internal_write)

    The AI classifier runs WITHOUT tools — prompt injection in the message
    cannot cause tool execution. Worst case is misclassification.

    Args:
        recipe_id: The recipe to execute.
        message: The inbound message to classify and respond to.
        sender: Sender identifier (name, phone number, email).
    """
    try:
        import asyncio
        from roost.services.recipes import execute_recipe as _execute

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(
                    asyncio.run,
                    _execute(recipe_id=recipe_id, message=message, sender=sender),
                ).result(timeout=30)
        else:
            return asyncio.run(
                _execute(recipe_id=recipe_id, message=message, sender=sender)
            )
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_recipe_runs(
    recipe_id: int | None = None,
    status: str = "",
    limit: int = 20,
) -> dict:
    """List automation run history with optional filters.

    Args:
        recipe_id: Filter by recipe ID. None = all recipes.
        status: Filter by status (running, completed, failed, awaiting_approval, skipped).
        limit: Max results (default 20).
    """
    try:
        from roost.services.recipes import list_runs
        runs = list_runs(recipe_id=recipe_id, status=status, limit=limit)
        return {"count": len(runs), "runs": runs}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def approve_recipe_run(run_id: int, final_output: str = "") -> dict:
    """Approve a run that is awaiting approval and mark it as completed.

    For external_write recipes, the draft is held until a human approves.
    Optionally override the final output (e.g. if the user edited the draft).

    Args:
        run_id: The run ID to approve.
        final_output: Optional override for the final output. If empty, uses the draft.
    """
    try:
        from roost.services.recipes import approve_run
        return approve_run(run_id, final_output=final_output)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def skip_recipe_run(run_id: int) -> dict:
    """Skip a run that is awaiting approval — do not execute it.

    Args:
        run_id: The run ID to skip.
    """
    try:
        from roost.services.recipes import skip_run
        return skip_run(run_id)
    except Exception as e:
        return {"error": str(e)}


# ── AI CDR (standalone classification) ─────────────────────────────


@mcp.tool()
def classify_inbound_message(
    message: str,
    sender: str = "",
) -> dict:
    """Classify an inbound message using the AI CDR pipeline (standalone).

    Runs the 4-layer Content Disarm & Reconstruct pipeline:
    1. Sanitize — strip obvious prompt injection patterns
    2. Frame — wrap in data delimiters with untrusted content warning
    3. Detonate — tool-less AI classification (no tools = no exploit surface)
    4. Validate — output must match fixed JSON schema

    Returns intent, urgency, extracted fields, confidence, reasoning.
    On any failure, returns safe default (unknown/cold).

    Args:
        message: The raw inbound message text (untrusted).
        sender: Sender identifier (name, phone, email).
    """
    try:
        from roost.services.ai_cdr import classify_message_sync
        return classify_message_sync(message=message, sender=sender)
    except Exception as e:
        return {"error": str(e)}
