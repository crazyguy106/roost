# Roost — Claude Instructions

Persistent agent workspace exposing 300+ MCP tools to Claude Code (see [`docs/mcp-inventory.md`](docs/mcp-inventory.md) for the canonical generated list), plus a web UI and Telegram bot. This file is the project-level pointer; see `docs/` for deep dives.

Roost is **domain-agnostic**. Vertical bundles (currently: **Property-Agent** for SG salespersons, **SME Ops** for small/medium businesses) are services + RPA library files + MCP tool modules layered on top of the same engine — they're examples of what the platform can be configured into, not core to it.

## Architecture (one screen)

```
Telegram bot ─┐
Web UI       ─┼─► roost.web.app (FastAPI) ─► services/* ─► sqlite (data/roost.db)
MCP server   ─┘                                  │
                                                 └─► external: Gmail, Drive, MS Graph,
                                                       Notion, WhatsApp, Gemini,
                                                       Zapier (universal SME bridge),
                                                       domain APIs (e.g. IRAS rates,
                                                       PDPC DNC, ComplyAdvantage)
```

- **Core services** (`roost/services/`) hold cross-cutting business logic (tasks, projects, guardian, recipes, telegram linking, …). Web/Telegram/MCP are thin adapters over it.
- **Vertical bundles** live under `roost/extras/<name>/`, each a self-contained package with `services/`, `mcp/`, `web/`, `templates/`, and a `BUNDLE` descriptor in `__init__.py`. A bundle's master flag (`<NAME>_ENABLED`) toggles import — disabled bundles cost zero LoC, zero MCP tools, and zero web routes. Registry in `roost/extras/__init__.py::load_enabled()`. Current bundles: `property_agent`, `sme_ops`, `crm`, `rpa`, `lead_nurture`, `messaging_external`.
- **Per-bundle DB schema.** Bundle-owned tables ship as `schema_sql()` on the `Bundle` descriptor and are applied (idempotently) at boot via `run_bundle_schemas()` — regardless of master flag, so tests work without env wiring. Cross-cutting tables (e.g. `guardian_drafts`) stay in `roost/database.py`.
- **MCP tools.** Core tools under `roost/mcp/tools_*.py` registered in `roost/mcp/server.py`. Bundle tools under `roost/extras/<name>/mcp/tools_*.py`, imported lazily by the bundle's `_register()`.
- **Web.** FastAPI + Jinja2. Core page routes in `roost/web/pages.py`, JSON APIs in `roost/web/api_*.py`. Bundle pages/APIs live under `roost/extras/<name>/web/`; bundle templates are added to the Jinja `ChoiceLoader` per-app-create.
- **RPA** (`roost/extras/rpa/services/rpa_flows/`) is data-driven: YAML library files seeded into `rpa_flow_configs` on boot. Step ops live in `_interpreter.py` and are cross-checked against `schema.KNOWN_OPS` at import.

## Conventions

- **Feature flags** live in `roost/config.py` as `os.getenv` reads, mirrored in `roost/config_service.py::FEATURE_FLAGS` with metadata for the settings page.
- **Adapters fail closed.** A new external integration (WhatsApp, DNC, CDD, etc.) adds an `*_ENABLED` flag, gates the MCP tool with an `{"error": "..."}` early return, and ships an entry in `env-templates/`.
- **Money-moving writes go through Guardian.** Any new MCP tool that triggers a customer-visible or financially irreversible side effect (refunds, order cancellations, authorised invoices, payouts) must call `guardian_gate(name, args)` first and short-circuit when it returns a `pending_approval` dict. Add the tool name to `_MONEY_MOVING_TOOLS` and register an executor in `_EXECUTORS` in `services/guardian.py`. Do not add a `confirm=True` agent-flippable bypass.
- **No mocks in DB tests.** Use the real SQLite via the `clean_*_table` fixtures.
- **Whole-string placeholders only** in RPA YAML (`"$param:KEY"`). Embedded form (`"AIA $param:KEY"`) is silently not substituted — pass full strings as params instead.
- **Docs live in `docs/`** as Markdown. New external-system integrations get their own `docs/<name>.md`.
- **Three deployment shapes** (laptop / hosted-by-you / VPS+domain) — see `docs/deployment.md`. The VPS+domain shape uses the bundled Caddy overlay at `docker-compose.public.yml` + `caddy/Caddyfile` + `env-templates/public-vps.env`; don't reinvent reverse-proxy plumbing.
- **SSH-into-Claude shortcut lives in `docker-compose.override.yml`, not the base compose.** The override loopback-binds `:2222`, adds `cap_add: [AUDIT_WRITE]` (mandatory — sshd's PAM session writes an audit record and tears down the session without it), and bind-mounts `ssh/authorized_keys`, `ssh/sshd_config.d/`, `ssh/bash_profile` so the ephemeral container `/home/dev` survives recreate. `cap_add` and volume changes need `docker compose up -d` (recreate), not `restart`. Full doc: `docs/container-ssh-access.md`.

## Key features

Core capabilities live under `roost/services/`, `roost/mcp/`, `roost/web/`. Vertical bundles live under `roost/extras/<name>/` (gated by `<NAME>_ENABLED`).

| Capability | Service | MCP module | Web | Doc |
|---|---|---|---|---|
| **RPA** *(bundle)* | `extras/rpa/services/{rpa_flows/,browser_service.py,rpa_runs.py}` | `extras/rpa/mcp/tools_rpa.py` | `/rpa` run viewer + `api_rpa.py` + `api_sidecar.py` | `docs/rpa.md`, `docs/rpa-authoring.md`, `docs/rpa-for-users.md` |
| **Messaging external** *(bundle)* — WhatsApp / WeChat / SMS / AI CDR | `extras/messaging_external/services/{whatsapp,wechat,sms,ai_cdr}.py` | `extras/messaging_external/mcp/tools_whatsapp.py` | `extras/messaging_external/web/{api_whatsapp,api_wechat}.py` | `docs/whatsapp-adapter.md`, `docs/sms-adapter.md` |
| Charter system | `services/charter.py` | — | settings tab | — |
| **Property-Agent Toolkit (SG)** *(bundle)* | `extras/property_agent/services/{iras_stamp_duty,pdpc_dnc,cdd_screening}.py` | `extras/property_agent/mcp/{tools_iras,tools_pdpc,tools_cdd}.py` | `/property-agent/*` | `docs/property-agent-toolkit.md`, `docs/aml-screening.md`, `docs/rpa-authoring.md` |
| **SME Ops** *(bundle)* | `extras/sme_ops/services/{zapier,stripe,shopify,xero,xero_oauth}.py` | `extras/sme_ops/mcp/{tools_stripe,tools_shopify,tools_xero}.py` | `/sme/{sync-status,orders,cashflow}`, `/api/{zapier,stripe,shopify,xero}/*` | `docs/sme-ops.md` |
| **CRM** *(bundle)* — Attio/Zoho/Pipedrive/HubSpot/Salesforce/local | `extras/crm/services/*` | `extras/crm/mcp/tools_crm.py` | `extras/crm/web/{api_crm,api_attio_webhook,auth_zoho}.py` | — |
| **Lead nurture** *(bundle)* | `extras/lead_nurture/services/{leads,nurture,lead_pipeline,cadences/}` | `extras/lead_nurture/mcp/{tools_leads,tools_lead_pipeline}.py` | `extras/lead_nurture/web/api_leads.py` (+ Attio webhook from CRM bundle) | `docs/lead-nurture.md` |
| Recipes / SOP triggers | `services/recipes.py`, `sop_triggers.py` | `tools_recipes.py` | `api.py` | — |
| Guardian (pre-flight safety + draft queue) | `services/guardian.py` (incl. `guardian_drafts` table, `guardian_gate`, `approve_draft`, `reject_draft`) | `tools_guardian.py` (+ sme_ops money-moving tools route through the gate) | settings tab; pending-drafts card on `/sme/sync-status`; `extras/sme_ops/web/api_sme_drafts.py` (`/api/sme/drafts/*`) | — |
| Daily summary (Telegram) | `services/daily_summary.py` | — | `bot/handlers/daily_summary.py` + scheduler tick | `docs/daily-summary.md` |

## Property-Agent Toolkit — quick orientation

Built for Singapore CEA-registered salespersons. Each component maps to a named regulatory obligation:

- `iras_calc_*` — Stamp Duties Act, post-27-Apr-2023 ABSD/SSD/lease rates. Pure Python, always available.
- `pdpc_dnc_check` — PDPA s.43 / Spam Control Act. Gated by `DNC_ENABLED`.
- `cdd_screen` — CEA PC 01-21 / 02-23 (AML/CFT). Vendor-agnostic, ComplyAdvantage built. Gated by `CDD_ENABLED`.
- `library/hdb_eip.yaml` — HDB EIP/SPR quota check (public eService, no auth).
- `await_user_session` step op — Singpass-assisted RPA pattern; flow waits while user logs in via the chromium sidecar, then resumes.

Full reference: `docs/property-agent-toolkit.md`.

## Testing

```
python3 -m pytest -q     # full suite
```

572 tests at last count (2026-05-27). RPA interpreter and library tests stub Playwright; the live smoke test against `the-internet.herokuapp.com` is documented in `docs/rpa-authoring.md` and not part of CI.

## Versioning

Roost follows [Semantic Versioning](https://semver.org/). Pre-1.0, **MINOR** = new
feature, **PATCH** = fix; backward-incompatible changes are flagged `### Breaking`.

- **Single source of truth:** `roost/__init__.py::__version__`. `setup.py` reads it
  at build time — never hardcode a version in two places.
- **`CHANGELOG.md`** is [Keep a Changelog](https://keepachangelog.com/) format. Add
  every user-facing change under `## [Unreleased]` as you land it (don't batch at
  release time — the entry rots if you wait).
- **Cutting a release:** move the `[Unreleased]` items into a new dated
  `## [X.Y.Z] — YYYY-MM-DD` section, bump `__version__`, commit as
  `release: vX.Y.Z`, then annotated-tag: `git tag -a vX.Y.Z -m "vX.Y.Z"`.
- **What bumps the version:** a new bundle, a new MCP tool surface, a new external
  adapter, a changed STOP/HELP keyword set, or a schema change. A docs-only or
  test-only commit does not.
- **Self-hosters `git pull` against `main`** (the workshop install scripts clone it
  directly), so the changelog and tags are how they decide whether an upgrade is
  safe. Treat them as a real audience.

## When working in this repo

- Edit existing services before introducing new modules. Watch the `roost/services/` and `roost/extras/<bundle>/services/` indexes to keep them coherent.
- New vertical capability? Add a bundle under `roost/extras/<name>/` with its own `BUNDLE` descriptor and master `<NAME>_ENABLED` flag; don't add to core `services/`. Cross-bundle imports must be lazy (inside handler functions) so master-flag independence holds.
- Cross-check `_interpreter.HANDLERS` and `schema.KNOWN_OPS` after every RPA step-op change — there's an `assert` at import that fails fast if they drift.
- After config or schema changes, restart the MCP server (`/mcp` reconnect) and run `python3 -m pytest -q` before declaring done.
- Don't write new top-level docs unless asked — extend `docs/` and add a one-liner to README.md and this file.

## Product strategy

- [`docs/agentic-workflow-strategy.md`](docs/agentic-workflow-strategy.md) — strategy memo for Roost's own agentic surface (Plan-Approve-Execute, live file ops, streaming tool calls). Read before working on the `/agentic` route, plan-display, streaming tool events, or any verticalisation of the chat surface. Not yet a build spec — direction only.
