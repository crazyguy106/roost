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

### Added
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
