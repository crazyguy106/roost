# Authoring an RPA Flow

Roost's RPA layer is **data-driven** — adding a new portal is a YAML file,
not Python code. This guide walks through writing one.

## The two homes for a flow

| Location | Purpose | Tracked in git |
|---|---|---|
| `roost/extras/rpa/services/rpa_flows/library/<portal>.yaml` | Shipped templates, reviewed in PRs, seeded on boot | yes |
| `data/rpa_flows/<portal>.yaml` | Your local working copy (not yet upstreamed) | no (`data/` is gitignored) |
| `rpa_flow_configs` table | Live, MCP-editable runtime config | no — file is canonical |

**Flow of edits:**

```
library/aia.yaml ──seed_library()──▶ DB ◀── rpa_set_flow_config (MCP)
       ▲                              │
       └──── rpa_export_flow ─────────┘
              (round-trip back to YAML when ready to upstream)
```

Library files are seeded on boot **only if** no DB config exists for that
portal — so a contributor's customisations are never overwritten by an
upgrade. To force-overwrite from a file: `rpa_import_flow(path)`.

## File schema

```yaml
portal: <slug>            # required, e.g. "aia"
name: <human label>       # optional
login_url: <url>          # convenience, often used as $param:login_url
enabled: true|false       # default true; ship templates as false
otp_config:
  default_source: telegram | email
steps:
  - op: <step type>
    <args...>
```

`steps` is a non-empty list. Each step has an `op` from the table below
plus its required args.

## Step types

| Op | Required args | Optional |
|---|---|---|
| `goto` | `url` | `wait_until`, `timeout_ms` |
| `fill` | `selector`, `value` | |
| `click` | `selector` | `timeout_ms` |
| `wait_for` | `selector` | `state`, `timeout_ms` |
| `wait_ms` | `ms` | |
| `press` | `selector`, `key` | |
| `select_option` | `selector`, `value` | `label`, `index`, `timeout_ms` |
| `get_otp` | — | `source` (`telegram`\|`email`), `query`, `regex`, `prompt`, `timeout` |
| `await_user_session` | — | `prompt`, `confirm_selector`, `timeout` — surface live URL, wait for human (e.g. Singpass login) before continuing |
| `download_one` | `trigger_selector` | `password_cred`, `extract` |
| `download_each` | `item_selector`, `trigger_selector_within` | `policy_attr`, `password_cred_pattern`, `extract`, `prompt_if_missing`, `max` |
| `upload_drive` | `remote_path` | `source` (`last_download`\|`last_extracted`) |
| `screenshot` | — | `name`, `full_page` — capture PNG into the run's artefact dir |
| `whatsapp_send` | `to`, `message` | `attachment_source` (`last_download`\|`last_extracted`) — dispatch via the WhatsApp adapter |
| `telegram_send` | — | `to` (defaults to first allowed chat), `body`/`caption`, `image`/`document`/`source` (`last_screenshot`\|`last_download`\|`last_extracted`), `as` (`photo`\|`document` — use `document` to keep QR codes scannable), `keyboard` (rows of `{text, value}` for inline-button prompts; pauses run until tapped), `as_var`, `timeout` |
| `log` | — | `message` |

The interpreter cross-checks this list against `_interpreter.HANDLERS` and `schema.KNOWN_OPS` at import time, so any drift between table and code fails fast.

## Placeholders

Inside any string `value`, `url`, `prompt`, `query`, etc.:

| Placeholder | Resolves to |
|---|---|
| `$cred:KEY` | encrypted credential (set via `rpa_set_credential`) |
| `$param:KEY` | runtime param passed to `rpa_run` |
| `$var:KEY` | interpreter variable (e.g. `$var:policy`) |
| `$otp` | shorthand for `$var:otp` |
| `$state:KEY` | run state value |

## Workflow for a new portal

1. **Copy the template:**
   ```bash
   cp roost/extras/rpa/services/rpa_flows/library/example.yaml data/rpa_flows/<portal>.yaml
   ```

2. **Inspect the live portal in DevTools.** Prefer stable selectors —
   `name=`, `id=`, `data-*` — over deep `nth-child` chains. Check that
   they survive a hard refresh.

3. **Validate the file:**
   ```
   rpa_validate_flow("/abs/path/to/<portal>.yaml")
   ```

4. **Import to your DB:**
   ```
   rpa_import_flow("/abs/path/to/<portal>.yaml")
   ```

5. **Iterate per-step against the live portal:**
   ```
   rpa_test_step(portal="<portal>", step_index=0)
   rpa_test_step(portal="<portal>", step_index=1)
   ...
   ```

6. **Run end-to-end:**
   ```
   rpa_run("<portal>", {"login_url": "...", "date": "2026-04-26"})
   ```

7. **Once stable, upstream it.** Either:
   - `rpa_export_flow("<portal>", "roost/extras/rpa/services/rpa_flows/library/<portal>.yaml", global_default=True)`, or
   - hand-edit the library YAML for cleaner diffs.
   Then open a PR. Set `enabled: false` if selectors might rot per-tenant.

## Conventions for upstreamed flows

- **`enabled: false` by default** for shipped templates whose selectors
  haven't been verified across environments.
- **Reference creds with stable names** — `<portal>_user`, `<portal>_password`,
  `zip_password_<portal>_{policy}`. Document any non-obvious creds in the
  YAML header comment.
- **Comment selectors** that are unusual or known to be fragile.
- **No secrets in the file.** Anything sensitive belongs in
  `rpa_set_credential`, never in YAML.

## Live smoke test against a public practice portal

Roost ships a flow for `the-internet.herokuapp.com` (Sauce Labs' practice
site — public credentials, no OTP, no anti-bot). Use this to verify your
chromium sidecar + browser_service are wired up before tackling a real
broker portal.

```bash
# 1. Bring up the chromium sidecar (browserless/chromium on CDP)
docker compose up -d chromium

# 2. Set CDP_ENDPOINT in your .env (direct WebSocket bypasses browserless'
#    /json/version discovery, which reports an unroutable ws://0.0.0.0:3000)
echo 'CDP_ENDPOINT=ws://chromium:3000' >> .env

# 3. From an MCP-connected client (Claude Code, etc.):
rpa_set_credential(portal="the_internet", field="user",
                   value="tomsmith")
rpa_set_credential(portal="the_internet", field="password",
                   value="SuperSecretPassword!")
rpa_run("the_internet",
        {"login_url": "https://the-internet.herokuapp.com/login"})

# 4. Watch:
rpa_get_run(<run_id>)            # status running → completed
ls uploads/rpa/the_internet/<user>/<date>/   # downloaded file lands here
```

If `rpa_get_run` shows `status: completed` and the uploads dir contains a
file, the engine is healthy end-to-end. From there, copy
`library/example.yaml` and start authoring your real portal flow.

## CI

`rpa_validate_flow` is a pure-Python check; it's easy to wire into CI to
verify every library file parses and matches the interpreter's known ops:

```python
# tests/test_library.py
from pathlib import Path
from roost.services.rpa_flows import LIBRARY_DIR, load_yaml

def test_library_files_valid():
    for p in LIBRARY_DIR.glob("*.yaml"):
        load_yaml(p)  # raises FlowValidationError on any issue
```
