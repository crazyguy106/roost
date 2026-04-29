"""Proactive monitoring — push alerts for risky behaviours and important events.

Two categories:
1. Risk monitoring: watches agent behaviour patterns over time
2. Event preparation: upcoming meetings, inbox priority alerts

Enable via PROACTIVE_ENABLED=true in .env.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from roost.database import db_connection

logger = logging.getLogger(__name__)


# ── Risk monitoring ──────────────────────────────────────────────────

def check_risk_patterns(user_id: str = "") -> list[dict]:
    """Check for risky agent behaviour patterns. Returns list of alerts."""
    alerts = []

    alerts.extend(_check_guardian_blocks(user_id))
    alerts.extend(_check_tool_burst(user_id))
    alerts.extend(_check_cost_spike(user_id))
    alerts.extend(_check_failed_tools(user_id))

    return alerts


def _check_guardian_blocks(user_id: str) -> list[dict]:
    """Alert if Guardian has blocked multiple actions recently."""
    try:
        with db_connection() as conn:
            count = conn.execute(
                """SELECT COUNT(*) as cnt FROM guardian_log
                   WHERE decision = 'block'
                   AND created_at > datetime('now', '-1 hour')""",
            ).fetchone()["cnt"]

            if count >= 3:
                return [{
                    "type": "risk",
                    "severity": "high",
                    "title": "Multiple actions blocked",
                    "message": f"Guardian blocked {count} actions in the last hour. "
                               "Your agent may be attempting unsafe operations.",
                }]
    except Exception:
        pass
    return []


def _check_tool_burst(user_id: str) -> list[dict]:
    """Alert on unusually high tool call volume (possible loop)."""
    try:
        with db_connection() as conn:
            count = conn.execute(
                """SELECT COUNT(*) as cnt FROM guardian_log
                   WHERE created_at > datetime('now', '-5 minutes')""",
            ).fetchone()["cnt"]

            if count >= 30:
                return [{
                    "type": "risk",
                    "severity": "high",
                    "title": "Tool call burst detected",
                    "message": f"{count} tool calls in 5 minutes. "
                               "Possible agent loop — check active sessions.",
                }]
    except Exception:
        pass
    return []


def _check_cost_spike(user_id: str) -> list[dict]:
    """Alert if today's cost is approaching the daily limit."""
    try:
        from roost.config import MAX_DAILY_COST
        from roost.services.cost_tracking import get_today_cost

        today = get_today_cost(user_id)
        if MAX_DAILY_COST > 0 and today >= MAX_DAILY_COST * 0.8:
            pct = int((today / MAX_DAILY_COST) * 100)
            return [{
                "type": "cost",
                "severity": "medium",
                "title": "Daily cost limit approaching",
                "message": f"Today's AI cost: ${today:.2f} / ${MAX_DAILY_COST:.2f} "
                           f"({pct}%). Consider reducing agent usage.",
            }]
    except Exception:
        pass
    return []


def _check_failed_tools(user_id: str) -> list[dict]:
    """Alert on repeated tool failures (possible prompt injection)."""
    try:
        with db_connection() as conn:
            # Check command_log for recent errors
            count = conn.execute(
                """SELECT COUNT(*) as cnt FROM command_log
                   WHERE output LIKE '%error%'
                   AND created_at > datetime('now', '-30 minutes')""",
            ).fetchone()["cnt"]

            if count >= 5:
                return [{
                    "type": "risk",
                    "severity": "medium",
                    "title": "Repeated tool failures",
                    "message": f"{count} tool errors in 30 minutes. "
                               "Check agent logs for potential prompt injection.",
                }]
    except Exception:
        pass
    return []


# ── Event preparation ────────────────────────────────────────────────

def check_upcoming_meetings(minutes_ahead: int = 30) -> list[dict]:
    """Check for meetings in the next N minutes and prepare context."""
    alerts = []

    try:
        from roost.calendar_service import get_today_events
        events = get_today_events()

        now = datetime.now()
        window = now + timedelta(minutes=minutes_ahead)

        for event in events:
            start_str = event.get("start", "")
            if not start_str:
                continue

            try:
                # Parse ISO format
                if "T" in start_str:
                    start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    start = start.replace(tzinfo=None)  # Compare naive
                else:
                    continue  # All-day event, skip

                if now <= start <= window:
                    alert = {
                        "type": "calendar_prep",
                        "severity": "info",
                        "title": f"Meeting in {int((start - now).total_seconds() / 60)} min",
                        "message": f"'{event.get('summary', 'Untitled')}' "
                                   f"at {start.strftime('%H:%M')}",
                        "event": event,
                    }

                    # Try to get participant context
                    attendees = event.get("attendees", [])
                    if attendees:
                        alert["participants"] = [
                            a.get("email", a.get("displayName", ""))
                            for a in attendees[:5]
                        ]

                    alerts.append(alert)
            except (ValueError, TypeError):
                continue

    except Exception:
        logger.debug("Calendar prep check failed", exc_info=True)

    return alerts


def check_inbox_priority(user_id: str = "") -> list[dict]:
    """Check for high-priority unread emails."""
    alerts = []

    try:
        from roost.config import GMAIL_ENABLED
        if not GMAIL_ENABLED:
            return []

        from roost.gmail.client import GmailClient
        client = GmailClient()
        # Search for important unread emails
        messages = client.search("is:unread is:important", max_results=5)

        if messages and len(messages) >= 3:
            alerts.append({
                "type": "inbox",
                "severity": "info",
                "title": f"{len(messages)} important unread emails",
                "message": "You have important unread emails that may need attention.",
                "count": len(messages),
            })
    except Exception:
        logger.debug("Inbox priority check failed", exc_info=True)

    return alerts


# ── Aggregated check ─────────────────────────────────────────────────

def run_proactive_checks(user_id: str = "") -> list[dict]:
    """Run all proactive checks and return combined alerts."""
    all_alerts = []

    # Risk monitoring
    all_alerts.extend(check_risk_patterns(user_id))

    # Event preparation
    all_alerts.extend(check_upcoming_meetings(minutes_ahead=30))

    # Inbox priority (less frequent — only when called)
    all_alerts.extend(check_inbox_priority(user_id))

    # Sort by severity
    severity_order = {"high": 0, "medium": 1, "info": 2}
    all_alerts.sort(key=lambda a: severity_order.get(a.get("severity", "info"), 3))

    return all_alerts
