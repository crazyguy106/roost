# Property-Agent RPA Flow Library — Scope

Target user: Singapore CEA-registered salesperson. Goal: replace the
manual "log in to portal X, paste NRIC, click around, download PDF,
file it" loop with one Telegram/web command per task.

Each flow below maps to the existing data-driven interpreter. Where a
flow needs a step op we don't have yet, it's flagged.

---

## Tractability matrix

| # | Flow | Auth | Frequency | Blocker | Build now? |
|---|---|---|---|---|---|
| 1 | DNC Scrub | API key (no portal login) | Per cold-call batch | None | ✅ Yes |
| 2 | INLIS title search | Singpass (paid per query) | Per listing | Singpass + payment | ⚠️ Stub only |
| 3 | e-Stamping (IRAS) submit | Singpass / Corppass | Per OTP/lease | Singpass | ⚠️ Stub only |
| 4 | EIP/SPR quota check (HDB) | Public (no login) | Per HDB resale | None | ✅ Yes |
| 5 | Instant CDD (screening) | API key (Acuris/ComplyAdvantage) | Per buyer/tenant | Vendor account | ✅ Yes (config-only) |
| 6 | HFE Guardian (HDB HFE) | Singpass | Per buyer | Singpass | ❌ Defer |

Singpass-gated flows (2, 3, 6) all hit the same blocker: Roost cannot
automate Singpass login (Singpass MyInfo OAuth is required, captcha +
device-bound). Best move there is **Singpass-assisted**: Roost prepares
the inputs, the user logs in via the chromium sidecar in the assisted
session, Roost resumes once the post-auth page lands. Doable but needs
a new `await_user_session` step op.

---

## Flow-by-flow

### 1. DNC Scrub  ✅

**Goal:** before a marketing call/SMS batch, scrub a list of S'pore
mobile numbers against the DNC registry (no-call / no-message / no-fax).

**Portal:** PDPC DNC API (`https://www.dnc.gov.sg/`) — RESTful, requires
account + API key. No browser automation needed: this is an HTTP flow.

**Decision:** **don't model as RPA**. Add as an MCP tool
`pdpc_dnc_check(numbers, registers=["DNC_NoCall","DNC_NoMessage"])` that
calls the API directly. RPA flows are for portals; this isn't one.

**Output:** `{number, allowed: bool, registers_blocked: [...], scrubbed_at}`
records, valid 21 days (per the Spam Control Act / fact-check item).

**Net-new code:** `roost/services/pdpc_dnc.py` + `roost/mcp/tools_pdpc.py`.

---

### 2. INLIS title search  ⚠️

**Goal:** pull official land-title info for a property (owner of record,
encumbrances, lease balance) before listing or before submitting an
offer.

**Portal:** `https://www.inlis.gov.sg/` (SLA). Singpass login. Each
search S$5.25 (paid via eNETS / credit card per session).

**Why hard:**
- Singpass auth (see assisted-session note below).
- Per-query payment — automating spend is risky; prefer a "prepare
  search, hand off to user" model.

**Proposed UX:** `inlis_prepare(property_address)` → flow opens chromium
sidecar to the right form, pre-fills the address fields from a parsed
listing, screenshots the result page, attaches the PDF receipt to the
deal record. User clicks "Pay & Search" themselves.

**Steps (with assisted handoff):**
```yaml
- op: goto
  url: https://www.inlis.gov.sg/
- op: await_user_session    # NEW: pause until user completes Singpass + payment
  selector: "div.search-results"
  prompt: "Log in via Singpass and complete the S$5.25 search. I'll grab the result."
- op: screenshot
  full_page: true
- op: download_one
  trigger_selector: "a.download-pdf"
```

**Net-new step op:** `await_user_session` — same shape as `get_otp` but
pauses indefinitely waiting for a target selector instead of OTP text.

---

### 3. e-Stamping (IRAS)  ⚠️

**Goal:** stamp a tenancy agreement / OTP / S&P. Compute duty, file,
pay, return the certificate PDF.

**Portal:** `https://mytax.iras.gov.sg/` → Stamp Duty section.
Singpass / Corppass login. Payment per stamping.

**Why hard:** same Singpass blocker. Plus the duty calculation is
non-trivial (BSD tiered, ABSD residency-dependent, lease formula), and
getting it wrong is the agent's most common error per §3.3 of the
requirements doc.

**Two-track approach:**
1. **Calculator-only (build now):** an MCP tool
   `iras_calc_stamp_duty(transaction_type, price, holding_period?, buyer_profile)`
   that returns BSD/ABSD/SSD figures. No automation, just the maths.
   Wraps the IRAS rate tables. **Highest-value piece** — eliminates the
   "PR 2nd property is 25%" stale-rate error.
2. **Filing flow (defer):** Singpass-assisted RPA flow that pre-fills
   the e-Stamping form from a parsed OTP, then hands off for payment.

**Net-new code:** `roost/services/iras_stamp_duty.py` (pure-Python rate
tables, post-Apr-2023). MCP tool `iras_calc_stamp_duty`.

---

### 4. HDB EIP/SPR quota check  ✅

**Goal:** before submitting an HDB resale application, check whether
the block has hit its Ethnic Integration Policy or SPR quota for the
buyer's profile. A miss here = case rejected weeks later.

**Portal:** `https://services2.hdb.gov.sg/web/fi10/emap.html` (eService
"Ethnic Group Eligibility for Buyers"). Public — no login.

**Inputs:** block + street + buyer ethnicity + citizenship.
**Output:** "eligible" / "not eligible" + current quota %.

**Steps:**
```yaml
portal: hdb_eip
name: HDB EIP/SPR Quota Check
login_url: https://services2.hdb.gov.sg/web/fi10/emap.html
steps:
  - op: goto
    url: $param:login_url
  - op: fill
    selector: "input#blockNo"
    value: $param:block_no
  - op: fill
    selector: "input#streetName"
    value: $param:street_name
  - op: click
    selector: "select#ethnicGroup option[value=$param:ethnic_group]"
  - op: click
    selector: "select#citizenship option[value=$param:citizenship]"
  - op: click
    selector: "button#search"
  - op: wait_for
    selector: "div.results"
  - op: screenshot
    full_page: true
  - op: log
    message: "EIP quota check captured for block $param:block_no"
```

**Caveat:** the placeholder gotcha — pass `block_no`, `street_name`,
`ethnic_group`, `citizenship` as standalone params, not embedded.

**Net-new step ops:** none. Build now.

---

### 5. Instant CDD (sanctions / PEP screening)  ✅

**Goal:** run AML/CFT Customer Due Diligence on a buyer/tenant — name
+ NRIC/passport — against sanctions, PEP, and adverse-media lists.
This is a **PC 01-21 / 02-23 mandatory check** before transacting.

**Portal:** none — this is an API integration with a screening vendor.
Options: ComplyAdvantage, Acuris (Dow Jones Risk Center), Refinitiv
World-Check. All are paid (~S$1–5 per screen).

**Decision:** **don't model as RPA**. Build as an MCP tool
`cdd_screen(name, dob?, nationality?, id_number?)` that wraps the
chosen vendor's API. Cache results 30 days (CDD refresh interval).

**Output:** `{matched: bool, hits: [...], risk_score, screened_at, expires_at}`,
with the full hit detail file stored as an artefact.

**Net-new code:** `roost/services/cdd_screening.py`,
`roost/mcp/tools_cdd.py`. Vendor-agnostic interface so we can swap
ComplyAdvantage ↔ Acuris.

---

### 6. HFE Guardian (HDB HFE letter)  ❌

**Goal:** track the agent's clients' HFE letters, alert before they
expire (9-month validity), pull the latest from the HDB portal.

**Why defer:** Singpass-only, no public form, the user already gets
expiry emails from HDB. Most leverage here is a **calendar reminder**
keyed off `hfe_issued_at + 9 months - 14 days`, not RPA. Build as a
Roost recipe later.

---

## Net-new building blocks (ranked by leverage)

1. **`iras_calc_stamp_duty`** (pure Python, <1 day) — kills the
   highest-frequency agent error. Just rate tables.
2. **`pdpc_dnc_check`** (API integration, ~1 day) — 21-day cycle, hits
   every cold-outreach batch.
3. **`cdd_screen`** (vendor API, ~2 days) — mandatory under PC 01-21,
   currently a manual web-search-and-pray.
4. **HDB EIP YAML flow** (config-only, hours) — proves the chromium
   sidecar covers a real public portal.
5. **`await_user_session` step op** (~1 day) — unblocks every Singpass
   flow without us ever touching a Singpass credential.
6. **INLIS / e-Stamping filing flows** (Singpass-assisted, after #5).

## Out of scope (v2 candidates)

- Singpass headless login. Singpass terms forbid it; even if we got it
  working, MyInfo deprecates session-cookie reuse.
- OCR of stamping certificates / OTP PDFs. Use Gemini Vision via the
  existing `gemini_vision` tool when needed.
- WhatsApp delivery of CDD reports — already covered by the
  `whatsapp_send` step op once a flow ends with a generated PDF.

## Compliance posture (why this matters for marketing)

Every one of the build-now items maps to a **named CEA / PDPC /
IRAS / MAS obligation**:

| Build item | Obligation |
|---|---|
| `cdd_screen` | PC 01-21, PC 02-23 (AML/CFT CDD) |
| `pdpc_dnc_check` | PDPA s.43 / Spam Control Act |
| `iras_calc_stamp_duty` | Stamp Duties Act (cooling measures) |
| HDB EIP flow | EAA Practice Guidelines on resale eligibility |
| `await_user_session` (Singpass-assisted) | preserves user-controlled auth — defensible under PDPA |

The pitch is "compliance-as-software" — each tool produces a
timestamped evidence artefact that lands in the agent's deal record.
Disciplinary defence and audit trail in one move.
