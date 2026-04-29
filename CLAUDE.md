# Roost — Claude Instructions

Persistent agent workspace exposing 270+ MCP tools to Claude Code, plus a web UI and Telegram bot. This file is the project-level pointer; see `docs/` for deep dives.

## Architecture (one screen)

```
Telegram bot ─┐
Web UI       ─┼─► roost.web.app (FastAPI) ─► services/* ─► sqlite (data/roost.db)
MCP server   ─┘                                  │
                                                 └─► external: Gmail, Drive, MS Graph,
                                                       Notion, WhatsApp, IRAS rates,
                                                       PDPC DNC, ComplyAdvantage, Gemini
```

- **Services layer** (`roost/services/`) is the canonical place for business logic. Web/Telegram/MCP are thin adapters over it.
- **MCP tools** live under `roost/mcp/tools_*.py`, registered in `roost/mcp/server.py`. Each major capability has its own `tools_*` module.
- **Web** uses FastAPI + Jinja2 templates. Page routes in `roost/web/pages.py`, JSON APIs in `roost/web/api_*.py`.
- **RPA** (`roost/services/rpa_flows/`) is data-driven: YAML library files seeded into `rpa_flow_configs` on boot. Step ops live in `_interpreter.py` and are cross-checked against `schema.KNOWN_OPS` at import.

## Conventions

- **Feature flags** live in `roost/config.py` as `os.getenv` reads, mirrored in `roost/config_service.py::FEATURE_FLAGS` with metadata for the settings page.
- **Adapters fail closed.** A new external integration (WhatsApp, DNC, CDD, etc.) adds an `*_ENABLED` flag, gates the MCP tool with an `{"error": "..."}` early return, and ships an entry in `env-templates/`.
- **No mocks in DB tests.** Use the real SQLite via the `clean_*_table` fixtures.
- **Whole-string placeholders only** in RPA YAML (`"$param:KEY"`). Embedded form (`"AIA $param:KEY"`) is silently not substituted — pass full strings as params instead.
- **Docs live in `docs/`** as Markdown. New external-system integrations get their own `docs/<name>.md`.
- **Three deployment shapes** (laptop / hosted-by-you / VPS+domain) — see `docs/deployment.md`. The VPS+domain shape uses the bundled Caddy overlay at `docker-compose.public.yml` + `caddy/Caddyfile` + `env-templates/public-vps.env`; don't reinvent reverse-proxy plumbing.

## Key features

| Capability | Service | MCP module | Web | Doc |
|---|---|---|---|---|
| RPA browser automation | `services/rpa_flows/` + `browser_service.py` | `tools_rpa.py` | run viewer (planned) | `docs/rpa.md`, `docs/rpa-authoring.md`, `docs/rpa-for-users.md` |
| WhatsApp Cloud API | `services/whatsapp.py` | `tools_whatsapp.py` | `api_whatsapp.py` (webhook) | `docs/whatsapp-adapter.md` |
| WeChat Official Account | `services/wechat.py` | — | `api_wechat.py` | — |
| AI CDR pipeline | `services/cdr.py` | — | inbound message handlers | — |
| Charter system | `services/charter.py` | — | settings tab | — |
| **Property-Agent Toolkit (SG)** | `iras_stamp_duty.py`, `pdpc_dnc.py`, `cdd_screening.py` | `tools_iras.py`, `tools_pdpc.py`, `tools_cdd.py` | `/property-agent/*` | `docs/property-agent-toolkit.md` (bundle), `docs/aml-screening.md` (generic CDD), `docs/rpa-authoring.md` (`await_user_session`) |
| Recipes / SOP triggers | `services/recipes.py`, `sop_triggers.py` | `tools_recipes.py` | `api.py` | — |
| Guardian (pre-flight safety) | `services/guardian.py` | `tools_guardian.py` | settings tab | — |

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

239 tests at last count. RPA interpreter and library tests stub Playwright; the live smoke test against `the-internet.herokuapp.com` is documented in `docs/rpa-authoring.md` and not part of CI.

## When working in this repo

- Edit existing services before introducing new modules. Watch the `roost/services/` index to keep it coherent.
- Cross-check `_interpreter.HANDLERS` and `schema.KNOWN_OPS` after every RPA step-op change — there's an `assert` at import that fails fast if they drift.
- After config or schema changes, restart the MCP server (`/mcp` reconnect) and run `python3 -m pytest -q` before declaring done.
- Don't write new top-level docs unless asked — extend `docs/` and add a one-liner to README.md and this file.
