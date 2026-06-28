# How Roost Works — Logic Reference

Knowledge-capture of Roost's core subsystem **logic**, written 2026-06-28 ahead of retiring/archiving Roost. Purpose: preserve the reusable IP (the *patterns*, not the plumbing) so it can be re-expressed anywhere later. Compliance-toolkit logic deliberately excluded.

> **The through-line:** every subsystem is **"AI/automation drafts → a human approves anything irreversible or external,"** backed by **durable state machines in SQLite**. That's the 98/2 / accountability-labour principle in code. The value is the five patterns at the bottom of this doc.

---

## 1. RPA engine

**Flow definition = YAML.** `portal`, `login_url`, and a list of `steps`, each an `op` from a fixed set:
`goto · fill · click · wait_for · wait_ms · press · select_option · get_otp · await_user_session · download_one · download_each · upload_drive · screenshot · whatsapp_send · telegram_send · log`
(`roost/extras/rpa/services/rpa_flows/schema.py:17-42`, with `REQUIRED_ARGS` per op.)

Placeholders resolve at runtime (`schema.py:13,49-59`): `$param:KEY` (inputs), `$cred:KEY` (encrypted credential store), `$var:KEY` (cross-step state, e.g. `$var:otp` after `get_otp`), `$state:KEY` (run state JSON).

**Executor** (`rpa_flows/_interpreter.py:566-657`): async Playwright over **CDP to a browserless sidecar**; per-portal storage-state persisted at `data/browser_state/<user>/<portal>.json` so logins stick (`browser_service.py`). A loop dispatches each step to a handler in `HANDLERS` (cross-checked against `schema.KNOWN_OPS` at import). Data-driven looping via `download_each`, which iterates DOM elements and injects per-row vars (`_interpreter.py:239-278`).

**The clever bit — Singpass live-takeover** (`await_user_session`, `_interpreter.py:170-218`):
1. Mark run `awaiting_input` (`rpa_runs.mark_awaiting_input`).
2. Derive a **public live browser URL** from the sidecar's `/json/list` CDP endpoint, rewritten to `SIDECAR_PUBLIC_URL` (`browser_service.py:220-269`); store it in `state_json.live_url`.
3. Notify via Telegram/web ("take over the browser: <url>").
4. **Block on `page.wait_for_selector(selector, state="visible")`** while the human logs in (Singpass/CAPTCHA/payment) in that live session.
5. When the post-login selector appears → clear `live_url`, **auto-resume**. The bot never handles the credentials.

**State** (`rpa_runs.py:8-18,61-291`): table `rpa_runs`; state machine `running ↔ awaiting_input → completed/failed/cancelled`; resume signalled by an in-memory `asyncio.Event`; rows durable across restarts. **No auto-retry** — a failed step aborts the run (`_interpreter.py:595-598`); wrap at the caller for retries.

---

## 2. Guardian (pre-flight safety gate)

**Every gated tool call** runs `guardian_check()` (`roost/services/guardian.py:104-130`) → **8 rules**, short-circuit on first non-allow → decision ∈ `allow / warn / block / needs_approval`.

| Rule | Tools | Decision |
|---|---|---|
| money movement | stripe_create_refund, shopify_cancel_order, xero_create_invoice (non-DRAFT) | needs_approval |
| client message | send_client_reply | needs_approval |
| bulk delete | delete_*, docker_compose_down, kubectl_delete (with "all"/"bulk") | block (single → warn) |
| bulk email | send_email/ms_send_email/schedule_email, 4+ recipients | block |
| dangerous cmd | ssh_exec/docker/kubectl matching rm -rf, dd, mkfs, `curl|bash`… | block |
| unknown recipient | email tools, address not in contacts | warn |
| tool-call burst | any, >15 calls / 60s | warn |
| sensitive file | read/write/search on .env, .pem, .key, token… | warn |

**The pattern — `guardian_gate()`** (`guardian.py:585-612`): returns **`None` = proceed**, or a dict = stop. Money-moving MCP tools call it first; on `needs_approval` it **inserts a `guardian_drafts` row**, Telegram-notifies with Approve/Reject buttons, and returns `{status: pending_approval, draft_id}`.

**Draft queue** (`database.py:874-894` — `guardian_drafts`: status `pending→approved→executed/failed | rejected`). Approve (`guardian.py:463-510`): load executor → **mark approved before dispatch** (crash-safe) → execute → mark `executed`/`failed` + store `result_json`. Reject (`513-529`): store reason, never execute.

**Hook points:** (a) the Gemini agent loop — block short-circuits, warn logs+continues (`gemini_agent.py:1042-1052`); (b) per-tool MCP wrappers (`tools_stripe/shopify/xero.py`) via `guardian_gate()`. Approvals happen on Telegram (`bot/handlers/guardian_drafts.py`) or HTTP (`api_sme_drafts.py`).

---

## 3. Recipes / automation

**Recipe = trigger + `instructions` + `risk_tier`** (`database.py:718-760` — `automation_recipes`, `automation_runs`). Risk tier is the gate: `read_only` auto-runs; `internal_write` runs + notifies; `external_write` **never auto-runs — always drafts for approval**.

**Triggers:**
- **cron** — natural language → `HH:MM[:day_spec]` config (Gemini or regex, `natural_cron.py`); scheduler ticks every 60s, matches the minute + day filter, dedupes on `last_run` (`bot/scheduler.py:569-646`).
- **event** — `fire_event(type, data)` / `fire_event_sync()` lists enabled event-recipes and matches `trigger_config == event_type` (`services/sop_triggers.py:36-83`; valid types incl. email_received, task_completed, calendar_event_starting, stripe_event…). Webhook handlers in `extras/*/web/api_*.py` call `fire_event_sync`.

**"SOP" = an execution pattern, not data** (`recipes.py:286-392` + `extras/messaging_external/services/ai_cdr.py`): on fire → **AI CDR**: sanitize inbound text → wrap as *untrusted data* → LLM **classifies (intent/urgency/extracted_fields/confidence) with NO tools** → validate against a fixed JSON schema → select a response template by intent → fill `{{vars}}` → apply the risk-tier gate. The recipe's `instructions` are for human understanding; the AI call is sandboxed + tool-less, so malicious inbound content can't drive tools.

---

## 4. Lead-nurture cadence

**Ingest** (`extras/lead_nurture/services/leads.py:239-525`): normalize email/phone to **E.164** for cross-channel dedupe (`_norm_phone`, :54-64) → CRM find-or-create person → optional AI-CDR classify (hot → promote stage) → find/create deal → push AI attributes + append note → **enroll** in a cadence (`nurture_enrollments`: `current_step`, `next_run_at`, `fields`, `last_inbound_at`, `status`, `pause_reason`). Also: hot-lead Telegram/email alert + local task mirror.

**Cadence = YAML** (`services/cadences/library/*.yaml`): steps with `day_offset`/`hour`/`minute_offset`/`channel`/`template`, plus `wait_for_reply` gate steps with `timeout_days`. **Tick loop** (`nurture.py:646-668`) pulls enrollments where `status=active AND next_run_at<=now` (≤50/tick) → `advance_enrollment` (`509-583`): handle gate steps (reply beat last step → exit; timeout → skip; else defer), render template (Jinja2 + fields + agent identity), check **preapproval matching** (score across cadence/source/channel/vertical, `*` wildcard; `store.py:541-577`) → **send** via `_dispatch_send` (email/WhatsApp/Telegram/SMS) **or pause** with `awaiting_approval` + Telegram Approve/Edit/Skip → log to CRM → schedule next step (or mark `completed`).

**Bidirectional Attio sync:** outbound is synchronous REST during ingest; inbound is an **Attio webhook** (`web/api_attio_webhook.py:97-140`) → deal → exit stage (won/lost/DNC) exits enrollments; → pause stage (qualified/meeting) pauses them (`store.py:456-486`).

**STOP/DNC auto-exit:** first inbound token matched against `STOP/STOPALL/UNSUBSCRIBE/CANCEL/END/QUIT` (`web/api_sms.py:41`, `api_chatwoot.py`) → `exit_enrollments_by_contact()` across all matching identifiers (`store.py:367-396`); normal inbound calls `mark_inbound_for_contact()` so `wait_for_reply` gates can fire.

---

## The reusable patterns (the crown jewels)

1. **Human-in-the-loop gate** — `gate() → None=proceed | draft/block` + a durable draft queue + Telegram approve/reject. Used by Guardian, nurture, and recipes' `external_write` tier. *The single most valuable pattern.*
2. **Live-takeover** — pause automation, hand the human a live browser URL, resume on a selector appearing. The bot never touches credentials. Rare and genuinely clever.
3. **Untrusted-input sandboxing (AI CDR)** — classify external messages tool-lessly with injection-hardened prompts; inbound data never touches tools.
4. **Durable state machines** — every run/enrollment/draft is an explicit status row in SQLite; resume via signals; crash-safe ordering (mark-approved-before-dispatch).
5. **Provider abstraction** — CRM behind `find_person/create_deal/append_note/log_communication` so the backend (Attio) is swappable.

## If re-expressing elsewhere
- Patterns 1–4 map cleanly onto any agent platform with an HTTP "draft → approve" surface + a state store. The hard one to replicate is **live-takeover** (needs a CDP-reachable browser with a publicly exposable session URL).
- The AI-CDR sandbox is just an injection-hardened classify-only prompt + fixed JSON schema — portable to any LLM.
