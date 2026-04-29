# RPA — Browser Automation for Insurance Broker Portals

Roost ships a small RPA layer for cumbersome login-then-download portals
(insurance brokers in particular). Flows can pause mid-run to ask the user
for an OTP via Telegram and resume when the user replies.

**Flows are data, not code.** Each portal's flow is a list of step dicts
stored in the `rpa_flow_configs` table and edited via MCP tools. Adding a
new portal is a configuration change — no Python file to write.

**Open-source friendly.** Shipped flows live as YAML files under
`roost/services/rpa_flows/library/` (reviewable in PRs, seeded on boot).
DB rows are the live mutable copy; user-authored flows live under
`data/rpa_flows/`. See [rpa-authoring.md](rpa-authoring.md) for the
contributor guide and the round-trip via `rpa_import_flow` /
`rpa_export_flow`.

**Two ways to author a flow:**
- **Non-technical:** chat with the bot — say *"set up automation for
  `<URL>` that does `<thing>`"*. The agent uses `rpa_inspect_page`
  to read the DOM, drafts the YAML, asks for credentials, walks
  through testing, and runs it. See [rpa-for-users.md](rpa-for-users.md).
- **Hand-authored:** write YAML directly. See [rpa-authoring.md](rpa-authoring.md).

## Architecture

```
recipe (RPA_FLOW:<portal>)         tools_rpa.rpa_run        /recipe in bot
        │                                   │                     │
        └────────► roost.services.rpa_flows.dispatch ─────────────┘
                                  │
                                  ▼
                       _interpreter.run_config
                  │            │             │
                  │            ▼             ▼
                  │   browser_service   archive_service
                  │   (async Playwright  (pyzipper, AES ok)
                  │    over CDP)
                  │
                  └─► otp_source.{Email,Telegram}OtpSource
                                  │
                                  ▼
                  rpa_runs.request_input  ──► Telegram prompt
                                                 ▲
                                                 │ user replies
                          rpa_runs.submit_input ─┘
```

## Components

| File | Role |
|---|---|
| `roost/services/rpa_runs.py` | Durable, pausable run state machine (`running` ↔ `awaiting_input` → `completed`/`failed`/`cancelled`). |
| `roost/services/browser_service.py` | Async-Playwright connection to the browserless sidecar (`CDP_ENDPOINT`); persists per-portal storage state under `data/browser_state/<user>/<portal>.json`. |
| `roost/services/archive_service.py` | `extract_zip(path, password)` using `pyzipper` (AES-aware). Raises `BadZipPassword`. |
| `roost/services/otp_source.py` | `EmailOtpSource` polls Gmail; `TelegramOtpSource` prompts the user. |
| `roost/services/rpa_flows/configs.py` | CRUD for stored flow configs. |
| `roost/services/rpa_flows/_interpreter.py` | Generic step interpreter — the engine. |
| `roost/bot/handlers/rpa_input.py` | Telegram handler at group `-1` that consumes the next plain message into an `awaiting_input` run; `/rpa list`, `/rpa cancel <id>`. |
| `roost/mcp/tools_rpa.py` | MCP tools for run management, credentials, and flow configs. |

## Step types

| Op | Args | Notes |
|---|---|---|
| `goto` | `url, wait_until?, timeout_ms?` | Navigate. |
| `fill` | `selector, value` | `value` supports placeholders (see below). |
| `click` | `selector, timeout_ms?` | |
| `wait_for` | `selector, state?, timeout_ms?` | |
| `wait_ms` | `ms` | Fixed delay. |
| `press` | `selector, key` | e.g. `{"selector":"input[name=q]","key":"Enter"}`. |
| `select_option` | `selector, value, by?` | Pick from a `<select>`. `by: "value"` (default) or `"label"`. `value` resolves placeholders. |
| `get_otp` | `source: "email"\|"telegram", query?, regex?, prompt?, timeout?` | Stores code in `$otp`. |
| `await_user_session` | `selector, prompt?, timeout_ms?` | Pauses until the user completes a manual action in the browser (Singpass, payment, captcha). Resumes when `selector` appears. Sends a Telegram heads-up; no user reply needed. |
| `download_one` | `trigger_selector, password_cred?, extract?` | Saves to today's dir; optional zip extract. |
| `download_each` | `item_selector, trigger_selector_within, policy_attr?, password_cred_pattern?, extract?, max?, prompt_if_missing?` | Loops over items; `password_cred_pattern` may use `{policy}`. |
| `upload_drive` | `remote_path, source: "last_download"\|"last_extracted"` | Push to Drive (requires Google enabled). |
| `screenshot` | `path?, name?, selector?, full_page?` | Captures the page (or `selector` element). Saves to today's run dir under `screenshots/`; path stored in `$var:last_screenshot`. |
| `whatsapp_send` | `to, body? \| document? \| image?, caption?, filename?, source?` | Sends via Meta WhatsApp Cloud API. `source` shortcut: `"last_download" \| "last_screenshot" \| "last_extracted"`. Errors are logged into `$var:last_whatsapp_error`, not raised. Requires `WHATSAPP_ENABLED`. |
| `log` | `message` | Info-log a message. |

### Value placeholders

Used inside `value`, `url`, `prompt`, `query`, etc.

| Placeholder | Resolves to |
|---|---|
| `$cred:KEY` | `credentials.get_credential(KEY)` (encrypted at rest). |
| `$param:KEY` | `params.KEY` supplied at run time. |
| `$var:KEY` | Interpreter variable (e.g. `$var:policy`). |
| `$otp` | Shorthand for `$var:otp`. |
| `$state:KEY` | Run state JSON value. |

## Setup

1. Store credentials:
   ```
   rpa_set_credential(portal="aia", field="user", value="…")
   rpa_set_credential(portal="aia", field="password", value="…")
   ```
2. Define the flow:
   ```
   rpa_set_flow_config(
     portal="aia",
     login_url="https://www.aia.com.sg/portal/login",
     steps=[
       {"op":"goto","url":"$param:login_url"},
       {"op":"fill","selector":"input[name='username']","value":"$cred:aia_user"},
       {"op":"fill","selector":"input[name='password']","value":"$cred:aia_password"},
       {"op":"click","selector":"button[type='submit']"},
       {"op":"wait_for","selector":"input[name='otp']"},
       {"op":"get_otp","source":"telegram","prompt":"Enter the AIA OTP"},
       {"op":"fill","selector":"input[name='otp']","value":"$otp"},
       {"op":"click","selector":"button[type='submit']"},
       {"op":"wait_for","selector":"table.policies"},
       {"op":"download_each",
        "item_selector":"tr.policy-row",
        "trigger_selector_within":"a.download",
        "policy_attr":"data-policy-no",
        "extract":True,
        "password_cred_pattern":"zip_password_aia_{policy}",
        "prompt_if_missing":True},
       {"op":"upload_drive","remote_path":"DACTA/Insurance/AIA/$param:date/","source":"last_extracted"}
     ],
   )
   ```
3. Trigger:
   - From Claude / MCP: `rpa_run("aia", {"login_url": "...", "date": "2026-04-25"})`.
   - As a recipe: create one with `instructions = "RPA_FLOW:aia"` and trigger via `/recipe` or cron.

## Debugging

- `rpa_test_step(portal, step_index)` runs one step in isolation against the
  live portal. Useful for iterating on selectors.
- `rpa_get_run(run_id)` shows current status and any pending prompt.
- `/rpa list` and `/rpa cancel <id>` from Telegram.

## Caveats

- A run mid-flight does not survive a process restart in v1 (the resume
  `asyncio.Event` is in-memory). DB rows persist so cleanup/listing works.
- No SMS receive — SMS-OTP portals must use `get_otp { source: "telegram" }`
  and have the user paste the code.
- No captcha solver. Captcha-gated portals will time-out at the OTP step.
- Anti-bot defences are real. Storage state is persisted per portal so we
  don't fingerprint-fresh on every run; `playwright-stealth` may be needed
  later for stricter portals.
- **Browserless CDP gotcha:** the `/json/version` discovery endpoint
  reports `ws://0.0.0.0:3000/` (its bind address, not its hostname), which
  Playwright's `connect_over_cdp` then fails to dial. Set
  `CDP_ENDPOINT=ws://chromium:3000` (a direct WebSocket URL) to skip
  discovery. The compose service alias `chromium` is already correct.
- **Download body transport:** `download.save_as()` over CDP performs a
  server-side copy *inside the sidecar* and returns 0 bytes locally. The
  interpreter works around this by re-fetching `download.url` via the
  page's APIRequestContext (which carries session cookies). `blob:` URLs
  fall back to `save_as` and may need a different strategy per portal.
