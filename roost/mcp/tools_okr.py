"""MCP tools for OKR management — cycles, objectives, key results, dashboard, scoring."""

from roost.mcp.server import mcp


@mcp.tool()
def create_okr_cycle(
    name: str,
    start_date: str,
    end_date: str,
    entity_id: int | None = None,
    notes: str = "",
) -> dict:
    """Create a new OKR cycle (quarterly period).

    Args:
        name: Cycle name, e.g. "Q2 2026".
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        entity_id: Optional entity to scope the cycle to.
        notes: Optional notes about this cycle.
    """
    try:
        from roost.services.okr import create_okr_cycle as _create
        from roost.models import OkrCycleCreate

        return _create(OkrCycleCreate(
            name=name, start_date=start_date, end_date=end_date,
            entity_id=entity_id, notes=notes,
        ))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def list_okr_cycles(
    status: str | None = None,
    entity_id: int | None = None,
) -> dict:
    """List OKR cycles with optional filters.

    Args:
        status: Filter by status: planning, active, scoring, closed.
        entity_id: Filter by entity.
    """
    try:
        from roost.services.okr import list_okr_cycles as _list

        cycles = _list(status=status, entity_id=entity_id)
        return {"count": len(cycles), "cycles": cycles}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_okr_cycle(
    cycle_id: int,
    name: str | None = None,
    status: str | None = None,
    notes: str | None = None,
) -> dict:
    """Update an OKR cycle. Status follows lifecycle: planning -> active -> scoring -> closed.

    Args:
        cycle_id: The cycle ID.
        name: New name.
        status: New status (must follow lifecycle order).
        notes: Updated notes.
    """
    try:
        from roost.services.okr import update_okr_cycle as _update
        from roost.models import OkrCycleUpdate, CycleStatus

        update_data = OkrCycleUpdate(
            name=name,
            status=CycleStatus(status) if status else None,
            notes=notes,
        )
        return _update(cycle_id, update_data)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def create_okr_objective(
    cycle_id: int,
    title: str,
    description: str = "",
    level: str = "personal",
    okr_type: str = "committed",
    parent_objective_id: int | None = None,
    owner_contact_id: int | None = None,
    entity_id: int | None = None,
    project_id: int | None = None,
    department: str = "",
) -> dict:
    """Create an objective under a cycle. Max 3 per level per cycle.

    Args:
        cycle_id: The cycle this objective belongs to.
        title: Qualitative goal statement.
        description: Longer description.
        level: company, department, or personal.
        okr_type: committed (must hit 0.7-1.0) or aspirational (0.4-0.6 is healthy).
        parent_objective_id: Optional parent for cascade alignment.
        owner_contact_id: Who owns this objective.
        entity_id: Optional entity alignment.
        project_id: Optional project alignment.
        department: Free text department (SOC, VAPT, Sales, AI BU, etc.).
    """
    try:
        from roost.services.okr import create_okr_objective as _create
        from roost.models import OkrObjectiveCreate, OkrLevel, OkrType

        return _create(OkrObjectiveCreate(
            cycle_id=cycle_id, title=title, description=description,
            level=OkrLevel(level), okr_type=OkrType(okr_type),
            parent_objective_id=parent_objective_id,
            owner_contact_id=owner_contact_id,
            entity_id=entity_id, project_id=project_id,
            department=department,
        ))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_okr_objective(
    objective_id: int,
    title: str | None = None,
    description: str | None = None,
    status: str | None = None,
    okr_type: str | None = None,
    parent_objective_id: int | None = None,
    owner_contact_id: int | None = None,
    entity_id: int | None = None,
    project_id: int | None = None,
    department: str | None = None,
    score: float | None = None,
    score_note: str | None = None,
    sort_order: int | None = None,
) -> dict:
    """Update an objective's fields or score it.

    Args:
        objective_id: The objective ID.
        title: New title.
        description: New description.
        status: draft, active, scored, or cancelled.
        okr_type: committed or aspirational.
        parent_objective_id: Parent objective for cascade.
        owner_contact_id: Owner contact.
        entity_id: Entity alignment.
        project_id: Project alignment.
        department: Department name.
        score: Manual score 0.0-1.0 (end-of-quarter judgment).
        score_note: Notes about the score.
        sort_order: Display order.
    """
    try:
        from roost.services.okr import update_okr_objective as _update
        from roost.models import OkrObjectiveUpdate, OkrObjectiveStatus, OkrType

        update_data = OkrObjectiveUpdate(
            title=title, description=description,
            status=OkrObjectiveStatus(status) if status else None,
            okr_type=OkrType(okr_type) if okr_type else None,
            parent_objective_id=parent_objective_id,
            owner_contact_id=owner_contact_id,
            entity_id=entity_id, project_id=project_id,
            department=department,
            score=score, score_note=score_note, sort_order=sort_order,
        )
        return _update(objective_id, update_data)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def create_okr_key_result(
    objective_id: int,
    title: str,
    description: str = "",
    metric_type: str = "number",
    start_value: float = 0,
    target_value: float = 1,
    current_value: float = 0,
    unit: str = "",
    owner_contact_id: int | None = None,
) -> dict:
    """Create a key result under an objective. Max 5 per objective.

    Args:
        objective_id: Parent objective.
        title: Measurable outcome statement.
        description: Additional context.
        metric_type: number, percentage, currency, or milestone.
        start_value: Starting value (baseline).
        target_value: Target value to achieve.
        current_value: Current value (defaults to start_value via 0).
        unit: Unit label (e.g. "clients", "%", "$", "min").
        owner_contact_id: Who owns this KR.
    """
    try:
        from roost.services.okr import create_okr_key_result as _create
        from roost.models import OkrKeyResultCreate, MetricType

        return _create(OkrKeyResultCreate(
            objective_id=objective_id, title=title, description=description,
            metric_type=MetricType(metric_type),
            start_value=start_value, target_value=target_value,
            current_value=current_value, unit=unit,
            owner_contact_id=owner_contact_id,
        ))
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def update_okr_key_result(
    kr_id: int,
    title: str | None = None,
    description: str | None = None,
    metric_type: str | None = None,
    start_value: float | None = None,
    target_value: float | None = None,
    current_value: float | None = None,
    unit: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    score: float | None = None,
    score_note: str | None = None,
    owner_contact_id: int | None = None,
    sort_order: int | None = None,
) -> dict:
    """Update a key result's fields, values, confidence, or score.

    Args:
        kr_id: The key result ID.
        title: New title.
        description: New description.
        metric_type: number, percentage, currency, milestone.
        start_value: New baseline.
        target_value: New target.
        current_value: Updated current value.
        unit: Unit label.
        confidence: Weekly signal: green, yellow, red.
        status: active, scored, or cancelled.
        score: Manual score 0.0-1.0.
        score_note: Notes about the score.
        owner_contact_id: Owner contact.
        sort_order: Display order.
    """
    try:
        from roost.services.okr import update_okr_key_result as _update
        from roost.models import OkrKeyResultUpdate, MetricType, Confidence, KrStatus

        update_data = OkrKeyResultUpdate(
            title=title, description=description,
            metric_type=MetricType(metric_type) if metric_type else None,
            start_value=start_value, target_value=target_value,
            current_value=current_value, unit=unit,
            confidence=Confidence(confidence) if confidence else None,
            status=KrStatus(status) if status else None,
            score=score, score_note=score_note,
            owner_contact_id=owner_contact_id, sort_order=sort_order,
        )
        return _update(kr_id, update_data)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def link_task_to_kr(key_result_id: int, task_id: int) -> dict:
    """Link an existing task to a key result.

    Args:
        key_result_id: The key result to link to.
        task_id: The task to link.
    """
    try:
        from roost.services.okr import link_task_to_kr as _link

        return _link(key_result_id, task_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def unlink_task_from_kr(key_result_id: int, task_id: int) -> dict:
    """Remove a task-KR link.

    Args:
        key_result_id: The key result.
        task_id: The task to unlink.
    """
    try:
        from roost.services.okr import unlink_task_from_kr as _unlink

        return _unlink(key_result_id, task_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_okr_dashboard(
    cycle_id: int | None = None,
    entity_id: int | None = None,
) -> dict:
    """Full OKR tree with live progress. Defaults to active cycle.

    Returns the cycle with all objectives, key results, linked tasks,
    and computed progress at every level.

    Args:
        cycle_id: Specific cycle ID. If omitted, uses the active cycle.
        entity_id: Filter by entity (only used when cycle_id is omitted).
    """
    try:
        from roost.services.okr import get_okr_dashboard as _dashboard

        return _dashboard(cycle_id=cycle_id, entity_id=entity_id)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def okr_checkin(
    kr_id: int,
    current_value: float,
    confidence: str = "green",
) -> dict:
    """Quick weekly check-in: update a KR's current value and confidence.

    Args:
        kr_id: The key result to check in on.
        current_value: Updated current value.
        confidence: Weekly signal: green (on track), yellow (at risk), red (off track).
    """
    try:
        from roost.services.okr import okr_checkin as _checkin

        return _checkin(kr_id, current_value, confidence)
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def get_okr_scorecard(cycle_id: int) -> dict:
    """End-of-quarter scorecard with achievement bands.

    Shows all objectives and KRs with their scores, progress,
    and achievement bands. Includes summary averages for committed
    vs aspirational OKRs.

    Args:
        cycle_id: The cycle to generate the scorecard for.
    """
    try:
        from roost.services.okr import get_okr_scorecard as _scorecard

        return _scorecard(cycle_id)
    except Exception as e:
        return {"error": str(e)}
