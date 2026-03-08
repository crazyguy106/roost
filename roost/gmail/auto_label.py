"""Auto-label and action-state cycling for Gmail.

Two responsibilities:
1. Auto-label: Apply user labels to incoming messages based on sender domain rules.
2. Action cycling: Manage (To Reply) ↔ (Waiting for Reply) state transitions.
   - When user sends a reply to a (To Reply) thread → remove (To Reply), add (Waiting for Reply)
   - When a new inbound message arrives on a (Waiting for Reply) thread → move to (To Reply)
"""

import logging
import time

logger = logging.getLogger("roost.gmail.auto_label")

# Sender domain → label name mapping
# These are applied to any unlabelled message from the domain.
# Configure with your own domain→label rules.
DOMAIN_LABEL_RULES: dict[str, str] = {
    # Example: "partner.com": "Partners/#partner-name",
}

# Cache for label name → ID mapping
_label_cache: dict[str, str] = {}
_action_label_ids: dict[str, str] = {}  # "to_reply" / "waiting" → label ID
_last_history_id: str | None = None
_my_email: str = ""


def _ensure_label_cache(service) -> None:
    """Build label name→ID cache if not already populated."""
    global _my_email
    if _label_cache:
        return

    labels = service.users().labels().list(userId="me").execute()
    for label in labels.get("labels", []):
        _label_cache[label["name"]] = label["id"]

    _action_label_ids["to_reply"] = _label_cache.get("(To Reply)", "")
    _action_label_ids["waiting"] = _label_cache.get("(Waiting for Reply)", "")

    if not _my_email:
        from roost.config import GMAIL_SEND_FROM
        _my_email = GMAIL_SEND_FROM.lower()

    if not _action_label_ids["to_reply"]:
        logger.warning("(To Reply) label not found")
    if not _action_label_ids["waiting"]:
        logger.warning("(Waiting for Reply) label not found")


def auto_label_recent(service, minutes: int = 10) -> int:
    """Apply domain-based labels to recent unlabelled inbox messages.

    Args:
        service: Authenticated Gmail API service
        minutes: How far back to look (default 10 min, matching poll interval)

    Returns:
        Number of messages labelled
    """
    _ensure_label_cache(service)

    # Search for recent inbox messages
    after_epoch = int(time.time()) - (minutes * 60)
    query = f"in:inbox after:{after_epoch}"

    try:
        results = service.users().messages().list(
            userId="me", q=query, maxResults=100,
        ).execute()
    except Exception:
        logger.exception("Failed to list recent messages for auto-labelling")
        return 0

    messages = results.get("messages", [])
    if not messages:
        return 0

    labelled = 0
    for msg_ref in messages:
        try:
            msg = service.users().messages().get(
                userId="me", id=msg_ref["id"], format="metadata",
                metadataHeaders=["From"],
            ).execute()

            # Skip if already has a user label
            label_ids = msg.get("labelIds", [])
            has_user_label = any(
                lid.startswith("Label_") for lid in label_ids
            )
            if has_user_label:
                continue

            # Extract sender domain
            from_header = ""
            for h in msg.get("payload", {}).get("headers", []):
                if h["name"] == "From":
                    from_header = h["value"].lower()
                    break

            import re
            domain_match = re.search(r"@([\w.-]+)", from_header)
            if not domain_match:
                continue
            domain = domain_match.group(1)

            # Check rules
            target_label = DOMAIN_LABEL_RULES.get(domain)
            if not target_label:
                continue

            target_id = _label_cache.get(target_label)
            if not target_id:
                logger.warning("Label %s not found for domain %s", target_label, domain)
                continue

            service.users().messages().modify(
                userId="me", id=msg_ref["id"],
                body={"addLabelIds": [target_id]},
            ).execute()
            labelled += 1

        except Exception:
            logger.exception("Failed to auto-label message %s", msg_ref["id"])

    if labelled:
        logger.info("Auto-labelled %d messages", labelled)
    return labelled


def cycle_action_labels(service) -> dict[str, int]:
    """Manage (To Reply) ↔ (Waiting for Reply) transitions.

    1. Threads in (To Reply) where last message is FROM user → move to (Waiting for Reply)
    2. Threads in (Waiting for Reply) where last message is NOT from user → move to (To Reply)

    Returns:
        Dict with counts: {"to_waiting": N, "to_action": N}
    """
    _ensure_label_cache(service)

    to_reply_id = _action_label_ids.get("to_reply")
    waiting_id = _action_label_ids.get("waiting")

    if not to_reply_id or not waiting_id:
        return {"to_waiting": 0, "to_action": 0}

    stats = {"to_waiting": 0, "to_action": 0}

    # 1. Check (To Reply) threads — move replied ones to (Waiting for Reply)
    try:
        threads = _get_all_threads(service, to_reply_id)
        for t in threads:
            try:
                thread = service.users().threads().get(
                    userId="me", id=t["id"], format="metadata",
                    metadataHeaders=["From"],
                ).execute()

                last_msg = thread["messages"][-1]
                from_header = _get_header(last_msg, "From").lower()

                if _my_email and _my_email in from_header:
                    service.users().threads().modify(
                        userId="me", id=t["id"],
                        body={
                            "removeLabelIds": [to_reply_id],
                            "addLabelIds": [waiting_id],
                        },
                    ).execute()
                    stats["to_waiting"] += 1

            except Exception:
                logger.exception("Failed to process (To Reply) thread %s", t["id"])

    except Exception:
        logger.exception("Failed to list (To Reply) threads")

    # 2. Check (Waiting for Reply) threads — move replied-to ones back to (To Reply)
    try:
        threads = _get_all_threads(service, waiting_id)
        for t in threads:
            try:
                thread = service.users().threads().get(
                    userId="me", id=t["id"], format="metadata",
                    metadataHeaders=["From"],
                ).execute()

                last_msg = thread["messages"][-1]
                from_header = _get_header(last_msg, "From").lower()

                if _my_email and _my_email not in from_header:
                    service.users().threads().modify(
                        userId="me", id=t["id"],
                        body={
                            "removeLabelIds": [waiting_id],
                            "addLabelIds": [to_reply_id],
                        },
                    ).execute()
                    stats["to_action"] += 1

            except Exception:
                logger.exception("Failed to process (Waiting) thread %s", t["id"])

    except Exception:
        logger.exception("Failed to list (Waiting for Reply) threads")

    if stats["to_waiting"] or stats["to_action"]:
        logger.info(
            "Action cycling: %d → Waiting, %d → To Reply",
            stats["to_waiting"], stats["to_action"],
        )
    return stats


def _get_all_threads(service, label_id: str) -> list[dict]:
    """Get all threads with a given label."""
    threads = []
    page_token = None
    while True:
        result = service.users().threads().list(
            userId="me", labelIds=[label_id], pageToken=page_token,
        ).execute()
        threads.extend(result.get("threads", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            break
    return threads


def _get_header(msg: dict, name: str) -> str:
    """Extract a header value from a message."""
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"] == name:
            return h["value"]
    return ""
