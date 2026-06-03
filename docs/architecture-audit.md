# Architecture Audit — Findings Log

A running ledger of structural / documentation / coverage gaps surfaced by
architecture walkthroughs. **Findings only** — not a triage plan, not a
fix list. Use it to decide what to investigate further, not as a TODO.

Each finding has:
- **ID** — stable `YYYY-MM-DD-NN` slug so PRs / commits can reference it.
- **Status** — one of:
  - `Verified` — agent's claim reproduced by direct repo inspection
  - `Partial` — the spirit is right, but the specifics need adjusting
  - `Unverified` — surfaced by an agent, not yet directly confirmed
  - `Suspected confab` — agent claim contradicted by direct inspection
  - `Resolved` — fixed in a referenced commit (link)
- **Severity** — `info` / `low` / `med` / `high`. Subjective; a starting
  point for the conversation, not a verdict.
- **Next step** — what verification or work would close it.

When a finding flips status (e.g. someone verifies an `Unverified` item),
edit the status line in place and add a one-line `**Updated YYYY-MM-DD:** …`
note. Don't rewrite history.

---

## 2026-06-03 — Top-down walkthrough (post-v0.3.0)

Methodology: four parallel Explore subagents covered core/infra,
web+bot, bundles, and mcp+tests+docs. Their reports were cross-checked
against direct `grep` / `ls` of the repo. Items below are everything
that survived that cross-check, plus things the cross-check itself
turned up.

### 2026-06-03-01 — CRM bundle absent from CLAUDE.md's "Doc" column
- **Status:** Resolved
- **Updated 2026-06-03:** CLAUDE.md row updated to point at `docs/crm-adapters.md`.
- **Severity:** low
- **Description:** `docs/crm-adapters.md` exists and covers the CRM
  bundle (Attio / Zoho / HubSpot / Pipedrive / Salesforce / local).
  But the bundle table in `CLAUDE.md` § "Key features" lists `—` for
  the CRM row's Doc column, so a reader (human or AI) following the
  index will conclude no doc exists.
- **Evidence:** `ls docs/crm-adapters.md` exists; CLAUDE.md bundle
  table row "**CRM** *(bundle)*" ends with `| — |`.
- **Next step:** Update the CRM row in CLAUDE.md to point at
  `docs/crm-adapters.md`. One-line edit.

### 2026-06-03-02 — `/template` Telegram command collision
- **Status:** Suspected confab
- **Severity:** info
- **Description:** One walkthrough subagent reported that `/template`
  is registered by both `bot/handlers/tasks.py` and
  `bot/handlers/recipes.py`. Direct grep for `"/template"` and
  `"template"` in both files returns no matches.
- **Evidence:** `grep -n '"/template"\|"template"' roost/bot/handlers/tasks.py roost/bot/handlers/recipes.py` → no output.
- **Next step:** None unless the original claim resurfaces. Leaving
  the finding so future audits don't re-discover and re-investigate.

### 2026-06-03-03 — No CI of any kind
- **Status:** Resolved
- **Updated 2026-06-03:** Added `.github/workflows/test.yml` (pytest -q on push/PR to main, Python 3.12) and `.github/workflows/mcp-inventory.yml` (regenerates `docs/mcp-inventory.md` and fails on drift — kills `-05`'s drift problem too). Status badges in README.
- **Severity:** med
- **Description:** Repo has no `.github/workflows/`, no `.gitlab-ci.yml`,
  no `Makefile`-driven CI target, no pre-push hook config. The test
  suite (811 green at v0.3.0) is only ever exercised by hand or by
  agents in the loop. Drift between `docs/mcp-inventory.md` and the
  actual tool set, between docs and code, and between schema and code
  is all caught by humans or not at all.
- **Evidence:** `ls .github/workflows/` → "No such file or directory".
- **Next step:** Decide whether single-developer + persistent-agent
  cadence is enough, or whether a minimal `pytest -q` GH Actions job
  is worth adding. Self-hosters' `git pull` story currently relies on
  the developer running tests before tagging.

### 2026-06-03-04 — Stale "Phase 1+" comment in `config.py`
- **Status:** Resolved
- **Updated 2026-06-03:** Rewrote the SME Ops section header to reflect current state — Zapier ingress + native Stripe/Shopify/Xero adapters, no "Phase 1+" promise.
- **Severity:** low
- **Description:** `roost/config.py:402` reads
  `# (XERO_ENABLED, SHOPIFY_ENABLED, etc.) will be added in Phase 1+.`
  but those flags are already defined and the bundles ship. The
  comment was true at some earlier checkpoint and survived the
  bundles being delivered.
- **Evidence:** `grep -n "Phase 1" roost/config.py` → line 402.
- **Next step:** Delete the line, or rewrite it as a section
  divider that reflects current state. One-line edit.

### 2026-06-03-05 — Hand-maintained `docs/mcp-inventory.md` with no drift check
- **Status:** Resolved
- **Updated 2026-06-03:** Original finding was a partial confab — `scripts/gen_mcp_inventory.py` already exists (since 2026-05-27) and walks `@mcp.tool()` decorators via AST to emit the doc. The walkthrough agent missed it. The doc *was* genuinely stale (330 → 333 tools), so the underlying drift was real. Fixed by (a) regenerating the inventory and (b) wiring `.github/workflows/mcp-inventory.yml` to fail on drift in CI (see `-03`).
- **Severity:** low-med

### 2026-06-03-06 — `roost/adapters/` and `roost/bot/adapters/` are unmapped
- **Status:** Resolved
- **Updated 2026-06-03:** Split the finding by investigation. The four `roost/adapters/{discord,slack,signal,matrix}_bot.py` modules are real working standalone-process adapters, env-flagged in `config.py` and already in `env-templates/gemini-minimal.env` — they just lacked documentation. Wrote `docs/messaging-adapters.md`, added a row to CLAUDE.md "Key features" + a link in README. The two `roost/bot/adapters/{dingtalk,feishu}.py` files were 43-line stubs untouched since 2026-04-12 (no imports, no flags, no tests, no docs) — deleted along with the now-empty `roost/bot/adapters/` package.
- **Severity:** med
- **Description:** Six adapter modules exist outside the bundle
  registry and outside the documented architecture:
  - `roost/adapters/discord_bot.py`
  - `roost/adapters/matrix_bot.py`
  - `roost/adapters/signal_bot.py`
  - `roost/adapters/slack_bot.py`
  - `roost/bot/adapters/dingtalk.py`
  - `roost/bot/adapters/feishu.py`

  None are mentioned in `README.md`, `CLAUDE.md`, the bundle table,
  `env-templates/`, or `docs/`. Their wiring status (entry-pointed?
  feature-flagged? dead code?) was not investigated in this walkthrough.
- **Evidence:** `ls roost/adapters/` and `ls roost/bot/adapters/` —
  outputs above.
- **Next step:** Targeted investigation — for each file: is it
  imported anywhere outside its own package? Does a feature flag
  gate it? Does it have tests? The answer determines whether each
  goes (a) into the bundle pattern under `roost/extras/`, (b) gets a
  doc + env-template entry, or (c) gets deleted.

### 2026-06-03-07 — CRM bundle has no provider-level tests
- **Status:** Resolved (partial — local + contract; vendor-mock tests deferred)
- **Updated 2026-06-03:** Added `tests/test_crm_local.py` (23 tests against the real SQLite-backed LocalProvider) and `tests/test_crm_contract.py` (12 tests asserting all 6 providers — local, attio, hubspot, zoho, salesforce, pipedrive — subclass `CrmProvider`, instantiate (i.e. no missing `@abstractmethod`s), set `name`, and return a well-shaped `test_connection()` dict instead of leaking vendor exceptions). The contract test caught and fixed a real bug: `LocalProvider.log_communication()` was calling `comms_svc.create_communication()`, which doesn't exist (correct name: `log_communication`). Vendor-mock integration tests for Attio/HubSpot/Zoho/Salesforce/Pipedrive (using `respx` or VCR) deferred — would test SDK wire-format separately and is a wider scope.
- **Severity:** med
- **Description:** The CRM bundle ships 12 MCP tools across 6
  provider adapters (Attio / Zoho / HubSpot / Pipedrive / Salesforce /
  local). Test coverage is `tests/test_attio_webhook.py` (Attio
  webhook ingestion only) and incidental references via
  `tests/test_leads.py` (lead nurture's CRM dependency). No
  per-provider unit tests, no `test_crm.py`. The "Partial" status
  reflects that *some* CRM code is exercised, but five providers and
  the `crm.base` abstraction are not.
- **Evidence:** `ls tests/ | grep -iE "crm|attio|zoho|hubspot|pipedrive|salesforce"` returns only `test_attio_webhook.py`.
- **Next step:** Decide which providers are tier-1 (need real
  fixtures + mock-server tests) vs tier-2 (smoke test only). The
  `crm.base` abstraction is the highest-leverage place to start —
  a contract test there would protect all six providers at once.

### 2026-06-03-08 — `env-templates/demo.env` doesn't pin SME sub-flags
- **Status:** Resolved
- **Updated 2026-06-03:** Added a "SME Ops bundle" section that pins `SME_OPS_ENABLED`, `ZAPIER_ENABLED`, `STRIPE_ENABLED`, `SHOPIFY_ENABLED`, `XERO_ENABLED` all `=true` with `CHANGE_ME` placeholders for the matching credentials. Also closes the related drift where the L79 comment promised `SME_OPS_ENABLED` was "set below" but it wasn't set anywhere.
- **Severity:** low
- **Description:** `demo.env` sets master flags
  (`PROPERTY_AGENT_ENABLED`, `SME_OPS_ENABLED`) but does not pin the
  SME sub-flags (`STRIPE_ENABLED`, `SHOPIFY_ENABLED`, `XERO_ENABLED`).
  A walkthrough operator following `demo.env` will get the SME
  bundle's pages and MCP tools registered but with each adapter
  defaulting to whatever `config.py` has as the env default. If the
  demo wants to walk through a specific surface (e.g. Stripe), it
  isn't guaranteed to be on.
- **Evidence:** `grep "STRIPE_ENABLED\|SHOPIFY_ENABLED\|XERO_ENABLED" env-templates/demo.env` returns nothing; only "PROPERTY_AGENT_ENABLED and SME_OPS_ENABLED are set below" matches "ENABLED" in that file.
- **Next step:** Add explicit `STRIPE_ENABLED=true` /
  `SHOPIFY_ENABLED=true` / `XERO_ENABLED=true` (or `=false`,
  whichever the demo script wants) so the demo state is reproducible.

### 2026-06-03-09 — `docs/build-audit.md` is not wired into anything
- **Status:** Resolved (rolled into `-03`)
- **Updated 2026-06-03:** `-03` is now closed by `.github/workflows/mcp-inventory.yml`, which is the same drift-detection job this finding asked for, scoped to the highest-churn doc. If we want `tools_audit`'s wider docs-vs-code checks to run too, that's a small follow-up: add another step to the same workflow invoking `tools_audit`. Closing this entry as "not separately actionable".
- **Severity:** low (subsumed by 2026-06-03-03)
- **Description:** `docs/build-audit.md` describes a docs-vs-code
  drift detector and `tools_audit.py` exposes 2 MCP tools for it,
  but nothing in CI runs the audit, so drift it's designed to catch
  goes uncaught.
- **Evidence:** No CI (see 2026-06-03-03); no pre-commit hook
  config; `tools_audit.py` is invokable only when an agent or the
  developer manually calls the MCP tool.
- **Next step:** Either run `tools_audit` from a scheduled job
  (`roost.bot.scheduler` already has hooks) and surface failures via
  Guardian, or accept that the audit is operator-driven and remove
  the implication that it's automated.

### 2026-06-03-10 — `docker-compose.override.yml` ships in-tree
- **Status:** Resolved
- **Updated 2026-06-03:** Added a 7-line header comment pointing at `docs/container-ssh-access.md` and the CLAUDE.md "SSH-into-Claude shortcut" section so the convention break is self-documenting.
- **Severity:** info
- **Description:** Convention in most Compose repos is that
  `docker-compose.override.yml` is per-machine and `.gitignore`d.
  Roost's checks in. That's intentional per CLAUDE.md (the SSH-into-
  Claude shortcut needs `AUDIT_WRITE` and bind-mounts that survive
  recreate), but a contributor reading idiomatic Compose conventions
  will trip on it. Worth a comment in the override file itself.
- **Evidence:** `git ls-files | grep override` shows the file is
  tracked; CLAUDE.md § "SSH-into-Claude shortcut" explains why.
- **Next step:** Add a 3-line header comment to the override file
  pointing at CLAUDE.md so the convention break is self-documenting.

### 2026-06-03-11 — Walkthrough counts are not CI-verified
- **Status:** Verified (meta)
- **Severity:** info
- **Description:** The architecture walkthrough that produced this
  log reports e.g. "35 `.py` files in `roost/services/`", "~88
  boolean flags in `config.py`", "~260 MCP tools across ~41
  `tools_*.py` files". These are agent-reported and were not
  cross-checked by a script. Treat the synthesis's numbers as ±,
  not as a manifest.
- **Evidence:** This document, the agents' summaries.
- **Next step:** None for the audit log itself. If we ever want a
  reliable inventory we'd write the `scripts/generate-mcp-inventory.py`
  envisioned in 2026-06-03-05 and let it cover the rest too.

---

## How to add a new walkthrough

1. Create a new `## YYYY-MM-DD — <one-line title>` section above the
   previous one.
2. State the methodology in 1-2 sentences (was it agent-driven? full
   manual? a specific bundle's deep-dive?).
3. New findings get fresh IDs in that section's date prefix.
4. If a new walkthrough revisits an existing finding, **update the
   existing entry's status line in place** and add a
   `**Updated YYYY-MM-DD:** …` note. Don't duplicate the finding
   under the new date.
5. Don't expand findings into investigation notes here — that goes
   in the commit / PR that resolves them. This file is a ledger, not
   a workbook.
