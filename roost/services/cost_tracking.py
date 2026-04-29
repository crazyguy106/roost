"""Cost tracking — per-run and daily token usage limits.

Tracks input/output tokens and estimated cost for each agent run.
Enforces MAX_COST_PER_RUN and MAX_DAILY_COST limits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from roost.database import db_connection

logger = logging.getLogger(__name__)


# ── Cost estimates per 1M tokens (USD) ───────────────────────────────
# Updated April 2026. These are approximate.

_COST_PER_1M = {
    "gemini-3-flash-preview":  {"input": 0.10, "output": 0.40},
    "gemini-2.5-flash":        {"input": 0.15, "output": 0.60},
    "gemini-2.5-pro":          {"input": 1.25, "output": 5.00},
    "claude-sonnet-4-20250514": {"input": 3.00, "output": 15.00},
    "claude-opus-4-6":         {"input": 15.00, "output": 75.00},
    "gpt-4o":                  {"input": 2.50, "output": 10.00},
    "gpt-4o-mini":             {"input": 0.15, "output": 0.60},
    # Local models are free
    "ollama":                  {"input": 0.0, "output": 0.0},
}

# Default for unknown models
_DEFAULT_COST = {"input": 0.50, "output": 2.00}


# Schema defined in database.py (SCHEMA_V24) to avoid circular imports.


# ── Run tracking ─────────────────────────────────────────────────────

class RunTracker:
    """Tracks token usage for a single agent run."""

    def __init__(self, model: str = "", user_id: str = ""):
        self.run_id = str(uuid.uuid4())[:8]
        self.model = model
        self.user_id = user_id
        self.total_input = 0
        self.total_output = 0
        self.total_cost = 0.0
        self.tool_calls = 0

    def record(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        """Record tokens from a single LLM call within this run."""
        self.total_input += input_tokens
        self.total_output += output_tokens

        # Estimate cost
        model_key = self.model
        costs = _COST_PER_1M.get(model_key, _DEFAULT_COST)
        call_cost = (
            (input_tokens / 1_000_000) * costs["input"] +
            (output_tokens / 1_000_000) * costs["output"]
        )
        self.total_cost += call_cost

    def record_tool_call(self) -> None:
        """Increment tool call counter."""
        self.tool_calls += 1

    def save(self) -> None:
        """Persist run totals to database."""
        try:
            with db_connection() as conn:
                conn.execute(
                    """INSERT INTO token_usage
                       (run_id, provider, model, input_tokens, output_tokens,
                        estimated_cost_usd, tool_calls, user_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (self.run_id, "", self.model,
                     self.total_input, self.total_output,
                     round(self.total_cost, 6), self.tool_calls,
                     self.user_id),
                )
                conn.commit()
        except Exception:
            logger.debug("Failed to save token usage", exc_info=True)


# ── Limit checking ───────────────────────────────────────────────────

def check_run_limit(tracker: RunTracker, max_cost: float) -> dict | None:
    """Check if current run has exceeded cost limit.

    Returns None if OK, or a dict with exceeded info.
    """
    if max_cost <= 0:
        return None

    if tracker.total_cost >= max_cost:
        return {
            "exceeded": True,
            "type": "per_run",
            "limit": max_cost,
            "current": round(tracker.total_cost, 4),
            "message": f"Run cost limit reached (${tracker.total_cost:.4f} / ${max_cost:.2f}). "
                       "Stopping to prevent overspend.",
        }
    return None


def check_daily_limit(max_daily: float, user_id: str = "") -> dict | None:
    """Check if today's total cost has exceeded the daily limit.

    Returns None if OK, or a dict with exceeded info.
    """
    if max_daily <= 0:
        return None

    today_cost = get_today_cost(user_id)
    if today_cost >= max_daily:
        return {
            "exceeded": True,
            "type": "daily",
            "limit": max_daily,
            "current": round(today_cost, 4),
            "message": f"Daily cost limit reached (${today_cost:.4f} / ${max_daily:.2f}). "
                       "Try again tomorrow or increase MAX_DAILY_COST.",
        }
    return None


# ── Query functions ──────────────────────────────────────────────────

def get_today_cost(user_id: str = "") -> float:
    """Get total estimated cost for today."""
    try:
        with db_connection() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(estimated_cost_usd), 0) as total
                   FROM token_usage
                   WHERE date(created_at) = date('now')""",
            ).fetchone()
            return row["total"]
    except Exception:
        return 0.0


def get_today_tokens(user_id: str = "") -> dict:
    """Get today's token totals."""
    try:
        with db_connection() as conn:
            row = conn.execute(
                """SELECT
                       COALESCE(SUM(input_tokens), 0) as input_total,
                       COALESCE(SUM(output_tokens), 0) as output_total,
                       COALESCE(SUM(estimated_cost_usd), 0) as cost_total,
                       COALESCE(SUM(tool_calls), 0) as tools_total,
                       COUNT(*) as run_count
                   FROM token_usage
                   WHERE date(created_at) = date('now')""",
            ).fetchone()
            return dict(row)
    except Exception:
        return {"input_total": 0, "output_total": 0, "cost_total": 0.0,
                "tools_total": 0, "run_count": 0}


def get_usage_history(days: int = 7, user_id: str = "") -> list[dict]:
    """Get daily usage for the last N days."""
    try:
        with db_connection() as conn:
            rows = conn.execute(
                """SELECT
                       date(created_at) as day,
                       SUM(input_tokens) as input_total,
                       SUM(output_tokens) as output_total,
                       SUM(estimated_cost_usd) as cost_total,
                       SUM(tool_calls) as tools_total,
                       COUNT(*) as run_count
                   FROM token_usage
                   WHERE created_at > datetime('now', ?)
                   GROUP BY date(created_at)
                   ORDER BY day DESC""",
                (f"-{days} days",),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []
