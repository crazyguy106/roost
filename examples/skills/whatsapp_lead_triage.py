"""WhatsApp lead triage — hook a skill into the AI CDR + recipes pipeline.

This is the most advanced example in the gallery. It shows how a skill
can be invoked by the automation_recipes engine when an inbound
WhatsApp (or WeChat) message arrives, use the AI CDR classifier to
understand it safely, and pick a response template.

The actual classification runs in the tool-less sandbox before this
skill is ever called — by the time `run()` executes, the message has
already been sanitized, framed, detonated in a no-tool AI, and
validated against a schema. Your skill just gets the clean result.

Usage:
    1. Create response templates in the web UI or via /template
    2. Create an automation recipe:
         /recipe new whatsapp_triage external_write
         Instructions: "Triage inbound WhatsApp leads"
    3. Link templates to the recipe
    4. This skill reads the classification output and shapes a reply

Because the recipe runs at `external_write` tier, any draft this skill
produces is held for your approval via Telegram before anything is
sent.
"""

import logging

logger = logging.getLogger(__name__)

SKILL_META = {
    "name": "whatsapp_lead_triage",
    "description": "Triage inbound WhatsApp lead using AI CDR + response templates.",
    "trigger": "wa_triage",
    "version": "1.0.0",
    "risk_tier": "external_write",
}


async def run(args: dict) -> str:
    """
    Expected args (passed from the recipe engine):
        message:    str  — the raw inbound WhatsApp message body
        sender:     str  — WhatsApp phone number or display name
        recipe_id:  int  — which recipe fired this (optional)

    Returns the drafted reply. The reply is NOT sent by this skill —
    the recipe engine holds it for human approval before dispatch.
    """
    message = args.get("message", "").strip()
    sender = args.get("sender", "unknown")

    if not message:
        return "No message body to triage."

    # 1. Classify the inbound message via AI CDR (tool-less sandbox).
    #    The recipes pipeline normally does this, but calling it here
    #    too makes the skill self-contained for ad-hoc use.
    try:
        from roost.extras.messaging_external.services.ai_cdr import classify_message
        from roost.services.response_templates import list_templates
    except ImportError:
        return "AI CDR or template service not available."

    templates = list_templates(active_only=True)
    if not templates:
        return "No response templates configured. Add some via /template."

    try:
        classification = await classify_message(
            message=message,
            sender=sender,
            templates=templates,
        )
    except Exception as e:
        logger.exception("classification failed")
        return f"Could not classify message: {e}"

    intent = classification.get("intent", "unknown")
    urgency = classification.get("urgency", "cold")
    extracted = classification.get("extracted_fields", {})
    confidence = classification.get("confidence", 0.0)

    # 2. Pick a template whose intent_tags match
    picked = _pick_template(templates, intent)
    if not picked:
        return (
            f"Classified as **{intent}** ({urgency}, {confidence:.0%}) but no "
            f"matching template. Draft manually for {sender}."
        )

    # 3. Fill the template using the extracted fields + sender
    body = picked["body"]
    fields = {"name": sender, **extracted}
    try:
        filled = body.format_map(_SafeDict(fields))
    except Exception:
        filled = body  # Fallback to raw template if formatting fails

    return (
        f"## Draft reply for {sender}\n\n"
        f"**Intent:** {intent} ({urgency}, confidence {confidence:.0%})\n"
        f"**Template:** {picked['name']}\n\n"
        f"---\n{filled}\n---\n\n"
        f"_This draft is held by the recipe engine. Approve via Telegram to send._"
    )


def _pick_template(templates: list[dict], intent: str) -> dict | None:
    """First active template whose intent_tags include the classified intent."""
    for t in templates:
        tags = t.get("intent_tags") or []
        if intent in tags:
            return t
    return None


class _SafeDict(dict):
    """dict subclass that returns '{key}' unchanged for missing keys.

    Prevents a KeyError if the template references a field the
    classifier didn't extract.
    """
    def __missing__(self, key):
        return "{" + key + "}"
