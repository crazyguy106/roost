"""Summarize unread emails into bullet points.

Reads the Gmail inbox via the existing Roost email service, then asks the
agent's AI provider to condense each message into one line.

No emails are marked as read, deleted, or replied to. This is a pure
read-only skill — safe to auto-run on a cron.
"""

import logging

logger = logging.getLogger(__name__)

SKILL_META = {
    "name": "email_summary",
    "description": "Summarize your unread emails into bullet points.",
    "trigger": "inbox",
    "version": "1.0.0",
    "risk_tier": "read_only",
}


async def run(args: dict) -> str:
    """
    Expected args:
        limit: int  — max number of unread emails to summarize (default 10)

    Returns a Markdown bullet list, one bullet per email.
    """
    try:
        limit = int(args.get("limit", 10))
    except (TypeError, ValueError):
        limit = 10
    limit = max(1, min(limit, 25))  # clamp to 1..25

    # gmail_helpers.search_messages is the raw (non-MCP-wrapped) helper.
    # Prefer it in skills — it's directly callable and returns a plain list.
    try:
        from roost.mcp.gmail_helpers import search_messages
    except ImportError:
        return "Gmail helpers are unavailable. Enable GOOGLE_ENABLED/GMAIL_ENABLED in .env."

    try:
        messages = search_messages(query="is:unread", max_results=limit)
    except Exception as e:
        logger.exception("email search failed")
        return f"Could not fetch emails: {e}"

    if not messages:
        return "Inbox zero. Nothing unread."

    # Ask the AI for a one-liner per email. gemini_generate is an
    # @mcp.tool()-decorated function — it exposes the raw callable via .fn
    try:
        from roost.mcp.tools_gemini import gemini_generate as _tool
        gemini_generate = getattr(_tool, "fn", _tool)
    except ImportError:
        # Fallback: plain list without AI summaries
        lines = [
            f"- **{m.get('from', 'unknown')}** — {m.get('subject', '(no subject)')}"
            for m in messages
        ]
        return "## Unread emails\n\n" + "\n".join(lines)

    # Build one condensed prompt covering all messages
    joined = "\n\n---\n\n".join(
        f"From: {m.get('from', 'unknown')}\n"
        f"Subject: {m.get('subject', '(no subject)')}\n"
        f"Snippet: {m.get('snippet', '')[:500]}"
        for m in messages
    )

    prompt = (
        "Summarize each email below into ONE concise bullet point. "
        "Format: '- **Sender** — what they want/need in under 15 words.'\n\n"
        f"{joined}"
    )

    try:
        result = gemini_generate(prompt=prompt, temperature=0.3, max_output_tokens=2048)
        # gemini_generate returns a dict with {"text": ...} on success
        summary = result.get("text", "") if isinstance(result, dict) else str(result)
        return f"## {len(messages)} unread emails\n\n{summary}"
    except Exception as e:
        logger.exception("AI summarization failed")
        return f"Fetched {len(messages)} emails but summarization failed: {e}"
