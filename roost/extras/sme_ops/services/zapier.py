"""Zapier bridge — inbound event recording + outbound webhook posting.

Inbound: HTTP webhook handler (in `roost/web/api_zapier.py`) calls
`record_event()` to persist + fire SOP trigger.

Outbound: `ZapierBridge.send()` POSTs to a configured Zapier "Catch Hook"
URL so Roost-side state changes can fan out to any Zapier-connected app.

Both sides fail closed when their flag is off — feature is opt-in.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from roost.config import (
    ZAPIER_ENABLED,
    ZAPIER_OUTBOUND_URL,
)
from roost.database import get_connection

logger = logging.getLogger("roost.extras.sme_ops.services.zapier")


def record_event(source: str, event: str, payload: dict[str, Any]) -> int:
    """Persist an inbound event and return its row id."""
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO sme_ops_events (source, event, payload_json) "
            "VALUES (?, ?, ?)",
            (source, event, json.dumps(payload, default=str)),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def mark_processed(event_id: int, status: str = "processed") -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE sme_ops_events SET status = ?, "
            "processed_at = datetime('now') WHERE id = ?",
            (status, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def recent_stats(source: str = "zapier", limit: int = 50) -> dict:
    """Adapter health snapshot — last received + counts by status today."""
    conn = get_connection()
    try:
        last = conn.execute(
            "SELECT received_at, event FROM sme_ops_events "
            "WHERE source = ? ORDER BY id DESC LIMIT 1",
            (source,),
        ).fetchone()
        counts = {}
        for row in conn.execute(
            "SELECT status, COUNT(*) AS n FROM sme_ops_events "
            "WHERE source = ? AND received_at >= date('now') "
            "GROUP BY status",
            (source,),
        ):
            counts[row["status"]] = row["n"]
        return {
            "source": source,
            "last_received_at": last["received_at"] if last else None,
            "last_event": last["event"] if last else None,
            "today_counts": counts,
        }
    finally:
        conn.close()


class ZapierBridge:
    """Outbound side — POST events to a Zapier Catch Hook URL.

    Mirrors the class-based shape of `roost/services/crm/attio.py`. The class
    captures config at init time so callers can rely on a stable instance even
    if env changes during a process's lifetime.
    """

    def __init__(self, url: str = "", *, enabled: bool | None = None):
        self.url = (url or ZAPIER_OUTBOUND_URL).strip()
        self.enabled = ZAPIER_ENABLED if enabled is None else enabled

    def is_configured(self) -> bool:
        return bool(self.enabled and self.url)

    def send(self, event: str, payload: dict[str, Any]) -> dict:
        """Fire-and-forget-ish POST. Returns `{ok, status, ...}`.

        Fails closed: returns `{"ok": False, "error": ...}` on any error
        rather than raising — outbound notifications must never break the
        caller's primary flow.
        """
        if not self.is_configured():
            return {"ok": False, "error": "zapier_outbound_disabled"}
        body = {"event": event, "payload": payload}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.url, json=body)
            return {"ok": 200 <= resp.status_code < 300, "status": resp.status_code}
        except Exception as e:
            logger.warning("Zapier outbound failed for event %s: %s", event, e)
            return {"ok": False, "error": str(e)}
