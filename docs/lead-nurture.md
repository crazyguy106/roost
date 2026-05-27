# Lead nurture

Multi-channel inbound capture + automated nurture cadences, with Telegram approval by default and per-rule auto-send opt-in. Built around Attio Free as the system-of-record so we don't reinvent kanban / funnel reports / email sync.

## Architecture

```
Web form ─┐
WhatsApp ─┼─► leads.ingest_lead ─► CRM (Attio) ─► nurture_enrollments
Email    ─┘                                          │
                                                     ▼
                                              nurture.tick (60s)
                                                     │
                                  ┌──────────────────┴──────────────────┐
                                  ▼                                     ▼
                       preapproval match?                       no match → hold
                                  │                                     │
                                  ▼                                     ▼
                         dispatch (email/wa/tg)              Telegram approval prompt
                                  │                                     │
                                  ▼                            approve / skip
                              advance                                   │
                                                                        ▼
                                                                  advance
```

Every step writes a communication note to the CRM, so Attio remains the source of truth for "what was said when".

## Channels in / out

| Direction | Channel    | Adapter                            | Notes |
|-----------|-----------|------------------------------------|-------|
| Inbound   | Web form  | `roost/extras/lead_nurture/web/api_leads.py`           | Existing framework-assessment form auto-routes through `leads.ingest_lead` via the legacy shim in `lead_pipeline.py`. |
| Inbound   | WhatsApp  | `roost/extras/messaging_external/web/api_whatsapp.py`        | Best-effort `leads.ingest_lead(channel="whatsapp", vertical="property")` runs alongside the recipe pipeline. |
| Inbound   | Email     | Gmail poller / MS Graph webhooks   | Currently feeds the recipes pipeline; `lead_ingest` MCP tool lets agents promote a thread to a lead manually. |
| Inbound   | Telegram  | bot handlers                       | `/lead …` slash-command path (planned). |
| Outbound  | Email     | `services/scheduled_emails.py` (Gmail provider) | Honors UTC `scheduled_at` strings. |
| Outbound  | WhatsApp  | `services/whatsapp.py`             | 24-hour customer-care window applies; sends fail loudly if expired. |
| Outbound  | Telegram  | inline notify                      | Used both for human-approval prompts and for the actual Telegram channel. |

The dispatcher (`nurture._dispatch_send()`) currently implements `email`, `whatsapp`, and `telegram` only. Other channels are roadmap; see "Roadmap" below.

## Cadence YAML

Cadences live as YAML under `roost/extras/lead_nurture/services/cadences/library/`. Each file ships its own templates and gets seeded into `nurture_cadences` + `response_templates` on web/bot startup. Seeding is idempotent and never overwrites a `source = 'user'` row.

Minimum shape:

```yaml
slug: property_buyer_intro
name: Property Buyer Intro
vertical: property
templates:
  - name: pbi_day0
    channel: whatsapp
    subject: ""
    body: |
      Hi {{first_name}}, thanks for reaching out about {{property}}…
steps:
  - day_offset: 0
    minute_offset: 2          # send 2 min after enrol
    channel: whatsapp
    template: pbi_day0
  - day_offset: 2
    hour: 9
    tz: Asia/Singapore
    channel: whatsapp
    template: pbi_day2
```

Step fields:
- `day_offset` (required) — days from `started_at`.
- `hour` + `tz` — pin to a specific local hour (defaults to keeping `started_at`'s wall-clock time).
- `minute_offset` — cosmetic delay for "immediately after enrol" steps.
- `channel` — `email | whatsapp | telegram` (sms is roadmap, not yet dispatched).
- `template` — must match a `response_templates.name` (templates inline in the same file are seeded automatically).

Validation runs at load time; broken files are skipped with errors surfaced via `cadence_library_status`.

Shipped library:

| Slug                       | Vertical            | Channel  | Days |
|----------------------------|---------------------|----------|------|
| `framework_assessment`     | generic             | email    | 0/3/7 |
| `property_buyer_intro`     | property            | whatsapp | 0/2/5 |
| `financial_advisor_intro`  | financial_advisor   | email    | 0/3/7 |
| `generic_b2b`              | generic             | email    | 0/3/7 |

## Approval gate

Default behaviour: every step is **held for approval** — the engine pauses the enrollment, posts the draft to Telegram with **inline Approve / Skip buttons**, and waits for the operator to act. The hold message also includes plain-text `/napprove <id>` and `/nskip <id>` shortcuts so a tap or a typed command both work.

Three ways to act on a held step:

| Surface | Approve | Skip |
|---------|---------|------|
| Telegram inline button | tap **✅ Approve** | tap **⏭ Skip** |
| Telegram command | `/napprove <enrollment_id>` | `/nskip <enrollment_id> [reason]` |
| MCP / programmatic | `nurture_approve_pending(id)` | `nurture_skip_pending(id, reason)` |

Other Telegram operator commands:

- `/nlist [paused\|active\|completed\|exited]` — list enrollments by status (default `paused`).
- `/preapprove <slug\|*> [source=X] [channel=Y] [vertical=Z] [note=…]` — create a pre-approval rule from your phone.

Inline-button routing lives in `roost/bot/handlers/nurture_approval.py::handle_nurture_callback`, registered in `roost/bot/main.py` ahead of the generic callback handler with the pattern `^(napprove|nskip):\d+$`.

To opt into auto-send, create a **pre-approval rule** with the most-specific axes you want to gate on:

```python
cadence_preapprove(
    cadence_slug="property_buyer_intro",
    source="whatsapp",
    channel="whatsapp",
    vertical="property",
    note="trusted: WhatsApp buyer flow",
)
```

Wildcards (`"*"`) are accepted on every axis. The most-specific matching rule wins (exact match = 1 point per axis; ties go to the most recent).

## CRM stage-change webhook

`POST /api/attio/webhook` (HMAC-SHA256 with `ATTIO_WEBHOOK_SECRET`) listens for deal stage changes and reacts:

| Stage match (case-insensitive) | Action |
|-------------------------------|--------|
| `won`, `closed won`, `lost`, `closed lost`, `disqualified`, `unqualified`, `do not contact` | Mark every active/paused enrollment for that deal as `exited`. |
| `qualified`, `meeting booked`, `demo scheduled`, `negotiation`, `proposal sent`, `in conversation` | Pause every active enrollment (a human is now driving). |
| Anything else | No-op. |

Returns `{ok, applied, actions: [{deal_id, stage, action, applied}]}`.

## MCP tool surface

All tools live in `roost/extras/lead_nurture/mcp/tools_leads.py` and prefix with `lead_*` / `cadence_*` / `nurture_*`:

- `lead_ingest(channel, email|phone, …)` — multi-channel entry point. Returns `{ok, errors, crm_person_id, crm_deal_id, enrollment_id, classification, …}`. **`ok` is `False` when any step in the pipeline produced an error** (CRM person create rejected, deal create failed, etc.) — the lead may still be partially captured. Inspect `errors` for the specifics.
- `cadence_list`, `cadence_get`, `cadence_library_status`.
- `cadence_enroll`, `cadence_list_enrollments`, `cadence_pause`, `cadence_resume`.
- `nurture_approve_pending`, `nurture_skip_pending`.
- `cadence_preapprove`, `cadence_list_preapprovals`, `cadence_delete_preapproval`.
- `nurture_tick(max_per_tick=50)` — manual catch-up; the scheduler also runs this every 60 s.

## Operational notes

- The 60 s nurture tick lives on the **bot** scheduler (`roost/bot/scheduler.py::_nurture_tick`). Web-only deployments without the bot will not advance enrollments — run `nurture_tick` from MCP or wire your own cron.
- `next_run_at` is always stored as a UTC ISO string. `_step_run_at` in `services/nurture.py` is the single computation site.
- `_dispatch_send` is the seam to mock in tests (`tests/test_nurture.py` shows the pattern).
- Adding a new cadence = drop a YAML in `library/` and restart. Adding a new channel = extend `_dispatch_send` and the YAML schema's `channel` enum.
- Telegram approval shortcuts are **`/napprove`** and **`/nskip`** (not `/approve` / `/skip`, which are reserved by the recipes engine).

## Template variables

Templates use `{{var}}` placeholders. The renderer (`response_templates.fill_template`) drops any placeholder whose value is empty, so missing values produce gaps rather than the literal string `{{var}}`. Three sources feed the field map at render time:

1. **Lead-side defaults** — set by `leads.ingest_lead` from the inbound: `first_name`, `name`, `email`, `phone`, `org_name`, `lead_source`.
2. **AI-extracted fields** — anything in `classification["extracted_fields"]` (e.g. `property_interest`, `preferred_area`, `budget`) is merged in. Only populated when AI CDR is enabled and runs.
3. **Operator identity** — read from per-user settings:
   - `agent_name` — your full name (used inline in greetings).
   - `agent_cea_no` — CEA registration number (exposed for templates that want it inline).
   - `agent_signoff` — the full signature block (multi-line OK). Recommended: put your CEA reg number in here so it always appears.

Set them once via the settings page or programmatically:

```python
from roost.services.settings import set_setting
set_setting("agent_name", "Ethan Seow", user_id=1)
set_setting(
    "agent_signoff",
    "Best regards,\nEthan Seow\nCEA Reg. R012345A | Verixiom Realty",
    user_id=1,
)
```

Safe defaults are applied when nothing else fills them: `agent_signoff` → "Best regards", `property_interest` → "your enquiry", `preferred_area` → "the area you're looking at" — so templates never read like `"about ."` or `"(CEA Reg. )"`.

**Reseeding library YAMLs.** `seed_library()` is idempotent and won't overwrite existing rows. After editing a YAML in `library/`, either delete the cadence + templates first or patch the row directly via `response_templates.update_template(...)`.

## Smoke testing

Two scripts let you exercise the engine end-to-end without setting up Meta tunnels or waiting for the 60s scheduler tick.

**`scripts/smoke_nurture.py`** — direct lead ingest path:

```bash
python3 scripts/smoke_nurture.py --vertical generic --cleanup
python3 scripts/smoke_nurture.py --vertical property --email me@example.com
```

Calls `leads.ingest_lead` with synthetic data, accelerates the first step's `next_run_at` to now, runs `nurture.tick()`, and prints what happened. Use to verify cadence selection + approval gate.

**`scripts/smoke_whatsapp_inbound.py`** — full inbound webhook pipeline:

```bash
python3 scripts/smoke_whatsapp_inbound.py --cleanup
python3 scripts/smoke_whatsapp_inbound.py --phone +6591234567 --name "Alice Tan" \
    --text "Looking for a 3BR in D9"
```

Calls `api_whatsapp._process_inbound` directly with a Meta-shaped payload (skips HTTP/HMAC — already covered by `tests/test_whatsapp_inbound.py`). Exercises: parse → `leads.ingest_lead` → AI CDR → cadence enrollment → tick → Telegram prompt. Default phone (`+6591234567`) is libphonenumber-valid; arbitrary E.164 strings will be rejected by Attio's phone validator.

Both scripts:
- Run against whatever CRM provider is active. Safe by default because the first step is held for approval — nothing is sent until you tap **✅ Approve** on Telegram. Live-Attio side effect is one test person + deal + note.
- Print the active CRM provider and (for `smoke_nurture.py`) the count of preapproval rules. **Caveat:** if a wildcard preapproval rule matches the smoke lead, the first step **will** auto-send.

## Tests

| File | Covers |
|------|--------|
| `tests/test_cadences.py` | Library validation, idempotent seed, enrollment, due picker, preapproval matching, stage-change router. |
| `tests/test_nurture.py` | Hold-for-approval, preapproved auto-send, approve/skip flow. |
| `tests/test_nurture_extras.py` | `_step_run_at` time math + multi-tick to `completed`. |
| `tests/test_nurture_dispatch.py` | Channel dispatch matrix (email / whatsapp / telegram / unsupported). |
| `tests/test_leads.py` | `ingest_lead` with a fake CRM provider — dedupe + vertical→cadence routing. |
| `tests/test_attio_webhook.py` | HMAC verification + stage-change webhook payload shapes. |
| `tests/test_whatsapp_inbound.py` | Inbound WhatsApp webhook calls `leads.ingest_lead`. |
| `tests/test_tools_leads.py` | MCP wrapper envelopes and arg pass-through. |
| `tests/test_scheduler_jobs.py` | `_nurture_tick` JobQueue registration + exception swallowing. |
| `tests/test_bot_nurture_approval.py` | `/napprove`, `/nskip`, `/nlist`, `/preapprove`, and inline-button callback routing. |

## Roadmap

Two items are referenced as "planned" / "roadmap" elsewhere in this doc and tracked here as the canonical to-build list:

### Telegram `/lead …` inbound slash-command

Currently a Telegram operator can read a thread but cannot promote it to a lead from the chat surface — they have to switch to MCP or the web UI and call `lead_ingest` manually. The planned shape:

```
/lead <name> | <phone-or-email> [| <vertical>] [| <free-text notes>]
```

- Adds a handler under `roost/bot/handlers/` that parses the pipe-delimited args, calls `lead_ingest(channel="telegram", source="manual_promote", …)`, and replies with the resulting enrollment ID + cadence slug.
- The `channel="telegram"` inbound is already accepted by `leads.ingest_lead`; what's missing is the bot-side glue.
- Symmetric with the existing `/preapprove`, `/napprove`, `/nskip` operator commands.

### SMS outbound channel

The YAML schema previously accepted `channel: sms` but the dispatcher silently no-op'd it; the enum has been trimmed to `email | whatsapp | telegram` to keep doc and code in sync. To re-enable SMS:

1. Pick a provider — Twilio (global), Vonage (global), or a local-MY/SG SMS gateway (cheaper for the regional flows).
2. Add an `SMS_ENABLED` flag in `roost/config.py` + `config_service.py::FEATURE_FLAGS`, plus credentials in `env-templates/`.
3. Add `services/sms.py` mirroring the WhatsApp adapter shape (send, error envelope, optional inbound webhook later).
4. Extend `nurture._dispatch_send()` with an `sms` branch and re-add `sms` to the channel enum in this doc + the YAML validator.
5. Tests: extend `tests/test_nurture_dispatch.py` with an SMS row in the channel dispatch matrix.

Adapter must fail closed when `SMS_ENABLED` is off — same posture as WhatsApp / DNC / CDD.
