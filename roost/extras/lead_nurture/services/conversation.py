"""Per-contact conversation thread for the /leads inbox.

Stores every inbound/outbound message in `lead_messages` so the operator
can read the full back-and-forth on the lead detail view and reply by
hand. Logging is best-effort — a failure to record a message must never
block the message itself (an inbound webhook or an outbound send).

A thread is keyed two ways so it survives the gap between "message
arrived" (we know channel + identifier) and "enrollment created" (we know
the enrollment id): rows carry both, and `get_thread` matches on either.
"""

from __future__ import annotations

import logging

from roost.database import get_connection

logger = logging.getLogger("roost.lead_nurture.conversation")


def log_message(
    *,
    channel: str,
    identifier: str,
    direction: str,
    body: str,
    enrollment_id: int | None = None,
    sender_name: str = "",
) -> None:
    """Record one message in the thread. Best-effort: never raises.

    `direction` is 'in' (from the lead) or 'out' (from us)."""
    if direction not in ("in", "out"):
        logger.warning("log_message: bad direction %r — skipping", direction)
        return
    try:
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO lead_messages
                   (enrollment_id, channel, identifier, direction, body, sender_name)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    enrollment_id,
                    channel or "",
                    str(identifier or ""),
                    direction,
                    body or "",
                    sender_name or "",
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 — logging must never break the message path
        logger.exception("log_message failed (non-fatal)")


def get_thread(
    *,
    enrollment_id: int | None = None,
    channel: str = "",
    identifier: str = "",
    limit: int = 200,
) -> list[dict]:
    """Return the message thread oldest-first.

    Matches rows by enrollment_id OR (channel, identifier) so messages
    logged before the enrollment existed still surface."""
    clauses: list[str] = []
    params: list = []
    if enrollment_id is not None:
        clauses.append("enrollment_id = ?")
        params.append(enrollment_id)
    if channel and identifier:
        clauses.append("(channel = ? AND identifier = ?)")
        params.extend([channel, str(identifier)])
    if not clauses:
        return []
    where = " OR ".join(clauses)
    sql = (
        f"SELECT id, enrollment_id, channel, identifier, direction, body, "
        f"sender_name, created_at FROM lead_messages WHERE {where} "
        f"ORDER BY id ASC LIMIT ?"
    )
    params.append(limit)
    conn = get_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def send_reply(*, enrollment_id: int, text: str) -> dict:
    """Send an operator's hand-typed reply to the lead on their channel,
    logging it to the thread on success.

    Resolves channel + identifier from the enrollment's qualification
    state, falling back to the enrollment's own channel/contact columns.
    Returns `{"ok": True}` or `{"error": ...}`."""
    text = (text or "").strip()
    if not text:
        return {"error": "empty message"}

    from roost.extras.lead_nurture.services.cadences.store import get_enrollment

    enr = get_enrollment(enrollment_id)
    if not enr:
        return {"error": "enrollment not found"}

    fields = enr.get("fields") or {}
    channel = fields.get("_qualify_channel") or enr.get("channel") or ""
    identifier = fields.get("_qualify_identifier") or ""
    if not identifier:
        if channel == "telegram":
            identifier = enr.get("contact_telegram_chat_id") or ""
        else:
            identifier = enr.get("contact_phone") or ""

    if not identifier or channel not in ("whatsapp", "wechat", "telegram", "chatwoot"):
        return {"error": f"no addressable channel for enrollment {enrollment_id}"}

    from roost.extras.lead_nurture.services.qualification import _send_question

    # _send_question records the outbound message in the thread on success,
    # so we don't log it again here.
    result = _send_question(channel, str(identifier), text)
    if "error" in result:
        return {"error": result["error"]}
    return {"ok": True, "channel": channel, "identifier": str(identifier)}
