"""REST API endpoints for email triage — search, read, draft, send, archive."""

import logging
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Request
from roost.web.rate_limit import limiter

logger = logging.getLogger("roost.web.api_email")

router = APIRouter(prefix="/api/emails")

MAX_BODY_CHARS_LIST = 2000  # Truncate bodies in thread view


# ── Request models ────────────────────────────────────────────────────

class DraftAIRequest(BaseModel):
    thread_id: str
    instruction: str

class SendRequest(BaseModel):
    to: str
    subject: str
    body: str
    cc: str = ""
    thread_id: str = ""

class TaskFromEmailRequest(BaseModel):
    subject: str
    from_name: str = ""
    thread_id: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/search")
def email_search(q: str = "is:unread label:INBOX", limit: int = 10):
    """Search emails. Returns {emails: [...]}."""
    try:
        from roost.mcp.gmail_helpers import search_messages
        messages = search_messages(q, max_results=limit)
        return {"emails": messages}
    except Exception as e:
        logger.exception("Email search failed")
        return {"error": str(e), "emails": []}


@router.get("/thread/{thread_id}")
def email_thread(thread_id: str):
    """Read full thread with body truncation."""
    try:
        from roost.mcp.gmail_helpers import read_thread
        data = read_thread(thread_id)
        # Truncate long bodies for the web UI
        for msg in data.get("messages", []):
            body = msg.get("body", "")
            if len(body) > MAX_BODY_CHARS_LIST:
                msg["body"] = body[:MAX_BODY_CHARS_LIST] + "\n\n[... truncated ...]"
        return data
    except Exception as e:
        logger.exception("Thread read failed")
        raise HTTPException(502, f"Gmail error: {e}")


@router.post("/draft-ai")
async def email_draft_ai(req: DraftAIRequest):
    """Generate AI draft reply. Returns {body: '...'}."""
    try:
        from roost.mcp.gmail_helpers import read_thread
        thread = read_thread(req.thread_id)
    except Exception as e:
        logger.exception("Failed to read thread for AI draft")
        raise HTTPException(502, f"Gmail error: {e}")

    try:
        from roost.bot.email_draft import draft_reply
        body = await draft_reply(thread.get("messages", []), req.instruction)
        if body.startswith("[Error"):
            raise HTTPException(502, body)
        return {"body": body}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("AI draft generation failed")
        raise HTTPException(500, f"Draft error: {e}")


@router.post("/send")
@limiter.limit("10/minute")
def email_send(request: Request, req: SendRequest):
    """Send email (with auto-threading headers for replies)."""
    try:
        from roost.mcp.gmail_helpers import send_message, read_thread

        in_reply_to = ""
        references = ""
        if req.thread_id:
            try:
                thread = read_thread(req.thread_id)
                if thread["messages"]:
                    last_msg = thread["messages"][-1]
                    in_reply_to = last_msg.get("message_id", "")
                    references = last_msg.get("references", "")
                    if in_reply_to:
                        references = f"{references} {in_reply_to}".strip()
            except Exception:
                logger.debug("Failed to fetch threading headers for reply", exc_info=True)

        result = send_message(
            to=req.to,
            subject=req.subject,
            body=req.body,
            cc=req.cc,
            thread_id=req.thread_id,
            in_reply_to=in_reply_to,
            references=references,
        )

        if "error" in result:
            raise HTTPException(502, result["error"])
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Email send failed")
        raise HTTPException(500, f"Send error: {e}")


@router.post("/archive/{thread_id}")
def email_archive(thread_id: str):
    """Archive thread (remove INBOX label)."""
    try:
        from roost.gmail import get_gmail_service
        service = get_gmail_service()
        if not service:
            raise HTTPException(502, "Gmail not available")

        service.users().threads().modify(
            userId="me",
            id=thread_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return {"ok": True}

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Archive failed")
        raise HTTPException(500, f"Archive error: {e}")


@router.post("/task")
def email_create_task(req: TaskFromEmailRequest):
    """Create a task from an email."""
    try:
        from roost import task_service
        from roost.models import TaskCreate

        task = task_service.create_task(
            TaskCreate(
                title=f"Email: {req.subject}",
                description=f"From: {req.from_name}\nThread: {req.thread_id}",
            ),
            source="web",
        )
        return {"id": task.id, "title": task.title}

    except Exception as e:
        logger.exception("Task creation from email failed")
        raise HTTPException(500, f"Task error: {e}")
