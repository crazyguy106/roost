"""Agentic Workflow API — plan-then-execute with per-tool event streaming.

Endpoints (gated by ``AGENTIC_WORKFLOW_ENABLED``):
- POST /api/agentic/message — SSE stream: plan → step events → tool events → final
- POST /api/agentic/confirm — confirm a held action with OTP (same shape as /chat/confirm)
- POST /api/agentic/cancel  — cancel a held action

See ``docs/agentic-workflow-phase1.md`` for the wire-format contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from roost.adapters import (
    BASE_SYSTEM_PROMPT,
    PLATFORM_PROMPTS,
    create_agent,
    get_agentic_mode,
)
from roost.config import AGENT_ENABLED, AGENTIC_WORKFLOW_ENABLED
from roost.context import build_agent_context, save_chat_history
from roost.services.action_confirmations import (
    cancel_action,
    create_pending_action,
    get_pending_actions,
    send_otp_via_telegram,
    verify_otp,
)
from roost.services.agentic_events import (
    EventEmitter,
    new_plan_id,
)
from roost.services.planner import generate_plan

# Tool scope used by the web surface. Matches gemini_agent.TIER_WEB without
# forcing the optional google-genai import path at module load.
TIER_WEB = "web"

router = APIRouter(prefix="/api/agentic", tags=["agentic"])
_logger = logging.getLogger("roost.web.agentic")

# Per-user rate limiter (shared semantics with /api/chat).
_last_message: dict[str, float] = {}
_RATE_LIMIT_SECONDS = 6


def _get_user(request: Request) -> dict | None:
    return getattr(request.state, "current_user", None)


# ── Tool-inventory introspection ─────────────────────────────────────

def _agent_tool_inventory(agent) -> list[dict]:
    """Return ``[{"name", "description"}, ...]`` for the agent's tools.

    Provider-agnostic: handles Gemini ``types.Tool(functionDeclarations=...)``,
    Claude ``[{name, description, input_schema}]`` and OpenAI
    ``[{type: function, function: {name, description, ...}}]``.
    """
    tools = getattr(agent, "tools", None)
    if not tools:
        return []

    inventory: list[dict] = []
    for t in tools:
        # Gemini wrapper: types.Tool with .functionDeclarations
        decls = getattr(t, "functionDeclarations", None) or getattr(t, "function_declarations", None)
        if decls:
            for d in decls:
                name = getattr(d, "name", None)
                if name:
                    inventory.append({
                        "name": name,
                        "description": (getattr(d, "description", "") or ""),
                    })
            continue

        if isinstance(t, dict):
            # OpenAI format
            if t.get("type") == "function" and isinstance(t.get("function"), dict):
                fn = t["function"]
                if fn.get("name"):
                    inventory.append({
                        "name": fn["name"],
                        "description": fn.get("description", "") or "",
                    })
                continue
            # Claude format
            if t.get("name"):
                inventory.append({
                    "name": t["name"],
                    "description": t.get("description", "") or "",
                })
                continue

    # Dedup while preserving order
    seen: set[str] = set()
    deduped: list[dict] = []
    for entry in inventory:
        if entry["name"] in seen:
            continue
        seen.add(entry["name"])
        deduped.append(entry)
    return deduped


# ── Endpoint: POST /api/agentic/message ──────────────────────────────

@router.post("/message")
async def send_message(request: Request):
    """Plan-then-execute SSE stream for the agentic surface.

    Body: ``{"message": "...", "session_id": "optional"}``
    """
    if not AGENTIC_WORKFLOW_ENABLED:
        return JSONResponse({"error": "Agentic workflow is not enabled"}, status_code=404)

    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    if not AGENT_ENABLED:
        return JSONResponse({"error": "Agent is disabled"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    if len(message) > 10000:
        return JSONResponse({"error": "Message too long (max 10,000 chars)"}, status_code=400)

    user_id = str(user.get("user_id", "1"))

    now = time.time()
    last = _last_message.get(user_id, 0)
    if now - last < _RATE_LIMIT_SECONDS:
        wait = int(_RATE_LIMIT_SECONDS - (now - last)) + 1
        return JSONResponse(
            {"error": f"Slow down — try again in {wait}s"},
            status_code=429,
        )
    _last_message[user_id] = now

    session_id = body.get("session_id", f"web:{user_id}:agentic")
    provider_override = body.get("provider") or None

    return StreamingResponse(
        _agentic_event_stream(
            user_id=user_id,
            session_id=session_id,
            message=message,
            provider_override=provider_override,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def _agentic_event_stream(
    *,
    user_id: str,
    session_id: str,
    message: str,
    provider_override: str | None = None,
):
    """Yield SSE-formatted events for one agentic run."""
    # Resolve provider
    mode = get_agentic_mode(override=provider_override)
    if not mode:
        yield _sse({"type": "error", "text": "No AI provider configured"})
        return

    # Build the system prompt (same as /chat)
    platform_suffix = PLATFORM_PROMPTS.get("web", "")
    system_prompt = build_agent_context(
        user_id,
        platform_suffix + BASE_SYSTEM_PROMPT,
        provider=mode,
    )

    # Create the agent (web tier — destructive tools held for OTP)
    try:
        agent = create_agent(mode, session_id, system_prompt)
        agent.tool_scope = TIER_WEB
    except Exception as e:
        _logger.exception("Agent creation failed (%s)", mode)
        yield _sse({"type": "error", "text": f"Agent creation failed: {e}"})
        return

    # Tool inventory for the planner
    inventory = _agent_tool_inventory(agent)

    # ── Phase 1: planner ──────────────────────────────────────────────
    yield _sse({"type": "thinking", "text": "Planning…"})

    try:
        plan = await generate_plan(
            user_message=message,
            tool_inventory=inventory,
            system_prompt=system_prompt,
            user_id=user_id,
            session_id=f"{session_id}:planner",
        )
    except Exception as e:
        # generate_plan is documented to never raise, but be defensive.
        _logger.exception("Planner raised unexpectedly")
        yield _sse({"type": "error", "text": f"Planner failed: {e}"})
        return

    plan_event = {
        "type": "plan",
        "plan_id": plan.plan_id,
        "model": plan.model,
        "data": plan.to_event_dict(),
    }
    yield _sse(plan_event)

    # Build tool→step_id map (best-effort: first occurrence wins).
    tool_to_step: dict[str, int] = {}
    for step in plan.steps:
        for tool_name in step.tools:
            tool_to_step.setdefault(tool_name, step.step_id)

    # ── Phase 2: execution ────────────────────────────────────────────

    held_actions: list[dict] = []

    def confirmation_callback(tool_name, tool_args, description):
        action_id, otp = create_pending_action(
            user_id=user_id,
            tool_name=tool_name,
            tool_args=tool_args,
            session_id=session_id,
            description=description,
        )
        held_actions.append({
            "action_id": action_id,
            "tool_name": tool_name,
            "otp": otp,
            "description": description,
        })
        return {
            "held": True,
            "action_id": action_id,
            "tool": tool_name,
            "message": (
                f"Action '{tool_name}' requires confirmation. "
                f"A verification code has been sent to your Telegram."
            ),
        }

    # Per-run event emitter so the runner-side callback can drop events into
    # the SSE stream without blocking on yield ordering.
    emitter = EventEmitter()

    async def on_tool_event(event: dict[str, Any]):
        # Enrich tool events with plan_id + best-effort step_id mapping.
        event = dict(event)
        event["plan_id"] = plan.plan_id
        tool_name = event.get("tool")
        if tool_name and tool_name in tool_to_step:
            event["step_id"] = tool_to_step[tool_name]
        event_type = event.pop("type", "tool_event")
        emitter.emit(event_type, event)

    async def on_progress(text: str):
        # Surface free-form progress text as the existing 'thinking' event.
        emitter.emit("thinking", {"text": text})

    # Persist the user message before kicking off
    save_chat_history(session_id, "user", message, user_id)

    # Kick off the agent run in the background and forward events as they arrive.
    output_box: dict[str, Any] = {}

    async def run_agent_task():
        try:
            output = await agent.run(
                message,
                user_id=user_id,
                on_progress=on_progress,
                confirmation_callback=confirmation_callback,
                on_tool_event=on_tool_event,
            )
            output_box["output"] = output or ""
        except Exception as e:
            _logger.exception("Agentic agent.run() failed")
            output_box["error"] = str(e)
        finally:
            emitter.close()

    task = asyncio.create_task(run_agent_task())

    # Stream emitter-formatted SSE lines as they arrive.
    try:
        async for line in emitter.stream():
            yield line
    finally:
        if not task.done():
            await task

    # Persist final response
    output = output_box.get("output") or ""
    if output:
        save_chat_history(session_id, "assistant", output, user_id)

    # Dispatch any OTPs that the executor stacked up
    for action in held_actions:
        asyncio.ensure_future(send_otp_via_telegram(
            user_id=user_id,
            otp=action["otp"],
            tool_name=action["tool_name"],
            description=action["description"],
            action_id=action["action_id"],
        ))

    # Held-action summary (no OTPs)
    if held_actions:
        yield _sse({
            "type": "held",
            "plan_id": plan.plan_id,
            "actions": [
                {
                    "action_id": a["action_id"],
                    "tool_name": a["tool_name"],
                    "description": a["description"],
                }
                for a in held_actions
            ],
        })

    # Final response or error
    if "error" in output_box:
        yield _sse({
            "type": "error",
            "plan_id": plan.plan_id,
            "text": f"Agent error: {output_box['error']}",
        })
    else:
        yield _sse({
            "type": "final",
            "plan_id": plan.plan_id,
            "text": output,
        })

    yield "data: [DONE]\n\n"


def _sse(payload: dict[str, Any]) -> str:
    """Serialize one SSE event line."""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ── Held-action mgmt (same shape as /chat) ───────────────────────────

@router.post("/confirm")
async def confirm_action(request: Request):
    if not AGENTIC_WORKFLOW_ENABLED:
        return JSONResponse({"error": "Agentic workflow is not enabled"}, status_code=404)
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action_id = body.get("action_id")
    otp = body.get("otp", "").strip()
    if not action_id or not otp:
        return JSONResponse({"error": "action_id and otp required"}, status_code=400)

    result = verify_otp(action_id, otp)
    if not result["ok"]:
        return JSONResponse({"error": result["error"]}, status_code=400)

    from roost.gemini_agent import _execute_tool, TIER_FULL

    tool_result = _execute_tool(
        result["tool_name"],
        result["tool_args"],
        user_id=result["user_id"],
        tool_scope=TIER_FULL,
    )
    _logger.info(
        "Confirmed agentic action #%d: %s for user %s",
        action_id, result["tool_name"], result["user_id"],
    )
    return JSONResponse({
        "ok": True,
        "tool_name": result["tool_name"],
        "result": tool_result,
    })


@router.post("/cancel")
async def cancel_held_action(request: Request):
    if not AGENTIC_WORKFLOW_ENABLED:
        return JSONResponse({"error": "Agentic workflow is not enabled"}, status_code=404)
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    action_id = body.get("action_id")
    if not action_id:
        return JSONResponse({"error": "action_id required"}, status_code=400)

    cancel_action(action_id)
    return JSONResponse({"ok": True})


@router.get("/pending")
async def list_pending(request: Request):
    if not AGENTIC_WORKFLOW_ENABLED:
        return JSONResponse({"error": "Agentic workflow is not enabled"}, status_code=404)
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    user_id = str(user.get("user_id", "1"))
    actions = get_pending_actions(user_id)
    return JSONResponse({"actions": actions})


# Exposed for test harness convenience (importing private symbols would be ugly).
__all__ = ["router", "_agent_tool_inventory"]
