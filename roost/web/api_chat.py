"""Web chat API — AI agent with OTP confirmation for destructive actions.

Endpoints:
- POST /api/chat/message — Send a message to the AI agent (SSE stream)
- POST /api/chat/confirm — Confirm a held action with OTP
- POST /api/chat/cancel — Cancel a held action
- GET  /api/chat/pending — List pending confirmations
"""

import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from roost.adapters import (
    run_agent, get_agentic_mode, create_agent, truncate,
    PLATFORM_PROMPTS, BASE_SYSTEM_PROMPT,
)
from roost.config import AGENT_ENABLED
from roost.context import build_agent_context, save_chat_history

# Tool scope constant — matches gemini_agent.TIER_WEB without importing the module
# (gemini_agent requires google-genai SDK which may not be installed in lite builds)
TIER_WEB = "web"
from roost.services.action_confirmations import (
    create_pending_action,
    verify_otp,
    cancel_action,
    get_pending_actions,
    send_otp_via_telegram,
    is_destructive,
)

router = APIRouter(prefix="/api/chat", tags=["chat"])
_logger = logging.getLogger("roost.web.chat")

# Rate limiting: per-user message tracking
_last_message: dict[str, float] = {}
_RATE_LIMIT_SECONDS = 6  # Min seconds between messages per user


def _get_user(request: Request) -> dict | None:
    """Extract current user from request state."""
    return getattr(request.state, "current_user", None)


@router.post("/message")
async def send_message(request: Request):
    """Send a message to the AI agent. Returns SSE stream.

    Body: {"message": "...", "session_id": "optional"}
    """
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

    # Rate limiting
    import time
    now = time.time()
    last = _last_message.get(user_id, 0)
    if now - last < _RATE_LIMIT_SECONDS:
        wait = int(_RATE_LIMIT_SECONDS - (now - last)) + 1
        return JSONResponse(
            {"error": f"Slow down — try again in {wait}s"},
            status_code=429,
        )
    _last_message[user_id] = now

    session_id = body.get("session_id", f"web:{user_id}:agent")

    # Held actions collector — filled by confirmation_callback
    held_actions = []

    def confirmation_callback(tool_name, tool_args, description):
        """Called when agent tries to use a destructive tool."""
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
            "otp": otp,  # Will be sent to Telegram, NOT to the browser
            "description": description,
        })
        return {
            "held": True,
            "action_id": action_id,
            "tool": tool_name,
            "message": (
                f"Action '{tool_name}' requires confirmation. "
                f"A verification code has been sent to your Telegram. "
                f"Enter the code to proceed."
            ),
        }

    async def event_stream():
        """SSE stream: progress updates, then final response."""
        progress_messages = []

        async def on_progress(text):
            progress_messages.append(text)
            yield_text = json.dumps({"type": "progress", "text": text})
            # SSE format: we'll collect and send after

        # Build system prompt for web
        mode = get_agentic_mode()
        if not mode:
            yield f"data: {json.dumps({'type': 'error', 'text': 'No AI provider configured'})}\n\n"
            return

        platform_suffix = PLATFORM_PROMPTS.get("web", (
            "Operational context: this is a web browser chat interface.\n"
            "- Use markdown for formatting.\n"
            "- Keep responses clear and well-structured.\n"
            "- When a destructive action is held for confirmation, explain what will happen and ask the user to enter the OTP from their Telegram.\n"
        ))
        system_prompt = build_agent_context(
            user_id,
            platform_suffix + BASE_SYSTEM_PROMPT,
            provider=mode,
        )

        # Create agent with TIER_WEB scope
        try:
            agent = create_agent(mode, session_id, system_prompt)
            agent.tool_scope = TIER_WEB  # Override scope for web
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'text': str(e)})}\n\n"
            return

        # Persist user message
        save_chat_history(session_id, "user", message, user_id)

        # Progress callback for SSE
        async def sse_progress(text):
            pass  # We'll track via held_actions instead

        # Send "thinking" event
        yield f"data: {json.dumps({'type': 'thinking', 'text': f'Thinking ({mode})...'})}\n\n"

        try:
            output = await agent.run(
                message,
                user_id=user_id,
                on_progress=sse_progress,
                confirmation_callback=confirmation_callback,
            )
        except Exception as e:
            _logger.exception("Agent.run() failed")
            output = f"Agent error: {e}"

        # Persist response
        if output:
            save_chat_history(session_id, "assistant", output, user_id)

        # Send OTPs to Telegram for any held actions
        for action in held_actions:
            asyncio.ensure_future(send_otp_via_telegram(
                user_id=user_id,
                otp=action["otp"],
                tool_name=action["tool_name"],
                description=action["description"],
                action_id=action["action_id"],
            ))

        # Send held actions info (without OTP!)
        if held_actions:
            yield f"data: {json.dumps({'type': 'confirmation_required', 'actions': [{'action_id': a['action_id'], 'tool_name': a['tool_name'], 'description': a['description']} for a in held_actions]})}\n\n"

        # Send final response
        yield f"data: {json.dumps({'type': 'response', 'text': output})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.post("/confirm")
async def confirm_action(request: Request):
    """Confirm a held action with OTP.

    Body: {"action_id": 123, "otp": "123456"}
    """
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

    # OTP verified — execute the held tool
    from roost.gemini_agent import _execute_tool, TIER_FULL

    tool_result = _execute_tool(
        result["tool_name"],
        result["tool_args"],
        user_id=result["user_id"],
        tool_scope=TIER_FULL,  # Bypass web scope — already confirmed
    )

    _logger.info(
        "Confirmed action #%d: %s for user %s",
        action_id, result["tool_name"], result["user_id"],
    )

    return JSONResponse({
        "ok": True,
        "tool_name": result["tool_name"],
        "result": tool_result,
    })


@router.post("/cancel")
async def cancel_held_action(request: Request):
    """Cancel a pending action.

    Body: {"action_id": 123}
    """
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
    """List pending confirmations for the current user."""
    user = _get_user(request)
    if not user:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)

    user_id = str(user.get("user_id", "1"))
    actions = get_pending_actions(user_id)
    return JSONResponse({"actions": actions})
