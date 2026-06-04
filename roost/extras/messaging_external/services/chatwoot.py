"""Chatwoot adapter — self-hosted helpdesk integration.

Used by the FA edition to front WhatsApp Cloud / WeChat / Email behind a
single Chatwoot inbox, so the salesperson sees one unified queue and the
Roost agent talks to one channel. Captured against Chatwoot 4.14.1 — see
`docs/chatwoot-webhook-samples/` for the real payload shapes this parses.

Two surfaces:

- **Inbound:** Chatwoot fires HMAC-signed webhooks. `verify_webhook_signature`
  validates `X-Chatwoot-Signature = sha256(secret, "<ts>.<body>")` and the
  matching `X-Chatwoot-Timestamp`. `parse_webhook_event` normalises the
  envelope into a uniform dict; the router only runs the AI pipeline on
  `message_created` + `message_type == "incoming"` (see quirk #6 in the
  samples README — `conversation_updated` is chatty and must be ignored).

- **Outbound:** REST API calls with `api_access_token` header. send_message,
  create_conversation, find_or_create_contact, mark_as_read, mark_as_resolved.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import time
from pathlib import Path
from typing import Any

import httpx

from roost.config import (
    CHATWOOT_ACCOUNT_ID,
    CHATWOOT_API_KEY,
    CHATWOOT_ENABLED,
    CHATWOOT_INBOX_ID,
    CHATWOOT_URL,
    CHATWOOT_WEBHOOK_SECRET,
)
from roost.extras.messaging_external.services.text_format import collapse_soft_wraps

logger = logging.getLogger("roost.chatwoot")

# Replay window for HMAC verification — reject requests where the
# Chatwoot-supplied timestamp is more than this many seconds off the wall
# clock. 300s matches what Chatwoot itself documents for verification.
_REPLAY_WINDOW_SECONDS = 300


def verify_webhook_signature(
    ts: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify a Chatwoot 4.14.1 webhook delivery.

    Algorithm (from `/app/lib/webhooks/trigger.rb` in Chatwoot 4.14.1):
        signature = "sha256=" + HMAC_SHA256(secret, "<ts>.<body>")

    Args:
        ts: value of the `X-Chatwoot-Timestamp` header (unix seconds).
        body: raw request body bytes.
        signature: value of the `X-Chatwoot-Signature` header
            (e.g. "sha256=4097ae...").

    Returns False on any of: missing secret, missing/empty inputs, malformed
    header, timestamp skew greater than the replay window, digest mismatch.
    """
    if not CHATWOOT_WEBHOOK_SECRET:
        logger.warning("CHATWOOT_WEBHOOK_SECRET not set — cannot verify webhook")
        return False
    if not (ts and body and signature and signature.startswith("sha256=")):
        return False

    try:
        skew = abs(int(time.time()) - int(ts))
    except (TypeError, ValueError):
        return False
    if skew > _REPLAY_WINDOW_SECONDS:
        logger.warning("Chatwoot webhook ts skew %ss — rejecting", skew)
        return False

    expected = hmac.new(
        CHATWOOT_WEBHOOK_SECRET.encode(),
        f"{ts}.{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, f"sha256={expected}")


def parse_webhook_event(payload: dict) -> dict:
    """Normalise a Chatwoot 4.14.1 webhook envelope into a uniform shape.

    Returns a dict with keys:
        event: top-level event string ("message_created", "conversation_created",
               "conversation_updated", "conversation_status_changed",
               "contact_created", "contact_updated")
        message_type: for message_created only — "incoming" | "outgoing"
                      | "template" | "activity". Empty string otherwise.
                      ALWAYS read this string field, not the nested int.
        account_id: int (top-level `account.id` when present, else from
                    `inbox.account_id` or `conversation.account_id`)
        conversation_id: int | None
        inbox_id: int | None
        source_id: str — Channel::Whatsapp → E.164 phone, Channel::Api → UUID.
                   Pulled from `conversation.contact_inbox.source_id`, the
                   canonical "who-to-reply-to" field (sender.phone_number is
                   contact-only and missing for web-widget visitors).
        channel: Rails STI string ("Channel::Whatsapp", "Channel::Api", ...)
        content: message body when event is message_created, else "".
        contact: {id, name, phone, email} when known, else {}.
        message_id: int | None (top-level `id` for message_created)
        raw: the original payload (for handlers that need extras)

    The router branches on `event` + `message_type`. Quirk: top-level
    `message_type` is a string ("incoming"/"outgoing"); the same field
    nested at `conversation.messages[].message_type` is an int (0/1/2/3) —
    we ignore the nested form entirely.
    """
    event = payload.get("event", "") or ""
    out: dict[str, Any] = {
        "event": event,
        "message_type": "",
        "account_id": None,
        "conversation_id": None,
        "inbox_id": None,
        "source_id": "",
        "channel": "",
        "content": "",
        "contact": {},
        "message_id": None,
        "raw": payload,
    }

    if event == "message_created":
        out["message_type"] = payload.get("message_type", "") or ""
        out["content"] = payload.get("content", "") or ""
        out["message_id"] = payload.get("id")
        out["account_id"] = (payload.get("account") or {}).get("id")
        out["inbox_id"] = (payload.get("inbox") or {}).get("id")

        conv = payload.get("conversation") or {}
        out["conversation_id"] = conv.get("id")
        out["channel"] = conv.get("channel", "") or ""
        ci = conv.get("contact_inbox") or {}
        out["source_id"] = ci.get("source_id", "") or ""

        # For incoming messages the contact is top-level `sender`; for
        # outgoing it's inside `conversation.meta.sender`. Quirk #3.
        if out["message_type"] == "incoming":
            sender = payload.get("sender") or {}
        else:
            sender = (conv.get("meta") or {}).get("sender") or {}
        out["contact"] = _contact_view(sender)

    elif event in ("conversation_created", "conversation_updated",
                   "conversation_status_changed"):
        # These three share an envelope (24 vs 23 top-level keys — the
        # status/updated variants add `changed_attributes`). One parser,
        # three thin event hooks — quirk #5.
        out["conversation_id"] = payload.get("id")
        out["inbox_id"] = payload.get("inbox_id")
        out["channel"] = payload.get("channel", "") or ""
        ci = payload.get("contact_inbox") or {}
        out["source_id"] = ci.get("source_id", "") or ""
        sender = (payload.get("meta") or {}).get("sender") or {}
        out["contact"] = _contact_view(sender)
        # account_id isn't on conversation envelopes; consumer can fall
        # back to env CHATWOOT_ACCOUNT_ID when this is None.

    elif event in ("contact_created", "contact_updated"):
        out["contact"] = _contact_view(payload)
        out["account_id"] = (payload.get("account") or {}).get("id")

    return out


def _contact_view(sender: dict) -> dict:
    """Project a Chatwoot contact-shaped dict into the adapter's normalised form."""
    return {
        "id": sender.get("id"),
        "name": sender.get("name", "") or "",
        "phone": sender.get("phone_number", "") or "",
        "email": sender.get("email", "") or "",
    }


# ─────────────────────────── Outbound API ────────────────────────────────


def _api_base() -> str | None:
    """Compose the per-account API base URL or return None when unconfigured."""
    if not (CHATWOOT_URL and CHATWOOT_API_KEY and CHATWOOT_ACCOUNT_ID):
        return None
    return f"{CHATWOOT_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}"


def _headers() -> dict[str, str]:
    return {
        "api_access_token": CHATWOOT_API_KEY,
        "Content-Type": "application/json",
    }


def send_message(
    conversation_id: int,
    content: str,
    *,
    message_type: str = "outgoing",
    private: bool = False,
) -> dict:
    """Post a message into a Chatwoot conversation.

    `message_type="outgoing"` is a normal reply (visible to the contact).
    `private=True` produces an internal note — invisible to the contact
    but recorded in the conversation timeline, useful for agent handoff.

    Returns {"ok": True, "message_id": int} on success or {"error": ...}.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    content = collapse_soft_wraps(content)
    url = f"{base}/conversations/{conversation_id}/messages"
    payload = {
        "content": content,
        "message_type": message_type,
        "private": private,
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(url, headers=_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()
            msg_id = data.get("id")
            logger.info("Chatwoot msg sent conv=%s id=%s", conversation_id, msg_id)
            return {"ok": True, "message_id": msg_id, "raw": data}
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        logger.error("Chatwoot send error: %s %s", e.response.status_code, details)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot send failed")
        return {"error": str(e)}


def find_or_create_contact(
    *,
    phone: str = "",
    name: str = "",
    email: str = "",
    inbox_id: int | str | None = None,
) -> dict:
    """Look up a contact by phone/email; create it if missing.

    Search order: phone, then email. The contact API's `/search` endpoint is
    a substring search across name/phone/email/identifier — we narrow by
    exact match on the returned rows. Returns
    {"ok": True, "contact_id": int, "source_id": str | "", "created": bool}.

    `source_id` is taken from the contact's contact_inbox row for the
    target inbox (CHATWOOT_INBOX_ID by default) — adapter callers use this
    to send messages back through the right channel.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    target_inbox = str(inbox_id or CHATWOOT_INBOX_ID or "")
    if not target_inbox:
        return {"error": "Chatwoot inbox id required"}

    # 1) Search ───────────────────────────────────────────────
    needle = phone or email or name
    if needle:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(
                    f"{base}/contacts/search",
                    headers=_headers(),
                    params={"q": needle, "include_contact_inboxes": "true"},
                )
                resp.raise_for_status()
                rows = (resp.json() or {}).get("payload", []) or []
                for row in rows:
                    if phone and (row.get("phone_number") or "") == phone:
                        return _contact_with_source(row, target_inbox, created=False)
                    if email and (row.get("email") or "") == email:
                        return _contact_with_source(row, target_inbox, created=False)
        except httpx.HTTPStatusError as e:
            details = _error_body(e)
            logger.error("Chatwoot contact search error: %s %s",
                         e.response.status_code, details)
            # fall through to create — search failures shouldn't block
        except Exception:
            logger.exception("Chatwoot contact search failed (non-fatal)")

    # 2) Create ───────────────────────────────────────────────
    payload: dict[str, Any] = {
        "inbox_id": int(target_inbox),
    }
    if name:
        payload["name"] = name
    if phone:
        payload["phone_number"] = phone
    if email:
        payload["email"] = email

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(f"{base}/contacts", headers=_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json() or {}
            # The /contacts POST response wraps the row under "payload":
            # {"payload": {"contact": {...}, "contact_inbox": {"source_id": ...}}}
            inner = data.get("payload") or data
            contact = inner.get("contact") or inner
            ci = inner.get("contact_inbox") or {}
            return {
                "ok": True,
                "contact_id": contact.get("id"),
                "source_id": ci.get("source_id", "") or "",
                "created": True,
                "raw": data,
            }
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        logger.error("Chatwoot contact create error: %s %s",
                     e.response.status_code, details)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot contact create failed")
        return {"error": str(e)}


def _contact_with_source(row: dict, target_inbox: str, *, created: bool) -> dict:
    """Wrap a /contacts/search row + look up its source_id on the target inbox."""
    inboxes = row.get("contact_inboxes") or []
    src = ""
    for ci in inboxes:
        ib = ci.get("inbox") or {}
        if str(ib.get("id") or "") == target_inbox:
            src = ci.get("source_id", "") or ""
            break
    return {
        "ok": True,
        "contact_id": row.get("id"),
        "source_id": src,
        "created": created,
        "raw": row,
    }


def create_conversation(
    *,
    source_id: str,
    inbox_id: int | str | None = None,
    contact_id: int | None = None,
    initial_message: str = "",
) -> dict:
    """Create a new Chatwoot conversation for an existing contact_inbox.

    `source_id` is the channel-specific identifier (E.164 phone for
    Channel::Whatsapp, the UUID Chatwoot mints for Channel::Api). Together
    with `inbox_id` it picks the contact_inbox row.

    Returns {"ok": True, "conversation_id": int} on success.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    target_inbox = inbox_id or CHATWOOT_INBOX_ID
    if not target_inbox:
        return {"error": "Chatwoot inbox id required"}

    payload: dict[str, Any] = {
        "source_id": source_id,
        "inbox_id": int(target_inbox),
    }
    if contact_id is not None:
        payload["contact_id"] = int(contact_id)
    if initial_message:
        payload["message"] = {"content": initial_message}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{base}/conversations", headers=_headers(), json=payload,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            return {
                "ok": True,
                "conversation_id": data.get("id"),
                "raw": data,
            }
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        logger.error("Chatwoot conversation create error: %s %s",
                     e.response.status_code, details)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot conversation create failed")
        return {"error": str(e)}


def mark_as_read(conversation_id: int) -> dict:
    """Bump the agent's last-seen on the conversation (clears unread).

    Endpoint: POST /conversations/:id/update_last_seen (no body required).
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{base}/conversations/{conversation_id}/update_last_seen",
                headers=_headers(),
                json={},
            )
            resp.raise_for_status()
            return {"ok": True}
    except Exception as e:
        logger.debug("Chatwoot mark_as_read failed: %s", e)
        return {"error": str(e)}


def _find_open_conversation(
    contact_id: int,
    inbox_id: int | str | None = None,
) -> int | None:
    """Return the id of an open conversation for `contact_id` in the target
    inbox, or None if there isn't one.

    Used by the outbound router to decide between posting into an existing
    thread (the common case — operator and agent are mid-conversation) or
    opening a fresh one. Best-effort: any API failure returns None and the
    caller will fall back to creating a new conversation rather than
    blocking the send.
    """
    base = _api_base()
    if base is None:
        return None

    target_inbox = str(inbox_id or CHATWOOT_INBOX_ID or "")
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{base}/contacts/{contact_id}/conversations",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json() or {}
            rows = data.get("payload") or data or []
            for c in rows:
                if not isinstance(c, dict):
                    continue
                if c.get("status") != "open":
                    continue
                if target_inbox and str(c.get("inbox_id") or "") != target_inbox:
                    continue
                return c.get("id")
    except Exception:
        logger.exception("Chatwoot _find_open_conversation failed (non-fatal)")
    return None


def route_text_to_whatsapp(
    phone: str,
    body: str,
    *,
    source_id: str = "",
) -> dict:
    """Outbound text → Chatwoot, FA-edition replacement for direct Meta calls.

    Flow:
    1. find_or_create_contact(phone) on the configured inbox
    2. _find_open_conversation(contact_id) — reuse the active thread
    3a. If found → send_message(conv_id, body)
    3b. If not → create_conversation(...initial_message=body)

    For Channel::Whatsapp the contact's `source_id` IS the E.164 phone,
    so the caller can either pass it explicitly or let us derive it from
    the contact lookup.

    Returns `{"ok": True, "conversation_id": int, "message_id": int|None,
    "created_conversation": bool}` on success.
    """
    if not CHATWOOT_ENABLED:
        return {"error": "Chatwoot not enabled"}

    contact = find_or_create_contact(phone=phone)
    if "error" in contact:
        return contact

    contact_id = contact.get("contact_id")
    if not contact_id:
        return {"error": "Chatwoot contact lookup returned no id",
                "details": contact}

    src = source_id or contact.get("source_id") or phone
    conv_id = _find_open_conversation(contact_id)

    if conv_id is not None:
        result = send_message(conv_id, body)
        if "error" in result:
            return result
        return {
            "ok": True,
            "conversation_id": conv_id,
            "message_id": result.get("message_id"),
            "created_conversation": False,
        }

    created = create_conversation(
        source_id=src,
        contact_id=contact_id,
        initial_message=body,
    )
    if "error" in created:
        return created
    return {
        "ok": True,
        "conversation_id": created.get("conversation_id"),
        "message_id": None,
        "created_conversation": True,
    }


def send_template(
    conversation_id: int,
    template_name: str,
    *,
    processed_params: dict[str, str] | None = None,
    language: str = "en",
    category: str = "UTILITY",
    body: str = "",
) -> dict:
    """Post a WhatsApp template into an existing Chatwoot conversation.

    Mirrors the payload Chatwoot's own UI sends when an agent fires a
    template from the WhatsApp inbox composer:

        POST /api/v1/accounts/:aid/conversations/:cid/messages
        {
          "content": "<rendered body>",
          "template_params": {
            "name": "<template_name>",
            "category": "MARKETING|UTILITY|AUTHENTICATION",
            "language": "en",
            "processed_params": {"1": "John"}
          }
        }

    `content` is what Chatwoot displays in the timeline; passing the rendered
    body (with substitutions applied) keeps the UI readable. Returns
    {"ok": True, "message_id": int}.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    payload: dict[str, Any] = {
        "content": body,
        "message_type": "outgoing",
        "template_params": {
            "name": template_name,
            "category": category,
            "language": language,
            "processed_params": processed_params or {},
        },
    }
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(
                f"{base}/conversations/{conversation_id}/messages",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json() or {}
            logger.info("Chatwoot template '%s' sent conv=%s id=%s",
                        template_name, conversation_id, data.get("id"))
            return {"ok": True, "message_id": data.get("id"), "raw": data}
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        logger.error("Chatwoot template send error: %s %s",
                     e.response.status_code, details)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot template send failed")
        return {"error": str(e)}


def send_attachment(
    conversation_id: int,
    file_path: str | Path,
    *,
    caption: str = "",
) -> dict:
    """Upload a local file as a multipart attachment to a Chatwoot conversation.

    Endpoint is the same `POST /conversations/:cid/messages` as text/templates,
    but the request is multipart/form-data with `attachments[]` carrying the
    file bytes. Chatwoot then forwards the attachment to the underlying
    channel (WhatsApp / WeChat / Email) — for WhatsApp Cloud, Chatwoot does
    the upload-to-Meta dance internally.

    Returns {"ok": True, "message_id": int}.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    p = Path(file_path)
    if not p.is_file():
        return {"error": f"file not found: {p}"}

    mime_type, _ = mimetypes.guess_type(str(p))
    mime_type = mime_type or "application/octet-stream"

    # httpx fills in the multipart boundary itself — don't set Content-Type.
    auth_only = {"api_access_token": _headers().get("api_access_token", "")}
    try:
        with httpx.Client(timeout=120) as client, p.open("rb") as fh:
            resp = client.post(
                f"{base}/conversations/{conversation_id}/messages",
                headers=auth_only,
                data={"content": caption, "message_type": "outgoing"},
                files={"attachments[]": (p.name, fh, mime_type)},
            )
            resp.raise_for_status()
            data = resp.json() or {}
            logger.info("Chatwoot attachment sent conv=%s file=%s id=%s",
                        conversation_id, p.name, data.get("id"))
            return {"ok": True, "message_id": data.get("id"), "raw": data}
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        logger.error("Chatwoot attachment send error: %s %s",
                     e.response.status_code, details)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot attachment send failed")
        return {"error": str(e)}


def list_templates(inbox_id: int | str | None = None) -> dict:
    """List approved WhatsApp templates synced from WABA for an inbox.

    Chatwoot exposes templates as part of the inbox detail payload:
    `GET /api/v1/accounts/:aid/inboxes/:iid` returns a `message_templates`
    array on WhatsApp Cloud channels. Returns
    {"ok": True, "templates": [{"name": ..., "language": ..., ...}, ...]}.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    target_inbox = inbox_id or CHATWOOT_INBOX_ID
    if not target_inbox:
        return {"error": "Chatwoot inbox id required"}

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                f"{base}/inboxes/{target_inbox}",
                headers=_headers(),
            )
            resp.raise_for_status()
            data = resp.json() or {}
            templates = data.get("message_templates") or []
            return {"ok": True, "templates": templates}
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot list_templates failed")
        return {"error": str(e)}


def conversation_meta(assignee_type: str = "me") -> dict:
    """Counts of open / resolved / pending conversations on the inbox.

    Chatwoot exposes a single endpoint for this:
    `GET /api/v1/accounts/:aid/conversations/meta?assignee_type=<type>`.
    Returns {"ok": True, "open": int, "resolved": int, "pending": int,
    "all_count": int}. The morning brief uses this to surface backlog.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{base}/conversations/meta",
                headers=_headers(),
                params={"assignee_type": assignee_type},
            )
            resp.raise_for_status()
            data = resp.json() or {}
            meta = data.get("meta") or {}
            return {
                "ok": True,
                "open": int(meta.get("open", 0) or 0),
                "resolved": int(meta.get("resolved", 0) or 0),
                "pending": int(meta.get("pending", 0) or 0),
                "all_count": int(meta.get("all_count", 0) or 0),
            }
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot conversation_meta failed")
        return {"error": str(e)}


def list_open_conversations(limit: int = 5) -> dict:
    """Top-of-inbox open conversations, ordered by most recent activity.

    Chatwoot's `GET /api/v1/accounts/:aid/conversations?status=open` returns
    a paginated list under `data.payload`. We project just what the morning
    brief needs: id, contact name, and a short preview of the latest
    message. Returns {"ok": True, "conversations": [{id, contact, preview}]}.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{base}/conversations",
                headers=_headers(),
                params={"status": "open", "page": 1},
            )
            resp.raise_for_status()
            data = resp.json() or {}
            payload = ((data.get("data") or {}).get("payload")) or []
            conversations = []
            for row in payload[:limit]:
                meta_sender = (row.get("meta") or {}).get("sender") or {}
                contact = (
                    meta_sender.get("name")
                    or meta_sender.get("phone_number")
                    or meta_sender.get("email")
                    or "(no contact)"
                )
                msgs = row.get("messages") or []
                preview = ""
                if msgs:
                    preview = (msgs[-1].get("content") or "")[:120]
                conversations.append({
                    "id": row.get("id"),
                    "contact": contact,
                    "preview": preview,
                })
            return {"ok": True, "conversations": conversations}
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot list_open_conversations failed")
        return {"error": str(e)}


def route_template_to_whatsapp(
    phone: str,
    template_name: str,
    *,
    processed_params: dict[str, str] | None = None,
    language: str = "en",
    category: str = "UTILITY",
    body: str = "",
    source_id: str = "",
) -> dict:
    """First-touch outbound: send a template to a phone via Chatwoot.

    Flow mirrors `route_text_to_whatsapp` — find/create contact, reuse the
    open conversation if there is one, else open a fresh empty one. Then
    fire the template via `send_template`.

    Returns {"ok": True, "conversation_id": int, "message_id": int,
    "created_conversation": bool}.
    """
    if not CHATWOOT_ENABLED:
        return {"error": "Chatwoot not enabled"}

    contact = find_or_create_contact(phone=phone)
    if "error" in contact:
        return contact

    contact_id = contact.get("contact_id")
    if not contact_id:
        return {"error": "Chatwoot contact lookup returned no id",
                "details": contact}

    src = source_id or contact.get("source_id") or phone
    conv_id = _find_open_conversation(contact_id)
    created_conv = False

    if conv_id is None:
        created = create_conversation(source_id=src, contact_id=contact_id)
        if "error" in created:
            return created
        conv_id = created.get("conversation_id")
        created_conv = True

    sent = send_template(
        conv_id,
        template_name,
        processed_params=processed_params,
        language=language,
        category=category,
        body=body,
    )
    if "error" in sent:
        return sent
    return {
        "ok": True,
        "conversation_id": conv_id,
        "message_id": sent.get("message_id"),
        "created_conversation": created_conv,
    }


def route_media_to_whatsapp(
    phone: str,
    file_path: str | Path,
    *,
    caption: str = "",
    source_id: str = "",
) -> dict:
    """Outbound media (PDF/image/etc) → Chatwoot multipart upload.

    Like `route_text_to_whatsapp` but the body of the message is a file.
    Find/create contact, reuse open conv or open a new empty one, then
    upload via `send_attachment`. The first message in a brand-new
    conversation has to be content-only (Chatwoot's create endpoint
    doesn't accept multipart on the same call), so we always go via
    create-then-attach in the cold-start path.

    Returns {"ok": True, "conversation_id": int, "message_id": int,
    "created_conversation": bool}.
    """
    if not CHATWOOT_ENABLED:
        return {"error": "Chatwoot not enabled"}

    contact = find_or_create_contact(phone=phone)
    if "error" in contact:
        return contact

    contact_id = contact.get("contact_id")
    if not contact_id:
        return {"error": "Chatwoot contact lookup returned no id",
                "details": contact}

    src = source_id or contact.get("source_id") or phone
    conv_id = _find_open_conversation(contact_id)
    created_conv = False

    if conv_id is None:
        created = create_conversation(source_id=src, contact_id=contact_id)
        if "error" in created:
            return created
        conv_id = created.get("conversation_id")
        created_conv = True

    sent = send_attachment(conv_id, file_path, caption=caption)
    if "error" in sent:
        return sent
    return {
        "ok": True,
        "conversation_id": conv_id,
        "message_id": sent.get("message_id"),
        "created_conversation": created_conv,
    }


def mark_as_resolved(conversation_id: int) -> dict:
    """Toggle conversation status to resolved.

    Endpoint: POST /conversations/:id/toggle_status with {"status": "resolved"}.
    """
    base = _api_base()
    if base is None:
        return {"error": "Chatwoot not configured"}

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(
                f"{base}/conversations/{conversation_id}/toggle_status",
                headers=_headers(),
                json={"status": "resolved"},
            )
            resp.raise_for_status()
            return {"ok": True, "raw": resp.json() if resp.content else {}}
    except httpx.HTTPStatusError as e:
        details = _error_body(e)
        return {"error": f"Chatwoot API {e.response.status_code}", "details": details}
    except Exception as e:
        logger.exception("Chatwoot mark_as_resolved failed")
        return {"error": str(e)}


def _error_body(e: httpx.HTTPStatusError) -> Any:
    """Best-effort body decode for an httpx error response."""
    try:
        return e.response.json()
    except (json.JSONDecodeError, ValueError):
        return e.response.text[:500] if e.response.content else ""
