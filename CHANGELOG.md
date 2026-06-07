# Changelog

All notable changes to Roost are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

While Roost is pre-1.0, the **MINOR** version is bumped for new
features and the **PATCH** version for fixes; any backward-incompatible change
(env var rename, schema migration that isn't auto-applied, removed MCP tool,
changed STOP keyword set) is called out explicitly under a `### Breaking`
heading so self-hosters know to read before `git pull`.

## [Unreleased]

## [0.8.1] — 2026-06-07

### Fixed
- **AI reply truncated mid-sentence.** `draft_reply` capped
  `max_output_tokens` at 400; newer Gemini models spend part of that budget
  on internal "thinking" tokens, leaving the visible reply cut off. Raised
  to 1024 (matching `classify_message`).

## [0.8.0] — 2026-06-07

### Changed
- **Risk-tiered reply gating — inbound auto-replies are now genuine
  free-form AI drafts, held by Guardian; canned replies auto-send.** The
  inbound reply was a filled template (deterministic, no leak risk) yet was
  still held — theatre. It's now a real contextual reply written by Gemini
  (`ai_cdr.draft_reply`), MAS/FAA-aware (no product/figure/return claims
  before a fact-find), after a **context-pull** of the recent thread
  (`conversation.recent_context`). Because that content is novel each time
  (leak / hallucination / prompt-injection surface), it's held in Guardian
  for the adviser to approve. Canned/template replies — the qualification
  questions — continue to **auto-send** (no per-message hold), since fixed
  text carries none of that risk. The `chatwoot_inbound` / `whatsapp_inbound`
  recipe is now just the on-switch for AI replies (its template is no longer
  used for the reply body).

### Added
- `ai_cdr.draft_reply` (free-form, MAS-aware reply) and
  `conversation.recent_context` (compact thread summary for the drafter).

## [0.7.0] — 2026-06-07

### Added
- **Configurable CRM deal-pipeline stages.** New `deal_stages` setting
  maps Roost's lead lifecycle to your CRM's actual pipeline stage names
  (`new` / `hot` / `won` / `lost`), defaulting to Attio's standard pipeline
  (`Lead` / `In Progress` / `Won 🎉` / `Lost`). Roost opens a deal at `new`
  on first contact and promotes it to `hot` when a message scores hot.

### Fixed
- **Deal-create no longer sends a non-existent stage.** `ingest_lead`
  hardcoded the stage `"Hot Lead"`, which isn't in Attio's default pipeline
  (every hot lead 400'd with `value_not_found`). It now uses the configured
  `hot` stage.
- **Returning leads no longer spawn duplicate deals.** `ingest_lead` now
  checks for an existing deal on the contact (`list_deals(person_id=…)`)
  and reuses it — promoting it to the hot stage if the new message is hot —
  instead of opening a fresh deal on every inbound.

## [0.6.0] — 2026-06-05

### Changed
- **Inbound AI-drafted replies are now held by Guardian, not the recipe
  queue.** When a `chatwoot_inbound` / `whatsapp_inbound` recipe drafts a
  reply, the *send* now routes through `guardian_gate("send_client_reply")`
  — a new Guardian rule (`_check_client_message` → NEEDS_APPROVAL) parks it
  as a real **`guardian_drafts`** row and pings the adviser to
  **Approve/Reject in Telegram** (`/gdrafts`); only on approval does
  Guardian's `send_client_reply` executor deliver it on the originating
  channel. This makes the "Guardian holds AI-drafted client-facing messages
  for your review" model literal (FA-edition slide 29/30) and unifies the
  approval surface. With `GUARDIAN_ENABLED=false`, the draft is surfaced for
  a manual send instead — never auto-sent. The recipe is now just the
  drafter (set it `read_only`); the older recipe-approval `send-on-approve`
  path remains for any external_write recipes that still use it.

### Added
- Guardian `send_client_reply` tool + executor (`services/guardian.py`).

## [0.5.1] — 2026-06-05

### Added
- **Telegram heads-up when an inbound message is gated.** Completing the
  v0.5.0 automation controls: when the gate pauses or dormancy-skips an
  inbound, Roost now pings the operator on Telegram
  (`messaging_external/services/operator_notify.py`) so they know to handle
  it in Chatwoot — e.g. `💤 Chatwoot lead resurfaced after 30.0h dormant`
  or `⏸ Automations paused`, with the contact and message preview.
  Best-effort; no-ops when Telegram isn't configured.

## [0.5.0] — 2026-06-05

### Added
- **Automation controls — a global pause switch and a per-contact recency
  gate.** Both gate inbound auto-engagement in `api_chatwoot` /
  `api_whatsapp` right after the STOP/HELP intercepts (which always run):
  - **Pause switch.** When on, inbound is still logged (and lands in the
    Chatwoot inbox) but Roost runs *no* automation — no auto-qualify, no
    AI-draft recipes, no cadence sends. Two sources: the baked
    `automations_paused` setting, or a **live sentinel file**
    `data/automations_paused` (`touch` to pause, remove to resume — no
    restart/rebuild, since `data/` is bind-mounted).
  - **Recency gate.** `auto_engage_window_hours` (default 24, 0 = off): a
    *returning* contact whose prior activity is older than the window is
    treated as dormant and not auto-engaged — their message waits in the
    inbox for a human instead of auto-continuing the questionnaire or
    drafting a reply. A brand-new contact is never dormant.
  - New `lead_nurture/services/gating.py::automation_gate` +
    `cadences/store.py::last_activity_at`.

## [0.4.4] — 2026-06-05

### Changed
- **Debounce lowered further to 4s** (`fragmented_messages.debounce_seconds`
  6 → 4; fast-path 2s and 40s max-wait unchanged) for a more immediate
  reply on a single inbound message.

## [0.4.3] — 2026-06-05

### Changed
- **Snappier inbound replies.** The fragmented-message debouncer waited 20s
  of silence before processing an inbound message (4s fast-path for
  messages ending in `.?!`), which felt like a non-response on a single
  message. Lowered the shipped defaults to **6s debounce / 2s fast-path /
  40s max-wait** (`roost-config/settings.yaml` + the `_DEFAULTS` fallback in
  `lead_nurture/services/settings.py`). Still batches genuine multi-message
  bursts, just far more responsively. Tune per-instance in
  `roost-config/settings.yaml` under `fragmented_messages`.

## [0.4.2] — 2026-06-04

### Fixed
- **Outbound messages rendered with awkward mid-sentence line breaks on
  WhatsApp.** Question packs and cadence templates are authored as YAML
  `|` block scalars hand-wrapped at ~70 columns; `|` keeps those wrap
  points as hard newlines, so a qualifying question arrived broken across
  three lines (and inconsistently across WhatsApp clients). New
  `messaging_external/services/text_format.py::collapse_soft_wraps` folds
  soft wraps back into flowing lines at the send boundary
  (`whatsapp.send_text_message` + `chatwoot.send_message`), while
  preserving blank-line paragraph breaks, list items, and short
  intentional breaks like a signature block. Applied to every WhatsApp
  surface so formatting is consistent regardless of source.

### Fixed
- **Returning leads were split into duplicate enrolments and re-asked the
  qualification questions.** Two root causes: (1) `_norm_phone` only
  stripped spaces, so the bare WhatsApp/Chatwoot wa_id (`6597531358`) and
  the E.164 contact phone (`+6597531358`) keyed as two different people;
  it now canonicalises to E.164. (2) `enroll_lead` always `INSERT`ed — it
  now **reuses an existing live (active/paused) enrolment** for the same
  contact + cadence instead of spawning a duplicate. And
  `start_qualification_if_needed` now **skips a contact who already has a
  `_qualify_status`**, so a returning lead isn't re-interrogated. Net
  effect: a contact with history continues their conversation (or routes
  to the approval-gated AI draft) rather than starting over each message.
  Affects every channel, not just Chatwoot.

  **Behaviour change for integrators:** `enroll_lead` is now idempotent
  per live contact+cadence — a second call returns the existing enrolment
  rather than a new row.

## [0.4.0] — 2026-06-04

### Added
- **Auto-qualify inbound Chatwoot leads.** A new WhatsApp lead fronted by
  Chatwoot now automatically receives the first qualifying question (from
  the cadence's question pack, e.g. `property_buyer_intro`) and the
  questionnaire captures their answers — no operator action. Previously
  `chatwoot` wasn't an addressable channel for qualification, so these
  leads got logged but never replied to. Added `chatwoot` to the channel
  guards in `leads.py` + `qualification.py`; the Chatwoot webhook now
  short-circuits the recipe/classify branch when a question was just sent
  (so a lead doesn't get a question *and* an AI draft for one message).
- **Send-on-approve for recipe drafts.** Approving an `awaiting_approval`
  recipe run now delivers the draft back to the channel it came from
  (Chatwoot conversation / WhatsApp), keyed on the run's `trigger_data`.
  Before, `approve_run` only filed the run — the approved reply never
  reached the customer. The send stays behind the human approval gate
  (draft-first); delivery failures are reported in the result, never
  unwind the approval. Wired in `services/recipes.py::approve_run`, so it
  works from the Telegram `/approve`, the inline Approve button, and any
  future approval surface.
- **`ENABLE_TELEGRAM` build arg is now env-driven** in `docker-compose.yml`
  (`${ENABLE_TELEGRAM:-false}`), so the operator-approval Telegram bot can
  be enabled from `.env` without editing compose. Default stays off.

## [0.3.3] — 2026-06-04

### Fixed
- **Inbound webhooks bounced to the login page.** `UnifiedAuthMiddleware`
  allow-lists third-party webhook paths (they can't carry a session
  cookie; each handler verifies its own signature), but
  `/api/chatwoot/webhook` and `/api/sms/webhook` were never added — so
  every Chatwoot and Twilio SMS delivery got a 307 redirect to
  `/auth/login-page` and silently failed. Added both to the allowlist in
  `roost/web/app.py`. New `tests/test_webhook_auth_allowlist.py` drives
  the full `create_app()` stack and asserts every webhook path bypasses
  auth (the prior adapter tests stubbed the middleware, so the gap was
  invisible).

### Docs
- **`docs/chatwoot.md`** — call out that the Chatwoot→Roost webhook URL
  must be a publicly-resolvable host: Chatwoot's SSRF guard rejects
  internal hostnames like `http://roost:8080` (`Hostname '…' has no
  public ip addresses`). Use the public `https://` host (proxied back to
  Roost); only `CHATWOOT_URL` (Roost→Chatwoot) uses the internal name.

## [0.3.2] — 2026-06-04

### Fixed
- **`mcp-inventory` CI never went green after day one.** `scripts/gen_mcp_inventory.py`
  stamped `**Generated:** <today>` into `docs/mcp-inventory.md`, so the
  drift-check workflow's `git diff --exit-code` failed on every push the
  day after a commit even with zero tool changes. Dropped the date line
  (and the now-unused `datetime` import) — the tool count is sufficient
  provenance and is the value the check actually guards.
- **CRM test isolation leak.** The `tests/test_crm_local.py` autouse
  cleanup deleted from a non-existent `communications` table; the real
  table is `contact_communications`, so logged-comm rows survived between
  tests and could cross-pollute assertions under reordering.
- **CRM note tags silently dropped.** `LocalProvider.append_note` passed
  `NoteCreate(tags=…)`, but the pydantic field is `tag` (singular) —
  every CRM note was written with an empty tag. Pydantic v2 ignored the
  unknown kwarg without error.
- **`LocalProvider.update_person` dropped plural identifiers.** It only
  read scalar `name/email/phone/notes`, silently discarding
  `emails=[…]` / `phones=[…]` that `create_person` accepts. Now mirrors
  create: head fills the scalar slot, the tail is written to
  `contact_identifiers`.
- **CI pip cache was a no-op.** `actions/setup-python`'s `cache: pip` had
  no `cache-dependency-path`, so it looked for a root `requirements.txt`
  (absent) and silently disabled caching. Pinned the key to
  `requirements/base.txt` + `requirements/test.txt`.

## [0.3.1] — 2026-06-03

### Added
- **CI** — `.github/workflows/test.yml` runs `pytest -q` on push/PR to
  `main` (Python 3.12, ubuntu-latest). `.github/workflows/mcp-inventory.yml`
  regenerates `docs/mcp-inventory.md` from the source `@mcp.tool()`
  decorators and fails on drift. Status badges in `README.md`.
- **CRM tests.** `tests/test_crm_local.py` (23 tests) exercises the
  `LocalProvider` end-to-end against the real SQLite — Person CRUD,
  org lookup, deal stubs, notes, comms logging, custom fields.
  `tests/test_crm_contract.py` (12 tests) asserts every provider
  (local / attio / hubspot / zoho / salesforce / pipedrive) subclasses
  `CrmProvider`, instantiates (catches missing `@abstractmethod`s),
  sets `name`, and returns `{ok: bool, detail: str}` from
  `test_connection()` instead of leaking vendor exceptions. Suite is
  now 846 tests.
- **Messaging-adapters doc.** `docs/messaging-adapters.md` documents
  the four standalone-process adapters (Discord / Slack / Signal /
  Matrix) — env vars, pip deps, start commands, auth model,
  deployment notes, and the shared base in `roost/adapters/__init__.py`.
  Linked from `CLAUDE.md` and `README.md`.
- **Architecture-audit ledger.** `docs/architecture-audit.md` — a
  living findings log with ID / status / severity / evidence / next
  step. Initial pass: 11 findings, 9 resolved this release.

### Fixed
- **`LocalProvider.log_communication` AttributeError.** Called the
  non-existent `comms_svc.create_communication`; the real name is
  `log_communication`. Surfaced by the new CRM contract test, fixed
  in `roost/extras/crm/services/local.py`.
- **`docs/mcp-inventory.md` drift.** Doc was at 330 tools, source had
  333. Regenerated; the new CI workflow prevents future drift.
- **CRM bundle missing from `CLAUDE.md` "Doc" column.** Pointed at
  `docs/crm-adapters.md`.
- **Stale "Phase 1+" comment in `roost/config.py`** — the SME Ops
  section header still promised future adapters that already shipped.
  Rewritten to reflect current state.
- **`env-templates/demo.env` doesn't pin SME sub-flags.** Added an
  explicit SME Ops block (`SME_OPS_ENABLED` / `ZAPIER_ENABLED` /
  `STRIPE_ENABLED` / `SHOPIFY_ENABLED` / `XERO_ENABLED` + matching
  `CHANGE_ME` credentials) so the demo state is reproducible.
- **`docker-compose.override.yml` convention break self-documented.**
  Added a 7-line header comment pointing at `docs/container-ssh-access.md`
  and the CLAUDE.md "SSH-into-Claude shortcut" section so contributors
  don't trip on the in-tree override file.

### Removed
- **Dead `roost/bot/adapters/` stub package.** `dingtalk.py` and
  `feishu.py` had been `Status: STUB — not yet implemented` since
  2026-04-12 (no config, no env, no tests, no docs, no imports).
  Deleted. Re-implement as `roost/adapters/<name>_bot.py` following
  the Discord/Slack pattern when demand exists. See
  `docs/messaging-adapters.md`.

## [0.3.0] — 2026-06-03

### Fixed
- **FA Phase 1.6 — audit fixes (cap race, resume semantics, sweep
  controls, picker dedup, UI polish, a11y).**
  - **Cap race closed.** `POST /api/tty/windows` previously read
    `count_windows()` then called `auto_create()` as two unrelated
    statements — two concurrent POSTs could both observe `count == cap-1`
    and squeak past the limit. New `chat_windows.create_window_if_under_cap`
    wraps the count + insert in a single `BEGIN IMMEDIATE` transaction
    so the cap is enforced atomically; at-cap path returns the existing
    409 + evictee recommendation.
  - **Resume endpoint is now intent-only.** `POST /api/tty/windows/{id}/
    resume` no longer flips `tmux_window_alive`. The WS attach handler
    already does that lazily when the browser actually connects, so
    flipping it twice (once eagerly, once on attach) was redundant and
    masked the "row is still paused until attach" semantics that the
    sweeper-vs-resume race needs to observe. Endpoint now validates
    ownership + returns the row. Test renamed to
    `test_resume_endpoint_validates_scope_only`.
  - **Sweeper batch limit.** `TTY_SWEEP_BATCH` (default 50) caps the
    number of rows the idle sweep processes per tick so a transient
    burst of stale rows can't tie up the scheduler behind tmux.
    `list_idle_for_sweep` now orders oldest-first so the most-idle rows
    get reaped under pressure.
  - **TTY master flag.** New `TTY_ENABLED` (default `true`) gates the
    scheduler tick — instances that don't expose the operator surface
    skip the 5-minute work entirely.
  - **Picker narrows exception nets.** `_picker_tasks` /
    `_picker_chatwoot` previously caught bare `Exception` and logged at
    `DEBUG`; now narrowed to `(ImportError, AttributeError, RuntimeError)`
    and logged at `WARNING` so genuine failures don't hide.
  - **Picker dedup.** Choosing a picker entry whose `linked_entity_*`
    already maps to an existing window now switches to that window
    instead of creating a duplicate (blanks always create).
  - **Close-window fallback honours resume.** `onCloseWindow` now routes
    the post-close fallback through `switchTo()` instead of `connect()`
    so a paused fallback window gets resumed end-to-end.
  - **Reconnect banner stays useful.** `suppressReconnectBanner` is now
    cleared in the WS `open` handler — a subsequent unexpected close
    still surfaces, only the immediate post-reconnect transient is
    silenced.
  - **Evict-modal dismiss restores picker.** Tracking `lastModalIntent`
    means dismissing the at-cap evict view (cancel / Esc / backdrop)
    brings the picker back instead of dropping the operator into nothing.
  - **a11y.** Tab close `×`, tab body, drawer rows, and `+ New` button
    are now keyboard-activatable (`role="button"`, `tabindex="0"`,
    Enter/Space handlers, `aria-label`). Status badge + status line
    get `aria-live="polite"`.
  - **Nits.** `asyncio.get_event_loop()` → `get_running_loop()` in the
    WS PTY pump; evict modal subtitle reads from runtime `cap` instead
    of a hard-coded `5`; stale 1.6d docstring updated; dead
    `drawerVisible` flag removed; `ChatWindow.from_row` no longer
    guards against pre-migration columns (the additive migration runs
    at boot). Suite at 811 green (was 808, +3 new atomic-cap + batch
    tests).

### Added
- **FA-edition Phase 1.6d — idle sweep, memory pressure, resume drawer.**
  Two new `chat_windows` columns via additive ALTER (idempotent):
  `tmux_window_alive` (INTEGER, default 1) and `last_resume_cmd` (TEXT).
  New service `roost.services.tty_sweeper` runs on a 5-min scheduler
  tick:
  - **Idle sweep** — windows whose `last_active_at` is older than
    `TTY_IDLE_TTL_MINUTES` (default 360 = 6h) get their tmux window
    killed, the row marked `tmux_window_alive=0`, and a resume hint
    stored. Row stays so the operator can resume.
  - **Memory-pressure sweep** — when cgroup v2 reports
    `memory.current/memory.max >= TTY_MEMORY_PRESSURE_RATIO`
    (default 0.85), evicts up to 5 coldest live windows globally,
    re-checking pressure between kills. No-ops in non-cgroup
    environments (CI, fresh dev boxes).
  New REST: `GET /api/tty/windows/paused` (top 15 paused windows for
  the caller, scope-isolated) and `POST /api/tty/windows/{id}/resume`
  (flips `tmux_window_alive=1` so the next WS attach lazy-recreates
  the tmux window). The WS attach handler now calls
  `mark_window_alive` in addition to `touch_active` so resuming
  through any path keeps the alive bit in sync. UI: status badge
  `live/cap` next to the connect line; collapsible "Paused
  conversations" drawer above the tab strip (clicking an entry calls
  resume + reconnects + promotes it into the tab strip); paused tabs
  show a green "Resume" pill instead of the kill `×` style. New env
  knobs in `env-templates/fa.env`. 13 new tests
  (`tests/test_tty_sweeper.py` × 8 + paused/resume in
  `tests/test_web_tty.py` × 5). Suite at 808 green (was 796, +12).
- **FA-edition Phase 1.6c — picker + cap-and-evict.** `POST /api/tty/
  windows` now enforces `DEFAULT_WINDOW_CAP` (5): at-cap requests get
  `409` with body
  `{"error": "at_cap", "cap": 5, "evictee_recommendation": {…}}` —
  the recommendation is the same `chat_windows.recommend_evictee` row
  the picker primitive returns (NULL `last_inbound_at` first, then
  oldest inbound, tied by oldest active). New `GET /api/tty/picker`
  returns up to ~4 recent in-progress tasks + ~4 open Chatwoot
  conversations (each soft-failing to `[]` when the bundle is off or
  the upstream call errors) plus a sentinel `{kind: "blank"}` entry.
  No search bar — the design holds at ≤8 candidates; the drawer for
  the long-tail "resume an older window" path lands in 1.6d. UI: the
  `+ New` button now opens a picker modal (rows show `kind` badge,
  title, hint preview); selecting a non-blank entry seeds the window's
  `title` + `linked_entity_type`/`linked_entity_id`. On 409 the modal
  switches to a "you're at the limit" view that lists windows sorted
  oldest-active-first with the recommended evictee highlighted; one
  click closes it (DELETE flow), and the picker re-opens so the
  operator can continue what they started. 4 new tests in
  `tests/test_web_tty.py` (cap-409, recommendation prefers
  NULL-inbound, picker returns blank with bundles off, picker requires
  auth). Suite at 796 green (was 792, +4).
- **FA-edition Phase 1.6b — multi-window web tty (tabs).** `/tty` now
  renders a tab strip backed by `chat_windows`. New REST surface
  (`roost/web/api_tty.py`):
  `GET /api/tty/windows` (newest-active-first, includes `cap`),
  `POST /api/tty/windows` (empty title → backend-assigned `Untitled N`,
  optional `linked_entity_type`/`linked_entity_id`),
  `DELETE /api/tty/windows/{id}` (scoped to the caller; 404 for other
  users' rows). The WS handler accepts `?window=<tmux_window_name>`,
  validates against `^[A-Za-z0-9_\-]{1,64}$` and the caller's
  `chat_windows` rows, lazy-creates the tmux window inside the per-user
  session, and bridges the PTY with
  `tmux attach-session -t <s> \; select-window -t <s>:<name>`
  (the `;` is its own argv element — tmux command separator, not shell).
  No-`?window`: pick the most-recently-active row or auto-create
  `"main"`. Service helper `chat_windows.auto_create(user_id, title=…)`
  picks a unique `w-<6hex>` name with retries. UI: tabs with
  hover-reveal `×` (one-click confirm — "anything running inside is
  lost"), `+ New` button (creates blank window + switches to it),
  browser-tab close still detaches without killing. Cap is reported but
  not yet enforced — Phase 1.6c adds the picker + 409 with evictee
  recommendation. 14 tests in `tests/test_web_tty.py` (was 3) covering
  REST CRUD, user-scope isolation on list+delete, default title,
  `auto_create` uniqueness, and WS rejection of unsafe window names.
  Suite at 792 green (was 781, +11 net).
- **FA-edition Phase 1.6a — `chat_windows` data model.** New core SQLite
  table (`SCHEMA_V31`) + service `roost.services.chat_windows` that maps
  tmux windows inside the per-user `roost-<user_id>` session to leads /
  tasks / Chatwoot conversations. Schema:
  `(id, user_id, tmux_window_name UNIQUE per user, title,
  linked_entity_type, linked_entity_id, last_topic, last_active_at,
  last_inbound_at, created_at)`. Service surfaces `list_windows`,
  `create_window`, `get_window`, `get_window_by_tmux_name`,
  `count_windows`, `touch_active`, `mark_inbound`, `link_to_entity`,
  `set_topic`, `delete_window`, and the cap-and-evict picker primitive
  `recommend_evictee(user_id, cap=5)` (null `last_inbound_at` first,
  then oldest inbound, tie-broken by oldest active). `DEFAULT_WINDOW_CAP
  = 5`. Foundation only — no tmux side-effects, no web routes; those
  land in Phase 1.6b (multi-window WS + tabs) and 1.6c (picker + cap
  enforcement). 17 new tests in `tests/test_chat_windows.py`. Suite at
  781 green (was 764, +17).
- **FA-edition Phase 1.5 — web tty (single-window).** New `/tty` page +
  `/ws/tty` WebSocket that bridges xterm.js to a persistent per-user tmux
  session inside the container (`tmux -L roost-tty new-session -A -s
  roost-<user_id>`). Closing the tab detaches; reopening reattaches with
  full scrollback, so an interactive `claude` (or any long-running shell
  command) survives between visits. Built so FA-edition operators — who
  aren't expected to SSH in — can drive the agent loop from a browser.
  Auth re-checks the session cookie inside the WS handler because
  `BaseHTTPMiddleware`-based `UnifiedAuthMiddleware` doesn't run on WS
  upgrades; anonymous clients get `close(1008)`. New files:
  `roost/web/api_tty.py`, `roost/web/templates/tty.html`,
  `docs/web-tty.md`, `tests/test_web_tty.py` (page-renders + WS-auth
  smoke tests). Sidebar "Terminal" link added in `base.html`. Phase 1.6
  (multi-window tabs + `chat_windows` mapping + cap-and-evict picker)
  is the planned follow-up.
- **FA-edition Phase 1C — Telegram queue for Guardian drafts.** Money-moving
  tool calls (Stripe refunds, Shopify cancels, non-DRAFT Xero invoices) that
  route through `guardian_gate` and land as a pending draft now push a
  Telegram notification to allowed users with ✅ Approve / ❌ Reject inline
  buttons (previously: drafts only surfaced via the `/sme/sync-status` web
  card). Tapping Approve calls `guardian.approve_draft` (dispatches the
  underlying executor); tapping Reject calls `guardian.reject_draft` with
  the operator id as the reason. Both audit-log via
  `roost.services.activity.log_action` (`guardian.approve` /
  `guardian.reject`). New `/gdrafts` command lists pending drafts. New
  handler module `roost/bot/handlers/guardian_drafts.py`; pattern-filtered
  `CallbackQueryHandler(handle_guardian_draft_callback, pattern=r"^gdraft:(approve|reject):\d+$")`
  registered before the generic dispatcher in `roost/bot/main.py`. The
  notification helper `guardian._notify_telegram_about_draft` uses sync
  `httpx.Client(timeout=5)` so it works from both sync MCP tool wrappers
  and async FastAPI endpoints, and is wrapped in a try/except so failures
  never block the gate. Also folds in a one-character fix missed from
  Phase 1A: the nurture callback regex in `main.py` now correctly
  dispatches `nedit:<id>` to `handle_nurture_callback` (was
  `^(napprove|nskip):\d+$`, now `^(napprove|nedit|nskip):\d+$`). Suite at
  761 green (was 749, +12).
- **FA-edition Phase 1B — Edit button on recipe drafts (WhatsApp /
  Chatwoot / WeChat).** Inbound messages that land in `awaiting_approval`
  now post a Telegram notification with inline ✅ Approve / ✏️ Edit /
  ⏭ Skip buttons (previously a text hint only: `/approve_<id>` /
  `/skip_<id>`). Tapping ✏️ Edit sends a `ForceReply` prompt seeded with
  the current `draft_output`; the operator's reply is captured by a new
  `handle_recipe_edit_reply` MessageHandler (registered at `group=-1`),
  persisted via new `recipes.apply_run_draft_edit(run_id, new_draft)`
  (writes `draft_output`), and the Approve/Edit/Skip keyboard is
  re-shown for one-tap send. `approve_run` is unchanged — it already
  reads `draft_output`, so the edited text flows through naturally.
  New helper `recipes.get_run(run_id)` returns one run by id.
  Approve / Edit-prompt / Edit-apply / Skip taps all write to the audit
  log via `roost.services.activity.log_action`. Inline-button surface
  added to all three `_notify_telegram` functions in
  `roost/extras/messaging_external/web/api_{whatsapp,chatwoot,wechat}.py`.
  Suite at 749 green (was 737, +12).
- **FA-edition Phase 1A — Edit button on cadence drafts.** Held nurture
  steps now ship with a third inline keyboard button (✏️ Edit) alongside
  ✅ Approve / ⏭ Skip. Tapping Edit sends a force-reply prompt seeded with
  the current draft body; the operator's reply is captured by a new
  `handle_nurture_edit_reply` MessageHandler (gated by
  `LEAD_NURTURE_ENABLED`, registered at `group=-1` so the agent catch-all
  doesn't also process it), persisted via new
  `nurture.apply_draft_edit(enrollment_id, body, subject=None)` as
  `body_override` / `subject_override` columns on `nurture_enrollments`,
  and the Approve/Edit/Skip keyboard is re-shown for one-tap send.
  `approve_pending` checks the overrides after `_build_message` and uses
  them in place of the template-rendered text when set. `_hold_for_approval`
  clears the override on each new hold so edits don't leak across steps.
  Every Approve / Edit-prompt / Edit-apply / Skip tap now writes to the
  audit log via `roost.services.activity.log_action`. Callback pattern in
  `roost/bot/main.py` extended to `^(napprove|nedit|nskip):\d+$`. Suite
  at 737 green (was 726, +11).
- **`audit_log` foundation — system-of-record for fast-path actions.** New
  service module `roost.services.activity` exposes a fire-and-forget
  `log_action(actor, action, entity_type=, entity_id=, ok=, result=,
  snippet=, actor_ref=)` writer plus `recent(limit, system_only=)` and
  `for_entity(entity_type, entity_id)` readers. Sits on top of the existing
  `activity_log` table — three new columns (`actor`, `ok`, `result_json`)
  added via idempotent migration. Task-coupled writers in
  `roost.services.tasks` keep their existing semantics; system writes leave
  `task_id` NULL. Two new MCP tools `audit_recent` / `audit_for_entity`
  (in a separate module from `tools_activity.py` so agents don't confuse
  the task-trail vs the system-trail). Productivity-stats counter in
  `stats_service.get_productivity_summary` updated to filter
  `task_id IS NOT NULL` so fast-path writes don't inflate the metric.
  First writers will be the FA-edition Telegram inline approval buttons
  (cadence drafts, guardian drafts, recipe drafts). Suite at 726 green
  (was 715, +11).
- **Schema-migration bug fix as a side effect.** The pre-existing Phase 12
  columns on `activity_log` (`tool_name`, `artifact_type`, `artifact_ref`)
  were declared in `_migrate_db`, which runs BEFORE `SCHEMA_V10` creates
  the table — on fresh databases the ALTERs silently no-op'd. Moved both
  the old and new column migrations into a dedicated
  `_migrate_activity_log_columns()` called after `SCHEMA_V10`. Idempotent
  on existing DBs; fresh DBs now get the full schema.

## [0.2.0] — 2026-06-02

The **FA (Financial Adviser) edition** release. Roost now ships as a turnkey
self-hosted stack fronted by Chatwoot, with WhatsApp / WeChat / Email flowing
through one customer-facing inbox and Telegram as the adviser's operator
surface. Includes the full FA-A → FA-K series, plus the unrelated quality
fixes accumulated since v0.1.0 (CRLF builds, default_vertical handling,
YAML question packs, conversation inbox on `/leads`, file uploads).

### Fixed
- **Lead-nurture dispatch handles `channel="chatwoot"`.** The Chatwoot inbound
  router stamps `channel="chatwoot"` on lead ingest (with the WhatsApp phone as
  identifier), but the cadence engine, qualification questionnaire, and the
  `/leads` reply box all hard-whitelisted `whatsapp / wechat / telegram` — so
  Chatwoot-sourced leads couldn't be replied to from any of them. Added a
  `chatwoot` branch to `qualification._do_send`, `nurture._dispatch_send`, and
  the `conversation.send_reply` whitelist. All three delegate to
  `whatsapp.send_text_message`, which since FA-G already routes through Chatwoot
  REST when `CHATWOOT_ENABLED=true` (FA edition stays on one dispatch path).
  The dispatcher reads both the Chatwoot return shape (`{ok, message_id, via,
  conversation_id}`) and the Meta-direct shape (`{messages: [{id}]}`) for the
  send-receipt id. Suite at 707 green. (FA-I)

### Added
- **MCP tool `chatwoot_list_templates(inbox_id=0)`.** Surfaces the WABA-approved
  WhatsApp templates that Chatwoot has synced from Meta — so an agent or recipe
  can pick a template name at runtime instead of hard-coding one that may have
  been retired. Lives in `roost/extras/messaging_external/mcp/tools_chatwoot.py`,
  gated by `CHATWOOT_ENABLED`, registered conditionally inside the bundle's
  `_register()`. Wraps `chatwoot.list_templates`, mapping the MCP-friendly
  `inbox_id=0` default to `None` so the service falls back to `CHATWOOT_INBOX_ID`.
  Three new tests cover happy path, env fallback, and the disabled-gate
  short-circuit. (FA-K)
- **Morning brief now includes Chatwoot inbox backlog (FA edition).** The 08:05
  Telegram digest used to be silent about the conversations actually sitting in
  the adviser's Chatwoot inbox — an awkward gap given Chatwoot is *the* customer
  surface in FA. Added two helpers to the chatwoot service:
  `conversation_meta(assignee_type="me")` (counts of open / resolved / pending /
  all from `GET /conversations/meta`) and
  `list_open_conversations(limit=5)` (top-of-inbox preview rows projected from
  `data.payload[].meta.sender` + last message). `build_summary` calls them under
  a `chatwoot` section that fails closed — flag off → empty section; REST
  unreachable → one-line "inbox unreachable" warning instead of crashing the
  digest. `format_summary` renders an inbox block with the open/pending counts
  and a preview list of the top conversations. Suite at 712 green. (FA-H)
- **FA edition defaults Telegram on as the operator surface.** Chatwoot is the
  customer-facing inbox; Telegram is where the *adviser* gets pinged — hot-lead
  alerts on inbound WhatsApp, `/nlist` Guardian draft approvals, and the morning
  `/briefing` digest all land in the adviser's personal Telegram. `env-templates/fa.env`
  flips `TELEGRAM_ENABLED=true` and adds `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS`
  sentinels. `docker-compose.fa.yml` adds `ENABLE_TELEGRAM=true` as a build-arg override
  so the FA image installs `python-telegram-bot` (base compose still ships it off).
  `scripts/install-fa.sh` prompts for both values via @BotFather (token) and
  @userinfobot (numeric user id) — skip-friendly: the entrypoint warns and keeps
  the rest of the stack up if either is blank. Existing FA hosts need a rebuild
  (`docker compose ... build roost && up -d`), not just a pull — see the
  "Already installed before 2026-06-02" callout in `docs/fa-laptop-install.md`. (FA-J)
- **All WhatsApp outbound routes through Chatwoot in FA edition.** Templates
  and media joined text on the Chatwoot REST path, so FA installs have one
  dispatch surface end-to-end. `send_template_message` now hits
  `chatwoot.route_template_to_whatsapp` — find-or-create contact, reuse open
  conv or open empty, then `POST /conversations/:cid/messages` with the
  `template_params: {name, category, language, processed_params}` payload
  Chatwoot's UI itself sends. Meta-style `components` get translated to
  Chatwoot's flat `processed_params` for the common body-variable case;
  header/button params fall back to plain refs. `send_document` / `send_image`
  with `path=` multipart-upload via `attachments[]` on the same endpoint;
  `link=` and `media_id=` still error cleanly since Chatwoot takes bytes,
  not URLs. New helper `chatwoot.list_templates(inbox_id)` surfaces
  approved templates from `GET /inboxes/:iid` (`message_templates` field)
  so callers don't hard-code names. `mark_as_read` stays a no-op (Chatwoot
  already owns inbound receipts on its inbox). Suite at 703 green. See
  `docs/chatwoot.md` § Outbound routing. (FA-G)
- **End-to-end Chatwoot round-trip test** (`tests/test_chatwoot_end_to_end.py`).
  Signs a financial-adviser-context `message_created`/`incoming` payload, POSTs
  it through the real FastAPI router with `CHATWOOT_ENABLED=true`, asserts the
  lead ingest call fires with `channel="chatwoot"` + FA message text, then
  calls `whatsapp.send_text_message` with a fake `httpx.Client` mounted on the
  service module — and asserts the captured HTTP traffic hits the right
  Chatwoot URLs (`/contacts/search`, `/contacts/77/conversations`,
  `/conversations/4242/messages`) with the `api_access_token` header and the
  reply body. Pins the FA-edition contract from webhook receipt to REST
  outbound in one test. Suite at 698 green. Operator smoke
  (`scripts/smoke_chatwoot.py`) covers the same path against a running stack
  using the real `CHATWOOT_WEBHOOK_SECRET` from `.env`. (FA-E)
- **FA-edition laptop install** (`scripts/install-fa.sh` +
  `docs/fa-laptop-install.md`). One command brings up Roost +
  Chatwoot 4.14.1 + Sidekiq + Postgres (pgvector) + Redis + a Tailscale
  Funnel sidecar that publishes Chatwoot at `https://<host>.<tailnet>.ts.net`
  for Meta to webhook into. The script auto-generates `SESSION_SECRET`
  (hex 32), `CHATWOOT_POSTGRES_PASSWORD` (hex 24), and
  `CHATWOOT_SECRET_KEY_BASE` (hex 64) — only when they still hold the
  `CHANGE_ME_*` sentinel, so re-runs are safe — prompts for
  `TAILSCALE_AUTHKEY` when interactive, creates the host bind-mount dirs
  (`data/`, `claude-auth/`, `gemini-auth/`, `codex-auth/`, `backups/`,
  `roost-config/`), pulls and starts the stack, waits up to 5 min for the
  Chatwoot healthcheck, then greps the Tailscale log to surface the Funnel
  URL. Bundles the FA compose overlays (`docker-compose.fa.yml` for
  laptop, `docker-compose.fa-vps.yml` for Caddy-fronted VPS),
  `env-templates/fa.env`, `tailscale/serve.json`, and the
  `scripts/roost-update.sh` snapshot-before-update wrapper. The runbook
  walks the manual Chatwoot wizard steps that can't be scripted: super-admin
  creation, WhatsApp Cloud inbox setup (Meta creds go into Chatwoot, not
  Roost's `.env`), Roost webhook registration with secret capture, and the
  Meta-side webhook pointing at Chatwoot. Base `docker-compose.yml`
  switched to a host bind-mount on `./data/` so updates can't accidentally
  blow away `roost.db` + RPA flow state. (FA-D)
- **Chatwoot adapter** (`messaging_external` bundle, sub-flag
  `CHATWOOT_ENABLED`). Lets Roost sit behind a self-hosted Chatwoot
  instance — Chatwoot fronts WhatsApp / WeChat / Email behind one queue,
  Roost reads HMAC-signed inbound webhooks (`/api/chatwoot/webhook`) and
  replies via REST. Outbound surface: `send_message`,
  `create_conversation`, `find_or_create_contact`, `mark_as_read`,
  `mark_as_resolved`. Only `message_created` + `incoming` runs the AI
  pipeline; `conversation_updated` (chatty) and outgoing/lifecycle events
  are ignored. Reference 4.14.1 payloads captured under
  `docs/chatwoot-webhook-samples/`. See `docs/chatwoot.md`. (FA-A)
- **WhatsApp outbound routes through Chatwoot in FA edition.** When
  `CHATWOOT_ENABLED=true`, `services/whatsapp.py::send_text_message`
  delegates to `chatwoot.route_text_to_whatsapp` (find-or-create contact,
  reuse open conversation or open a new one with the initial message).
  `send_template_message` and `send_document` / `send_image` return a
  clear error in FA edition (templates have no 1:1 Chatwoot mapping;
  media attachments are tracked as FA-B v2). `mark_as_read` becomes a
  no-op — Chatwoot owns inbound receipts on its inbox. Callers
  (lead-nurture cadences, MCP tools, RPA `whatsapp_send`) need no
  changes; the redirect happens inside the service. See
  `docs/chatwoot.md` § Outbound routing. (FA-B)

### Changed
- **Morning briefing no longer ships a hardcoded personal quote.** The `/briefing`
  command and the scheduled morning digest dropped the embedded inspirational
  line so the default install greets every operator neutrally. (Side effect: the
  digest's "Nothing urgent today" empty-day fallback now fires correctly, since
  the message no longer always starts with two lines.)
- **`env-templates/demo.env` defaults to username/password web login.** The Google
  and Microsoft OAuth client IDs are now blank in the demo template, so a fresh
  demo install presents the `WEB_USERNAME`/`WEB_PASSWORD` sign-in form instead of
  a "Sign in with Google" button nobody can use without real OAuth creds. Fill the
  client IDs back in to restore the OAuth buttons.

### Fixed
- **Container crashed on Windows clones (CRLF line endings).** `entrypoint.sh` was
  re-written with `\r\n` endings by `git core.autocrlf=true` on Windows checkouts,
  so the kernel tried to exec an interpreter named `bash\r` and the container died
  with exit 127 (`env: 'bash\r': No such file or directory`). Added a
  `.gitattributes` pinning shell/Python scripts to `eol=lf` (fixes it at checkout,
  including the host-side `scripts/install.sh`), plus a defensive `sed` CRLF strip
  on `entrypoint.sh` in the Dockerfile.
- **Telegram bot crash-looped when its package wasn't installed.** A build without
  the bot (`ENABLE_TELEGRAM=false`, the default) combined with `TELEGRAM_ENABLED=true`
  in the env made the entrypoint try to start the bot and hit
  `No module named 'telegram'`. The entrypoint now checks the package is importable
  first and skips with a clear message instead.
- **Inbound WhatsApp/WeChat leads ignored `default_vertical`.** Both webhooks
  hardcoded `vertical="property"` on lead ingest, so a new inbound always
  enrolled in `property_buyer_intro` regardless of the configured
  `default_vertical` (`roost-config/settings.yaml`). Now both read
  `settings.get("default_vertical", "property")`, so a financial-advisor
  install enrols inbound leads in `financial_advisor_intro` as expected.
- **Lead qualification stalled on YAML-only question packs.** `process_answer`
  resolved questions from the in-code `QUESTIONS_BY_CADENCE` dict instead of the
  YAML loader, so packs that exist only in YAML (e.g. `financial_advisor_intro`)
  never advanced past question 1. Now goes through `_get_pack()`, matching
  `start_qualification_if_needed`. The same dict-only lookup in the `/leads`
  dashboard (card progress + detail Q&A) is fixed too, so YAML-only packs now
  render their questions and the "N/M" progress chip.

### Added
- **Web file uploads.** A new `/files` page (Core nav) lets operators drag-drop
  or browse-upload reference files (CSVs, PDFs, images, spreadsheets) straight
  into `UPLOADS_DIR` — the same directory the Telegram bot and chat tools already
  read from, so uploads are immediately usable by the agent. New `/api/files`
  endpoints (list / upload / download / delete) backed by `services/uploads.py`
  with basename sanitisation, an uploads-dir containment guard, and a 25MB cap.
- **Human escape in qualification.** A lead who asks to speak to a person
  ("just call me", "talk to someone") is escalated to the operator — enrollment
  paused (`pause_reason="human_requested"`), hot alert fired with the ask quoted
  — instead of being marched through the rest of the questionnaire. Handled both
  on the opening inbound message and mid-questionnaire.
- **Customer conversation inbox on `/leads`.** Every inbound/outbound message is
  recorded per contact (new `lead_messages` table) and the lead detail panel now
  shows the full threaded conversation with a reply box that sends on the lead's
  own channel (WhatsApp / WeChat / Telegram). New `POST /api/leads/{id}/reply`
  endpoint and `conversation` service; outbound logging hangs off the single
  `_send_question` send chokepoint so questionnaire messages appear in the thread
  too.

## [0.1.0] — 2026-05-28

First tagged release. Marks the point where Roost moved from untracked rolling
development to versioned releases; everything below is the accumulated baseline.

### Added
- **Customer-side Telegram channel** — a customer-facing bot path separate from
  the operator allowlist (`TELEGRAM_ALLOWED_USERS`). Leads can DM the bot to
  establish a `chat_id`; operator commands are denied for non-allowlisted users;
  hand-off (`/takeover`, `/return_to_agent`) lets the human take a thread.
- **Multi-channel STOP/HELP parity** across WhatsApp, Telegram, and SMS — strict
  first-token match (uppercased, punctuation stripped), immediate exit via
  `exit_enrollments_by_contact`, `do_not_contact` enforcement with re-enrolment
  block. `mark_inbound` parity gives a uniform audit trail across channels.
- **SMS (Twilio) adapter** — inbound webhook + cadence dispatch channel, gated by
  its `*_ENABLED` flag (`docs/sms-adapter.md`).
- **Lead nurture bundle** — leads → cadences → dispatch → STOP/exit, with the
  `financial_advisor_intro`, `property_buyer_intro`, `generic_b2b`, and
  `framework_assessment` cadence templates (`docs/lead-nurture.md`).
- **Property-Agent, SME Ops, CRM, RPA, Messaging-external bundles** — vertical
  packages under `roost/extras/<name>/`, each toggled by a `<NAME>_ENABLED` flag.
- **Guardian** pre-flight gate for money-moving / irreversible actions, with a
  draft-approval queue surfaced on the web UI and Telegram (`/nlist`).
- **Three deployment shapes** — laptop Docker, hosted-by-you, and VPS+domain with
  Caddy auto-TLS (`docker-compose.public.yml`).
- **MCP server** exposing 300+ tools, web UI (FastAPI + Jinja2), and Telegram bot
  over the same engine.

[Unreleased]: https://github.com/crazyguy106/roost/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/crazyguy106/roost/releases/tag/v0.1.0
