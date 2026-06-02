"""End-of-day summary builder.

Pulls activity from the five surfaces the user cares about:
  1. Nurture — what advanced today (sent / held / completed / exited).
  2. Tasks — completed today + still-open count.
  3. Inbound leads — new enrollments today by source/channel.
  4. Recipe runs — automation_runs grouped by status (with failures listed).
  5. RPA runs — rpa_runs grouped by status (failures include error snippet).

`build_summary` returns structured data so callers can render it however
they want; `format_summary` produces a Telegram-ready Markdown string.

Time window defaults to "since local-tz midnight today, in UTC". Caller
can override either bound for catch-up sends.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Any

from roost.database import get_connection

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _local_midnight_utc(tz_name: str) -> str:
    """UTC ISO string for 'today's local midnight' in the given tz."""
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    now_local = datetime.now(tz)
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _row_get(row, key: str, default=""):
    """sqlite3.Row access that tolerates missing columns."""
    try:
        return row[key]
    except (KeyError, IndexError):
        return default


def build_summary(
    *,
    user_id: str = "",
    since_utc: str | None = None,
    until_utc: str | None = None,
    tz_name: str = "Asia/Singapore",
) -> dict[str, Any]:
    """Compile a structured summary of activity in the window.

    Window defaults to [today's local midnight, now]. Times are stored as
    "YYYY-MM-DD HH:MM:SS" UTC strings (sqlite's default datetime format),
    matching every existing table's `created_at` / `updated_at` columns.
    """
    if not since_utc:
        since_utc = _local_midnight_utc(tz_name)
    if not until_utc:
        until_utc = _utc_now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        return {
            "since_utc": since_utc,
            "until_utc": until_utc,
            "tz": tz_name,
            "nurture": _nurture_section(conn, user_id, since_utc, until_utc),
            "tasks": _tasks_section(conn, since_utc, until_utc),
            "inbound_leads": _leads_section(conn, user_id, since_utc, until_utc),
            "recipes": _recipes_section(conn, since_utc, until_utc),
            "rpa": _rpa_section(conn, user_id, since_utc, until_utc),
            "chatwoot": _chatwoot_section(),
        }
    finally:
        conn.close()


# ── Section builders ─────────────────────────────────────────────────


def _nurture_section(conn, user_id, since, until) -> dict:
    user_clause = " AND user_id = ?" if user_id else ""
    params: tuple = (since, until) + ((user_id,) if user_id else ())

    # What advanced in-window: enrollments whose last_step_at moved.
    by_status = {}
    for row in conn.execute(
        f"SELECT status, COUNT(*) AS n FROM nurture_enrollments "
        f"WHERE last_step_at >= ? AND last_step_at <= ?{user_clause} "
        f"GROUP BY status", params,
    ):
        by_status[row["status"]] = row["n"]

    # Pending approval right now (regardless of window — these need action).
    pending = []
    for row in conn.execute(
        f"SELECT id, cadence_slug, contact_name, contact_email, contact_phone, "
        f"current_step FROM nurture_enrollments "
        f"WHERE status = 'paused' AND pause_reason LIKE 'awaiting_approval%'"
        f"{user_clause} ORDER BY id LIMIT 20",
        (user_id,) if user_id else (),
    ):
        contact = (
            row["contact_name"] or row["contact_email"]
            or row["contact_phone"] or "(no contact)"
        )
        pending.append({
            "id": row["id"], "cadence": row["cadence_slug"],
            "contact": contact, "step": row["current_step"] + 1,
        })
    return {"advanced_by_status": by_status, "pending_approvals": pending}


def _tasks_section(conn, since, until) -> dict:
    completed = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE status = 'done' "
        "AND updated_at >= ? AND updated_at <= ?",
        (since, until),
    ).fetchone()["n"]
    open_n = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('todo', 'in_progress')"
    ).fetchone()["n"]
    overdue = conn.execute(
        "SELECT COUNT(*) AS n FROM tasks WHERE status IN ('todo', 'in_progress') "
        "AND deadline IS NOT NULL AND deadline != '' AND deadline < ?",
        (until[:10],),
    ).fetchone()["n"]
    return {"completed": completed, "open": open_n, "overdue": overdue}


def _leads_section(conn, user_id, since, until) -> dict:
    user_clause = " AND user_id = ?" if user_id else ""
    params: tuple = (since, until) + ((user_id,) if user_id else ())

    by_source = {}
    for row in conn.execute(
        f"SELECT COALESCE(NULLIF(source, ''), '(unknown)') AS src, "
        f"COUNT(*) AS n FROM nurture_enrollments "
        f"WHERE created_at >= ? AND created_at <= ?{user_clause} "
        f"GROUP BY src ORDER BY n DESC", params,
    ):
        by_source[row["src"]] = row["n"]
    total = sum(by_source.values())
    return {"total_new": total, "by_source": by_source}


def _recipes_section(conn, since, until) -> dict:
    by_status = {}
    for row in conn.execute(
        "SELECT status, COUNT(*) AS n FROM automation_runs "
        "WHERE started_at >= ? AND started_at <= ? GROUP BY status",
        (since, until),
    ):
        by_status[row["status"]] = row["n"]

    failures = []
    for row in conn.execute(
        "SELECT r.id, r.started_at, COALESCE(a.name, '(unknown)') AS recipe_name "
        "FROM automation_runs r "
        "LEFT JOIN automation_recipes a ON a.id = r.recipe_id "
        "WHERE r.status = 'failed' AND r.started_at >= ? AND r.started_at <= ? "
        "ORDER BY r.started_at DESC LIMIT 5",
        (since, until),
    ):
        failures.append({
            "id": row["id"],
            "recipe": row["recipe_name"],
            "at": row["started_at"],
        })
    return {"by_status": by_status, "failures": failures}


def _rpa_section(conn, user_id, since, until) -> dict:
    user_clause = " AND user_id = ?" if user_id else ""
    params: tuple = (since, until) + ((user_id,) if user_id else ())

    by_status = {}
    for row in conn.execute(
        f"SELECT status, COUNT(*) AS n FROM rpa_runs "
        f"WHERE updated_at >= ? AND updated_at <= ?{user_clause} "
        f"GROUP BY status", params,
    ):
        by_status[row["status"]] = row["n"]

    # Failed flows — show portal + error snippet.
    failed = []
    for row in conn.execute(
        f"SELECT id, portal_slug, error FROM rpa_runs "
        f"WHERE status = 'failed' AND updated_at >= ? AND updated_at <= ?"
        f"{user_clause} ORDER BY updated_at DESC LIMIT 5", params,
    ):
        failed.append({
            "id": row["id"], "portal": row["portal_slug"],
            "error": (row["error"] or "")[:120],
        })

    # Successful flows — show portal + count by portal.
    succeeded = {}
    for row in conn.execute(
        f"SELECT portal_slug, COUNT(*) AS n FROM rpa_runs "
        f"WHERE status = 'completed' AND updated_at >= ? AND updated_at <= ?"
        f"{user_clause} GROUP BY portal_slug", params,
    ):
        succeeded[row["portal_slug"]] = row["n"]
    return {
        "by_status": by_status, "succeeded_by_portal": succeeded,
        "failures": failed,
    }


def _chatwoot_section() -> dict:
    """Read-side rollup from the Chatwoot inbox itself (REST, not sqlite).

    Returns counts of open/pending conversations plus a preview of the
    top open threads. Fails closed: if Chatwoot is unreachable or the
    flag is off, returns an empty section and the morning brief omits it
    rather than blocking. The two helpers
    (`conversation_meta`, `list_open_conversations`) already swallow
    exceptions internally and return `{error: ...}` shapes, so this
    function just needs to recognise the error envelope.
    """
    from roost.config import CHATWOOT_ENABLED
    if not CHATWOOT_ENABLED:
        return {"enabled": False}

    try:
        from roost.extras.messaging_external.services import chatwoot
    except Exception:
        return {"enabled": True, "error": "chatwoot service unavailable"}

    meta = chatwoot.conversation_meta(assignee_type="me")
    if "error" in meta:
        return {"enabled": True, "error": meta["error"]}

    top = chatwoot.list_open_conversations(limit=5)
    conversations = top.get("conversations") or [] if "error" not in top else []

    return {
        "enabled": True,
        "open": meta.get("open", 0),
        "pending": meta.get("pending", 0),
        "top_open": conversations,
    }


# ── Markdown formatter ───────────────────────────────────────────────


_AI_PROMPT = """You are summarising a property agent's day from structured activity data.
Write 2–3 short bullets covering: what shipped, what needs the agent's attention now,
and one anomaly or pattern if visible (e.g. all RPA failures on one portal, lead spike).
Use only the numbers shown — do not invent figures. Plain text bullets starting with "•".
No preamble, no headings, under 80 words total.

Data:
{json}
"""


def _ai_narrative(s: dict) -> str | None:
    """Generate a short AI narrative from the summary dict. Fails closed."""
    from roost.config import AI_SUMMARY_ENABLED, GEMINI_API_KEY, GEMINI_MODEL
    if not AI_SUMMARY_ENABLED or not GEMINI_API_KEY:
        return None
    # Skip empty days — nothing to narrate.
    if not any((
        s["nurture"]["advanced_by_status"], s["nurture"]["pending_approvals"],
        s["tasks"]["completed"], s["tasks"]["overdue"],
        s["inbound_leads"]["total_new"],
        s["recipes"]["by_status"], s["rpa"]["by_status"],
    )):
        return None
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)
        payload = {k: s[k] for k in
                   ("nurture", "tasks", "inbound_leads", "recipes", "rpa")}
        prompt = _AI_PROMPT.format(json=json.dumps(payload, default=str))
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0.3, max_output_tokens=256,
            ),
        )
        text = (response.text or "").strip()
        return text or None
    except Exception:
        logger.exception("daily summary AI narrative failed; falling through")
        return None


def format_summary(s: dict) -> str:
    """Render `build_summary()` output as a Telegram-friendly Markdown block."""
    window_local = _utc_to_local_short(s["since_utc"], s["tz"])
    lines = [f"📊 *Daily summary* — since {window_local} ({s['tz']})", ""]

    narrative = _ai_narrative(s)
    if narrative:
        lines.append(narrative)
        lines.append("")

    # Nurture
    n = s["nurture"]
    if n["advanced_by_status"] or n["pending_approvals"]:
        lines.append("*Nurture*")
        if n["advanced_by_status"]:
            parts = [f"{v} {k}" for k, v in n["advanced_by_status"].items()]
            lines.append("  Advanced: " + ", ".join(parts))
        if n["pending_approvals"]:
            lines.append(f"  ⚠ {len(n['pending_approvals'])} awaiting approval:")
            for p in n["pending_approvals"][:5]:
                lines.append(
                    f"    • #{p['id']} {p['cadence']} step {p['step']} → {p['contact']}"
                )
            if len(n["pending_approvals"]) > 5:
                lines.append(f"    …and {len(n['pending_approvals']) - 5} more")
        lines.append("")

    # Tasks
    t = s["tasks"]
    if t["completed"] or t["open"]:
        lines.append("*Tasks*")
        lines.append(
            f"  ✅ {t['completed']} completed today  •  "
            f"{t['open']} open" + (f"  •  ⚠ {t['overdue']} overdue"
                                    if t["overdue"] else "")
        )
        lines.append("")

    # Inbound leads
    l = s["inbound_leads"]
    if l["total_new"]:
        lines.append("*Inbound leads*")
        lines.append(f"  {l['total_new']} new today")
        for src, n_ in list(l["by_source"].items())[:5]:
            lines.append(f"    • {src}: {n_}")
        lines.append("")

    # Recipes
    r = s["recipes"]
    if r["by_status"]:
        lines.append("*Recipes*")
        parts = [f"{v} {k}" for k, v in r["by_status"].items()]
        lines.append("  " + ", ".join(parts))
        for f in r["failures"]:
            lines.append(f"  ✗ #{f['id']} {f['recipe']} @ {f['at'][11:16]}")
        lines.append("")

    # Chatwoot inbox (FA edition)
    cw = s.get("chatwoot") or {}
    if cw.get("enabled") and "error" not in cw:
        if cw.get("open") or cw.get("pending") or cw.get("top_open"):
            lines.append("*Chatwoot*")
            counts: list[str] = []
            if cw.get("open"):
                counts.append(f"📥 {cw['open']} open")
            if cw.get("pending"):
                counts.append(f"⏳ {cw['pending']} pending")
            if counts:
                lines.append("  " + "  •  ".join(counts))
            top = cw.get("top_open") or []
            if top:
                lines.append("  Top open:")
                for c in top:
                    preview = (c.get("preview") or "").replace("\n", " ").strip()
                    if preview:
                        lines.append(f"    • #{c['id']} {c['contact']}: \"{preview}\"")
                    else:
                        lines.append(f"    • #{c['id']} {c['contact']}")
            lines.append("")
    elif cw.get("enabled") and cw.get("error"):
        lines.append("*Chatwoot*")
        lines.append(f"  ⚠ inbox unreachable: {cw['error']}")
        lines.append("")

    # RPA
    rpa = s["rpa"]
    if rpa["by_status"]:
        lines.append("*RPA*")
        parts = [f"{v} {k}" for k, v in rpa["by_status"].items()]
        lines.append("  " + ", ".join(parts))
        if rpa["succeeded_by_portal"]:
            ok_parts = [f"{p}×{n_}" for p, n_ in rpa["succeeded_by_portal"].items()]
            lines.append("  ✅ " + ", ".join(ok_parts))
        for f in rpa["failures"]:
            err = f["error"] or "(no detail)"
            lines.append(f"  ✗ #{f['id']} {f['portal']}: {err}")
        lines.append("")

    if len(lines) <= 2:
        lines.append("_No activity in the window._")

    return "\n".join(lines).rstrip()


def _utc_to_local_short(utc_str: str, tz_name: str) -> str:
    try:
        tz = ZoneInfo(tz_name)
        dt = datetime.strptime(utc_str[:19], "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc).astimezone(tz)
        return dt.strftime("%a %H:%M")
    except Exception:
        return utc_str[:16]
