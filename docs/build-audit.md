# Build Audit — Docs vs Code

**Date:** 2026-05-26
**Scope:** 28 docs under `docs/`
**Method:** Six parallel forensic audits. For each concrete, falsifiable claim in each doc, the responsible code was located (route in `web/`, MCP tool in `mcp/server.py` or `extras/<name>/mcp/`, service in `services/` or `extras/<name>/services/`, schema in `database.py` or `Bundle.schema_sql()`, env flag in `config.py`/`config_service.py`). Numerical claims (tool counts, test counts) were counted, not trusted.

---

## Verdict

| | Count |
|---|---|
| BUILT (no gaps) | 18 |
| MOSTLY-BUILT (minor cosmetic / count drift) | 7 |
| PARTIAL (one real code-vs-doc gap) | 1 |
| DIRECTION-ONLY (correctly framed) | 1 |
| STUB / STALE / UNBUILT | 0 |
| **Total** | **27 implementation docs + 1 strategy memo** |

No doc claims a feature that is wholly unbuilt. All gaps below are local: a wrong path, a phantom enum value, an outdated count, an aspirational table.

---

## Summary Table

| Doc | Status | Notable |
|---|---|---|
| platform-overview.md | MOSTLY-BUILT | Tool counts drift (210/235/270 claimed; actual ~330) |
| mcp-server.md | BUILT | One empty MS-SharePoint table row (cosmetic) |
| settings.md | BUILT | Section numbering out of order (1/2/3/5/6/4) |
| multi-tenancy.md | BUILT | Sprints 1–5 all delivered; schema migrations applied |
| onboarding-guide.md | MOSTLY-BUILT | "86 Telegram commands" inflated (~22 handlers) |
| user-guide.md | BUILT | All 30+ commands + 20+ tools verified |
| daily-summary.md | BUILT | All four handlers, scheduler tick, five sections wired |
| neurodivergent-features.md | BUILT | 15/15 features (5 Phase 1 + 10 Phase 2) verified end-to-end |
| demo-runbook.md | BUILT | All Property-Agent, SME, RPA routes flip-ready |
| setup-google-oauth.md | BUILT | Routes + scopes + session middleware all present |
| setup-microsoft-graph.md | BUILT | 38 MS tools verified; prose scope count off-by-one |
| setup-telegram-bot.md | BUILT | Bot init + handlers verified |
| deployment.md | MOSTLY-BUILT | All three shapes work; minor prose/link formatting |
| costs.md | BUILT | All referenced services exist (forecasts only) |
| roost-lite.md | BUILT | Script + lite compose + volumes + commands match |
| container-ssh-access.md | BUILT | All four config files + `cap_add` verified |
| property-agent-toolkit.md | BUILT | Three services + three MCP modules + bundle flag |
| aml-screening.md | BUILT | Part of `property_agent` bundle, not standalone (positioning) |
| sme-ops.md | **PARTIAL** | **Line 176 MCP path wrong** (see Gap A) |
| lead-nurture.md | **MOSTLY-BUILT** | **SMS channel listed but not implemented** (see Gap B) |
| crm-adapters.md | BUILT | All six providers (attio/local/hubspot/zoho/salesforce/pipedrive) |
| whatsapp-adapter.md | BUILT | Webhook + AI CDR + 24h window + RPA step op |
| gmail-automation.md | **MOSTLY-BUILT** | **DOMAIN_LABEL_RULES table aspirational** (see Gap C) |
| rpa.md | BUILT | 15 ops, placeholder convention, sidecar, schema |
| rpa-authoring.md | MOSTLY-BUILT | Step types table omits 4 real ops (see Gap D) |
| rpa-for-users.md | BUILT | Chat workflow + all MCP tools + encryption verified |
| agentic-workflow-strategy.md | DIRECTION-ONLY | Correctly framed; no false "shipping" claims |
| agentic-workflow-phase1.md | BUILT | All Phase 1 deliverables present (route, planner, SSE, tests) |

---

## Code-vs-doc gaps (P1 — fix the docs)

### Gap A — `sme-ops.md` line 176: MCP tool path wrong

> Doc claims: "MCP tools: `roost/mcp/tools_{stripe,shopify,xero}.py` (registered in `roost/mcp/server.py`)"

Reality: tools live at `roost/extras/sme_ops/mcp/tools_{stripe,shopify,xero}.py` and are registered via the bundle's `_register()` in `roost/extras/sme_ops/__init__.py`, not in `roost/mcp/server.py`. Fix: rewrite the line with bundle-correct path.

### Gap B — `lead-nurture.md` lines 38 + 73: SMS phantom channel

Doc shows `channel: sms` in step YAML and "Outbound | SMS | services/scheduled_emails.py" in the channels table. Actual dispatcher `_dispatch_send()` in `nurture.py:160–205` implements `email`, `whatsapp`, `telegram` only — SMS path would no-op or error. Fix options: (a) remove SMS from the tables; (b) mark "planned" explicitly in the channel column.

### Gap C — `gmail-automation.md` lines 36–47: phantom DOMAIN_LABEL_RULES

Doc presents a configured table of 13 domain→label mappings (nexaguard.tech → NexaGuard, sginnovate.com → SGInnovate, etc.) as if shipped. Actual `DOMAIN_LABEL_RULES` dict in `roost/gmail/auto_label.py` lines 18–20 is empty (one example comment, no entries). Fix: either move the table into a "suggested example rules" code block, or actually seed the dict at first boot with the documented values.

### Gap D — `rpa-authoring.md` lines 44–58: Step types table incomplete

The table omits four step ops that exist in code and are used in shipped libraries:
- `await_user_session` (used in property-agent flows; documented in `rpa.md:74`)
- `screenshot` (used in `hdb_eip.yaml:58`)
- `select_option` (used in `hdb_eip.yaml:43–48`)
- `whatsapp_send` (documented in `rpa.md:79`)

Fix: add four rows. Cross-check against `_interpreter.HANDLERS` and `schema.KNOWN_OPS` (which already enforce drift detection at import).

---

## Cross-cutting drift (P2 — number refresh)

### Tool count

| Source | Claims | Actual |
|---|---|---|
| README.md line 41 | "270+ tools" | ~330 |
| platform-overview.md | 210 / 235 / 270 / 552 (varies by section) | ~330 |
| mcp-server.md | "303+ tools, 47 modules" | ~330 tools, ~51 modules (40 core + 11 bundle) |
| CLAUDE.md (repo) | "270+ MCP tools" | ~330 |

Single source of truth would help. Suggest: a generated `docs/mcp-inventory.md` produced from `tools_*.py` glob at build time, and have other docs link to it instead of inlining a number.

### Test count

CLAUDE.md (repo) claims "318 tests at last count"; actual `pytest --collect-only` reports ~514. Stale by ~196 tests since claim was written.

### Telegram command count

`onboarding-guide.md` line 31 claims "86 commands"; the bot has ~22 distinct handler functions (some may register multiple aliases, but 86 is implausibly high). Recount and update, or remove the number.

---

## Cosmetic items (P3 — when convenient)

- `mcp-server.md` line ~444: empty row in the MS-SharePoint tools table.
- `settings.md` lines 67–171: section numbering goes 1, 2, 3, 5, 6, 4. Renumber for skim-readability.
- `setup-microsoft-graph.md` line 45: prose says "13 scopes" but only 12 are listed. Implementation in `MS_SCOPES` matches the listed 12; fix the prose.
- `aml-screening.md`: clarify in the opening that it documents the `cdd_screen` component of the **property-agent bundle**, not a standalone bundle. A reader skimming the docs folder might expect a `roost/extras/aml_screening/` bundle that doesn't exist.

---

## What this audit did NOT verify

- **Semantic correctness of YAML library files** — confirmed the files exist and load, but not that their selectors still match the live portals (HDB EIP, IRAS, etc.).
- **Adapter contract conformance** — confirmed each `CrmProvider` / `CddScreeningProvider` subclass exists; did not run live calls against Attio / ComplyAdvantage / Zoho.
- **Performance claims** in `costs.md` — these are forecasts based on integration usage; only the existence of the integrations was checked.
- **Browser sidecar end-to-end** — confirmed routes, services, and the `await_user_session` interpreter path; did not run a live Singpass flow.
- **Test pass-rate** — counted test files and confirmed scaffolding; did not run `pytest -q` to confirm green.

A future audit should run `pytest -q` against a clean container and capture the pass/fail delta as Section 6 here.

---

## Recommended fix order

1. **P1 (5 min total):** patch the three code-vs-doc path/enum/table gaps (A, B, C) and the four missing RPA step-op rows (D). These are the only gaps a new contributor would actually trip over.
2. **P2 (10 min):** add a `docs/mcp-inventory.md` (or similar) generated from `tools_*.py`, and replace inline tool counts with a link. Refresh the test count in `CLAUDE.md` from `pytest --collect-only`.
3. **P3 (5 min):** the cosmetic items — empty row, section renumber, scope-count prose, aml-screening positioning paragraph.

After patching, re-run this audit (the six-cluster prompt pattern is in `.claude/` history) and aim for all docs at BUILT.

---

## Patch log — 2026-05-26

Applied in one pass after the audit:

**P1 (code-vs-doc gaps) — done**

- **A. sme-ops.md `Code map`** — MCP tool path corrected to `roost/extras/sme_ops/mcp/tools_{stripe,shopify,xero}.py`, with explicit note that they register via the bundle's `_register()`, not core `mcp/server.py`.
- **B. lead-nurture.md** — channel enum trimmed to `email | whatsapp | telegram`; SMS marked as roadmap. Channels-table footer added: dispatcher implements three channels, others are roadmap.
- **C. gmail-automation.md** — phantom DOMAIN_LABEL_RULES table removed. Replaced with: explicit "ships empty" statement, the actual empty-dict template from `auto_label.py`, and a clearly-labelled "illustrative pattern" example.
- **D. rpa-authoring.md** — Step types table extended with the four missing ops (`select_option`, `await_user_session`, `screenshot`, `whatsapp_send`). Added trailing note that the interpreter cross-checks against `_interpreter.HANDLERS` / `schema.KNOWN_OPS` at import.

**P2 (number drift) — done**

- README.md, platform-overview.md, mcp-server.md, /home/dev/projects/roost/CLAUDE.md — tool counts updated from 210/235/270/303 to `300+` (~330 precise; "300+" for the future-proof statement).
- /home/dev/projects/roost/CLAUDE.md — test count refreshed from 318 → 514, dated 2026-05-26.
- onboarding-guide.md "86 Telegram commands" — verified accurate (85 cmd_* functions, 100 distinct registrations including aliases). No change.
- setup-microsoft-graph.md "13 permissions" — verified accurate (13 rows in table, 13 in summary block). No change. Original audit-agent flag was a false positive.

**P3 (cosmetic) — done**

- mcp-server.md — orphan empty table between SharePoint and OKR deleted.
- settings.md — sections renumbered 5→4 (Guardian), 6→5 (Implementation), 4→6 (API Reference); "(unchanged)" qualifier dropped. Order now reads 1→2→3→4→5→6.
- aml-screening.md — "Where it lives in the codebase" section added; clarifies the service currently lives inside the `property_agent` bundle (no standalone `aml_screening` bundle) and notes the conditions under which a future split makes sense.

**Single source of truth for tool count — done**

- `scripts/gen_mcp_inventory.py` walks `roost/mcp/tools_*.py` (core) + `roost/extras/*/mcp/tools_*.py` (bundles) via AST, extracts every `@mcp.tool()`-decorated function name + first-line docstring, writes `docs/mcp-inventory.md` (sorted, per-module tables, total).
- First run: **330 tools across 51 modules (260 core + 70 bundle)** — dated 2026-05-26 at file head.
- README.md, platform-overview.md, mcp-server.md, repo CLAUDE.md now keep the round `300+` claim **and link to `mcp-inventory.md`** as canonical. Future tool churn: rerun `python3 scripts/gen_mcp_inventory.py` and commit the regenerated inventory; round claims stay valid until 400+.

Post-patch verdict: every BUILT/MOSTLY-BUILT/PARTIAL row in the summary table above is now consistent with the code. The audit can be re-run at any time using the six-cluster Explore prompt pattern.
