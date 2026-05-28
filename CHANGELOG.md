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

### Fixed
- **Lead qualification stalled on YAML-only question packs.** `process_answer`
  resolved questions from the in-code `QUESTIONS_BY_CADENCE` dict instead of the
  YAML loader, so packs that exist only in YAML (e.g. `financial_advisor_intro`)
  never advanced past question 1. Now goes through `_get_pack()`, matching
  `start_qualification_if_needed`.

### Added
- **Human escape in qualification.** A lead who asks to speak to a person
  ("just call me", "talk to someone") is escalated to the operator — enrollment
  paused (`pause_reason="human_requested"`), hot alert fired with the ask quoted
  — instead of being marched through the rest of the questionnaire. Handled both
  on the opening inbound message and mid-questionnaire.

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
