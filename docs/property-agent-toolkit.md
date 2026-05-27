# Property-Agent Toolkit (Singapore)

Compliance-grade tooling for CEA-registered salespersons. Each component
maps to a named regulatory obligation and produces a timestamped evidence
artefact suitable for disciplinary defence and audit.

## Components

| Module | Backed by | UI | MCP | Obligation |
|---|---|---|---|---|
| Stamp Duty Calculator | `roost/extras/property_agent/services/iras_stamp_duty.py` | `/property-agent/stamp-duty` | `iras_calc_buyer_stamp_duty`, `iras_calc_seller_stamp_duty`, `iras_calc_lease_stamp_duty` | Stamp Duties Act, post-27-Apr-2023 cooling measures |
| DNC Scrub | `roost/extras/property_agent/services/pdpc_dnc.py` | `/property-agent/dnc-scrub` | `pdpc_dnc_check` | PDPA s.43 / Spam Control Act (21-day validity) |
| CDD Screening | `roost/extras/property_agent/services/cdd_screening.py` | `/property-agent/cdd-screen` | `cdd_screen` | CEA Practice Circulars 01-21 & 02-23 (AML/CFT) |
| HDB EIP/SPR Quota | `library/hdb_eip.yaml` (RPA) | via `/recipe rpa` or chat | `rpa_run("hdb_eip", …)` | EAA Practice Guidelines on HDB resale eligibility |
| Singpass-assisted flows | `await_user_session` step op | RPA flow author | RPA YAML | Defensible under PDPA — Roost never holds Singpass creds |

## 1. Stamp Duty Calculator

Pure-Python rate tables. **No** IRAS account, no portal automation —
just maths. The point is to kill the highest-frequency agent error
class: quoting stale ABSD rates (e.g. PR 2nd property at the old 25%).

**Current rates (from 27 Apr 2023):**

| Profile | 1st | 2nd | 3rd+ |
|---|---|---|---|
| Singapore Citizen | 0% | 20% | 30% |
| SPR | 5% | 30% | 35% |
| Foreigner | 60% | 60% | 60% |
| Entity / Trustee | 65% | 65% | 65% |

FTA-exempt foreigners (USA, Iceland, Liechtenstein, Norway, Switzerland)
buying as individuals are taxed at SC rates — pass `fta_exempt: true`.

**SSD (residential resale):** 12% / 8% / 4% / 0% by holding period
(≤1yr / ≤2yr / ≤3yr / >3yr).

**Lease duty:** 0.4% × total rent, capped at 4× AAR for leases > 4 years.

## 2. DNC Scrub

Wraps the PDPC DNC Registry B2B API. Required scrub before any marketing
voice call / SMS / fax to a SG number. Result is valid 21 days.

The service returns `expires_at = scrubbed_at + 21 days` so any consumer
(recipe, RPA flow, web UI) can gate sending on freshness.

**Configuration:**

```bash
DNC_ENABLED=true
DNC_API_BASE_URL=https://www.dnc.gov.sg/api/v2
DNC_API_KEY=<issued by PDPC>
DNC_ORG_ID=<issued by PDPC>
```

Until those land, the page and API endpoints fail closed with a clear
error message; nothing else in the app is affected.

## 3. CDD Screening

The CDD/sanctions/PEP screen is **not property-agent-specific** — it's
a generic AML primitive used here to satisfy CEA Practice Circulars
01-21 / 02-23. Same engine works for fintech onboarding, recruiter
sanctions checks, etc.

Full reference: **[docs/aml-screening.md](aml-screening.md)** —
covers provider ABC, scoring model, configuration, audit-trail shape.

For property-agent use specifically: capture the result on the deal
record before transacting; re-screen when `expires_at` passes.

## 4. HDB EIP/SPR Quota Flow

Public eService — no Singpass. Inputs: block, street, ethnicity,
citizenship. Outputs: a screenshot of the eligibility result and a
log line.

YAML lives at `roost/extras/rpa/services/rpa_flows/library/hdb_eip.yaml`. Selectors
are HDB's; if HDB updates the form, use `rpa_inspect_page` and
`rpa_test_step` to refresh.

## 5. Singpass-Assisted Flows

The `await_user_session` step op is **not property-agent-specific** —
it's a generic RPA primitive for any portal that needs human auth
(IRAS, CPF, MOM, broker portals, banking onboarding, anything 2FA-
gated). Authoring guide: **[docs/rpa-authoring.md](rpa-authoring.md)**.

For property-agent use specifically — INLIS title search, e-Stamping
filing, HFE letter retrieval — all Singpass-gated:

```yaml
- op: goto
  url: https://www.inlis.gov.sg/
- op: await_user_session
  selector: "div.search-results"
  prompt: "Log in via Singpass and complete the S$5.25 search. I'll grab the result."
- op: screenshot
  full_page: true
- op: download_one
  trigger_selector: "a.download-pdf"
```

The flow sends a Telegram heads-up containing a **live browser URL**,
then polls the page in the chromium sidecar until `selector` appears.
The user opens the URL, completes Singpass + payment in the visible
browser — Roost never touches Singpass credentials.

### Reaching the live browser

The Telegram prompt embeds a URL the user opens to drive the live
Chromium session. How that URL is reachable depends on where Roost is
deployed (laptop, hosted-by-you, or VPS+domain) — see
**[docs/deployment.md](deployment.md)** for the full matrix and the
exact `SIDECAR_PUBLIC_URL` value for each shape.

**Never publish chromium port 3000 on a public interface** — that
bypasses Roost's auth. Use the built-in `/sidecar` reverse proxy
(default for cloud / VPS deploys) which gates access through the same
session middleware as the rest of the app.

## Web routes

| Path | Purpose |
|---|---|
| `GET /property-agent/stamp-duty` | Calculator UI (BSD/ABSD/SSD/lease) |
| `GET /property-agent/dnc-scrub` | DNC scrub UI |
| `GET /property-agent/cdd-screen` | CDD screening UI |
| `POST /api/property-agent/stamp-duty/{buyer,seller,lease}` | JSON calc endpoints |
| `POST /api/property-agent/dnc/scrub` | Scrub a number list |
| `POST /api/property-agent/cdd/screen` | Screen a person |

## Tests

```
tests/test_iras_stamp_duty.py   # 22 cases — rate tables, edge cases
tests/test_pdpc_dnc.py          # 7 cases — normalisation, gating, parsing
tests/test_cdd_screening.py     # 10 cases — vendor dispatch, scoring, payload
tests/test_rpa_library.py       # extended — select_option, await_user_session, hdb_eip
```

Run all: `python3 -m pytest -q`.
