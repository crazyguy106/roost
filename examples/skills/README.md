# Skill Examples Gallery

Starter skills for Roost. **These are reference code**, not installed skills
— Roost's skill loader (`skills/*.py`) never scans this directory, so you
can safely read and copy without triggering execution.

## How to use these

1. **Read** the example that's closest to what you want.
2. **Copy** the file into the repo's live skills directory:
   ```bash
   cp examples/skills/email_summary.py skills/
   ```
3. **Restart** the bot (or `/skill reload` if running) — the skill becomes
   available under its `trigger` keyword.

Or, for the curriculum path (recommended): use `/skill <description>` in
Telegram to have the AI generate a fresh skill for you, using these
examples as a mental model of what "a skill" looks like.

## What each example shows

| File | What it teaches | Uses | Tier |
|---|---|---|---|
| [`currency_convert.py`](currency_convert.py) | Simplest possible skill — pure logic, no external calls | Stdlib only | `read_only` |
| [`email_summary.py`](email_summary.py) | Read-only Gmail access, summarize via AI | `gmail_helpers.search_messages`, `gemini_generate.fn` | `read_only` |
| [`daily_briefing.py`](daily_briefing.py) | Multi-source roll-up for a morning briefing | `calendar_service.get_today_events`, `services.tasks.list_tasks` | `read_only` |
| [`weekly_report.py`](weekly_report.py) | Draft-first report for human review | `services.tasks.list_tasks`, `gemini_generate.fn` | `external_write` |
| [`whatsapp_lead_triage.py`](whatsapp_lead_triage.py) | Wires a skill into the AI CDR pipeline + recipes | `ai_cdr.classify_message`, `response_templates.list_templates` | `external_write` |

### Note on FastMCP-decorated imports

Several Roost tools live under `roost.mcp.tools_*` and are decorated with
`@mcp.tool()`. Those decorators replace the function with a `FunctionTool`
wrapper that **isn't directly callable**. To call them from a skill, grab
the raw function via `.fn`:

```python
from roost.mcp.tools_gemini import gemini_generate as _tool
gemini_generate = getattr(_tool, "fn", _tool)  # handle both forms
```

The `email_summary` and `weekly_report` examples both demonstrate this
pattern. Plain (non-decorated) helpers like `roost.mcp.gmail_helpers.search_messages`
or `roost.services.tasks.list_tasks` can be imported and called directly.

## The skill contract

Every Roost skill follows the same shape:

```python
"""One-line description of what the skill does."""

SKILL_META = {
    "name": "my_skill",
    "description": "Human-readable description.",
    "trigger": "myskill",      # Word to type in Telegram
    "version": "1.0.0",
    "risk_tier": "read_only",  # read_only | internal_write | external_write
}

async def run(args: dict) -> str:
    """Main entry point. Return a string to show the user."""
    ...
    return "Done."
```

## Risk tiers — which to pick

Match the tier to what your skill actually does:

- **`read_only`** — only reads data (search emails, list tasks, fetch calendar). Auto-runs, no approval needed.
- **`internal_write`** — modifies local Roost state (creates tasks, notes, preferences). Auto-runs with a notification.
- **`external_write`** — sends data outside Roost (emails, WhatsApp, Slack, public webhooks). **Requires human approval** before anything goes out.

When in doubt, start with `read_only` and test. You can always upgrade
later once you trust the skill's behaviour.

## Safety rules (same as the skill builder's system prompt)

All skills — examples and AI-generated — must:

- **No shell injection** — never pass user input to `os.system` or
  `subprocess` with `shell=True`.
- **No file access outside `data/`** — use `PROJECT_ROOT / "data"` as your
  only writable location.
- **No hardcoded network URLs** to external services without user config.
- **No credential storage in the skill file** — read from `roost.config`.

Read the examples with these rules in mind. If you spot a violation, open
an issue — the point of this gallery is to model *correct* patterns.
