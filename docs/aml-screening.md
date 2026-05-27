# AML / Sanctions Screening

Vendor-agnostic customer due-diligence screening — sanctions, PEP,
adverse-media — wired today against ComplyAdvantage with a `Provider`
ABC so Acuris / Refinitiv / Dow Jones can be swapped in without
touching call sites.

This is **not** property-agent-specific. Any AML-regulated workflow can
use it: fintech onboarding, crypto KYC, accounting / law / corporate
secretarial firms, recruiters checking sanctions, marketplaces with
seller-verification requirements.

### Where it lives in the codebase

The screening service is currently the `cdd_screen` component of the
`property_agent` bundle (Singapore CEA AML/CFT is the first concrete use
case). There is no standalone `aml_screening` bundle:

- Service: `roost/extras/property_agent/services/cdd_screening.py`
- MCP tool: `roost/extras/property_agent/mcp/tools_cdd.py` (`cdd_screen`)
- Web UI: `/property-agent/cdd-screen`
- Master flag: `CDD_ENABLED` (also requires `PROPERTY_AGENT_ENABLED` to
  load the bundle)

When a second vertical (fintech, recruitment, etc.) actually consumes
this, the natural refactor is to lift `cdd_screening.py` into its own
`aml_screening` bundle and have `property_agent` depend on it. Until
then, the property-agent location keeps the surface area honest.

## Configuration

```bash
CDD_ENABLED=true
CDD_PROVIDER=complyadvantage
CDD_API_KEY=<vendor key>
CDD_REFRESH_DAYS=30
```

When `CDD_ENABLED=false`, the MCP tool and web endpoints fail closed
with a clear "not configured" error; nothing else in the app breaks.

## What it returns

A normalised record with:

- `risk_score` ∈ [0, 1] — weighted by hit type (sanction 1.0 →
  adverse-media 0.3) × match status (true_positive 1.0 /
  potential_match 0.7 / false_positive 0.0).
- `hits[]` — full provider payload preserved on each hit.
- `expires_at = scrubbed_at + CDD_REFRESH_DAYS` — UI / recipes can gate
  on freshness and prompt for re-screen.
- `raw` — the unmodified vendor response (audit trail).

Persist the whole result on the deal / customer record. **The audit
trail is the point** — when a regulator asks "why did you onboard
them", the answer is this object plus a timestamp.

## Surfaces

- **MCP:** `cdd_screen(name, dob?, nationality?, ...)` — tool in
  `roost/extras/property_agent/mcp/tools_cdd.py`.
- **Web UI:** `/property-agent/cdd-screen` (the page lives under the
  property-agent namespace today; the underlying screen is generic).
- **Web API:** `POST /api/property-agent/cdd/screen`.
- **Service:** `roost/extras/property_agent/services/cdd_screening.py`.

## For Singapore property agents

CEA Practice Circulars **01-21** and **02-23** require AML/CFT
screening before transacting. `cdd_screen` produces an artefact
suitable for CEA disciplinary defence — capture it on the deal,
reference the `expires_at` to know when to re-screen.

For non-property AML obligations (MAS Notice 626 for fintech, MOM for
recruiters of foreign workers, etc.) the same primitive applies — only
the retention period and trigger conditions change.

## Adding a new provider

Implement the `Provider` ABC in `roost/extras/property_agent/services/cdd_screening.py`:

```python
class Provider(ABC):
    def screen(self, *, name: str, dob: date | None = None,
               nationality: str | None = None, ...) -> ScreenResult: ...
```

Register the class in `_PROVIDERS` and gate it on its own env var. The
risk-score normaliser is shared — providers return raw hits, the
service computes the score uniformly.

## Tests

```
tests/test_cdd_screening.py   # 10 cases — vendor dispatch, scoring, payload
```
