# CRM Adapters

Roost talks to many CRMs through one vendor-agnostic interface. Pick the
backend with `CRM_PROVIDER`; recipes and MCP tools work unchanged.

## Supported providers

| `CRM_PROVIDER` | Auth | Notes |
|---|---|---|
| `local` *(default)* | — | Wraps Roost's own `contacts` table. No external account. Deals are stubbed. |
| `attio` | API key (`ATTIO_API_KEY`) | Configurable object slugs (`ATTIO_OBJECT_PEOPLE/COMPANIES/DEALS`). AI-attribute push via `AttioProvider.set_ai_attribute(...)`. |
| `hubspot` | Private App access token (`HUBSPOT_ACCESS_TOKEN`) | Uses v3 CRM API. WhatsApp/SMS/LinkedIn map to native communication objects. |
| `zoho` | OAuth refresh token (`ZOHO_CLIENT_ID/SECRET/REFRESH_TOKEN`) | Auto-refreshes 1-hour access token. Capture the refresh token via the **Connect via OAuth** button on the settings page (`GET /auth/zoho/start`). |
| `salesforce` | Access token + instance URL | v1 ships with raw access token; refresh-token plumbing comes with the OAuth flow. |
| `pipedrive` | API token (`PIPEDRIVE_API_TOKEN`) | Legacy query-param auth. |

Switch backends:

- **Settings page** — `Settings → CRM → Activate`. Stores a DB override that
  beats the env var; no restart needed.
- **Env var** — set `CRM_PROVIDER=...` in `.env` and restart.

Resolution order: explicit `get_provider(provider=...)` arg → DB setting
`crm_provider` → `CRM_PROVIDER` env → `local`.

## The 80% interface

Every provider implements the same `CrmProvider` ABC (`roost/services/crm/base.py`):

- **People** — `find_person`, `get_person`, `search_people`, `create_person`, `update_person`
- **Organisations** — `find_org`, `create_org`
- **Deals** — `list_deals`, `create_deal`, `update_deal`, `move_deal_stage`
- **Notes & comms** — `append_note`, `log_communication`
- **Custom fields** — `set_custom_field`

Vendor-specific power features ship as namespaced extras on the
concrete provider class (e.g. `AttioProvider.set_ai_attribute`) and are
not part of the base ABC.

## MCP tools

Recipes and Claude Code drive the same surface via MCP:

- `crm_test_connection`
- `crm_find_person`, `crm_get_person`, `crm_search_people`,
  `crm_create_person`, `crm_update_person`
- `crm_list_deals`, `crm_create_deal`, `crm_move_deal_stage`
- `crm_append_note`, `crm_log_communication`
- `crm_set_custom_field`

## Webhooks

POST `/api/crm/{provider}/webhook` — one endpoint per provider. Events
are verified per-vendor (HMAC for Attio/HubSpot, bearer for Zoho/SF,
basic-auth for Pipedrive) and normalised to:

```json
{
  "provider": "hubspot",
  "event_type": "updated",
  "object_type": "deal",
  "record_id": "1234",
  "changes": {"dealstage": {"to": "qualifiedtobuy"}},
  "raw": { ... }
}
```

Configure verification per provider:

```env
ATTIO_WEBHOOK_SECRET=...           # HMAC SHA256 of body
HUBSPOT_APP_SECRET=...             # X-HubSpot-Signature-v3
ZOHO_WEBHOOK_TOKEN=...             # Authorization: Bearer
SALESFORCE_WEBHOOK_TOKEN=...
PIPEDRIVE_WEBHOOK_USER=...
PIPEDRIVE_WEBHOOK_PASSWORD=...
```

Empty secrets accept all requests — only do this in dev.

## Recipes: `crm_event` trigger

Register a recipe with `trigger_type="crm_event"` and a JSON
`trigger_config` filter. The webhook handler dispatches matching events
to the recipe's `run_recipe(...)` with the normalised event in
`context["crm_event"]`:

```json
{
  "trigger_type": "crm_event",
  "trigger_config": {
    "provider": "hubspot",
    "object_type": "deal",
    "event_type": "updated"
  }
}
```

Filter keys are optional — omitting `provider` matches across CRMs.

## Why an LCD and not 1:1 mapping?

Real-world recipes need portable verbs ("when a deal moves to Won, send a
WhatsApp template"). The shared LCD lets a Roost user swap Attio for
HubSpot without rewriting their automations. Vendor-specific power
features remain available via `extra=` kwargs on `crm_create_*`/
`crm_update_*` (passed through unchanged) and via direct provider
methods when a recipe absolutely needs them.
