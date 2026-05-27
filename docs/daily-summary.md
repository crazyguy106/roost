# Daily Summary

End-of-day digest delivered via Telegram. Covers the day's nurture activity, tasks, inbound leads, recipe runs, and RPA outcomes — so you can close the laptop knowing what shipped and what needs attention tomorrow.

## What's in it

| Section | Source | Shows |
|---|---|---|
| Nurture | `nurture_enrollments` | Enrollments advanced today (by status) + pending approvals (paused, `awaiting_approval:*`) |
| Tasks | `tasks` | Completed today, still open, overdue (deadline before today) |
| Inbound leads | `nurture_enrollments.created_at` | New today, broken down by source (whatsapp / wechat / web / etc.) |
| Recipes | `automation_runs` | Counts by status + failures with recipe names |
| RPA | `rpa_runs` | Counts by status, successes by portal, failures with portal + error snippet |

The window is "today" in your configured timezone — local midnight to local midnight.

## Telegram commands

| Command | Effect |
|---|---|
| `/summary` | Render and send the summary right now |
| `/summarytime HH:MM [tz]` | Schedule daily delivery. Default tz: `Asia/Singapore`. Examples: `/summarytime 18:00`, `/summarytime 21:30 Europe/London` |
| `/summaryoff` | Disable scheduled delivery (manual `/summary` still works) |
| `/summarystatus` | Show current schedule + last sent date |

Per-user — each Telegram user in `TELEGRAM_ALLOWED_USERS` can configure their own time and timezone.

## How it fires

The scheduler tick runs every 60 seconds (`roost/bot/scheduler.py::_daily_summary_tick`). On each tick it:

1. Reads `daily_summary_time` and `daily_summary_tz` settings for each allowed user.
2. Compares the current local-time `HH:MM` against the configured time.
3. Checks `daily_summary_last_sent` (date string) for dedupe — won't re-send if today's already gone out.
4. Builds the summary, sends it, then records today's date in `daily_summary_last_sent`.

If you change the time and the new time hasn't passed yet today, the dedupe marker is cleared so the summary still fires.

## AI narrative

When `AI_SUMMARY_ENABLED=true` (default) and `GEMINI_API_KEY` is set, `format_summary` prepends a 2–3 bullet narrative above the deterministic counts. Gemini receives the structured `build_summary` dict and is prompted to call out what shipped, what needs attention, and any anomaly — using *only* the numbers shown.

- **Model:** whatever `GEMINI_MODEL` is set to (defaults to `gemini-3-flash-preview` — fast and cheap; ~fractions of a cent per summary).
- **Skipped on empty days** — no Gemini call if every section is zero.
- **Fail-closed** — any error (missing key, network, invalid JSON) is logged and the deterministic summary still goes out unmodified.
- **Disable** with `AI_SUMMARY_ENABLED=false` if you want pure counts.

## Code map

- **Builder + formatter:** `roost/services/daily_summary.py` — `build_summary(user_id, since_utc, until_utc, tz_name)` returns structured dict; `format_summary(s)` renders Markdown.
- **Handlers:** `roost/bot/handlers/daily_summary.py`
- **Scheduler:** `roost/bot/scheduler.py::_daily_summary_tick` (registered with `jq.run_repeating`, interval=60)

## Tests

`tests/test_daily_summary.py` — 12 tests covering builder windowing, section counts, formatter output, and all four handlers (with `monkeypatch` against `roost.services.settings.*` to avoid DB lock contention with the live bot).
