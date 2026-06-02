# Roost FA Edition — Architecture

The **FA (Financial Adviser) edition** is the Chatwoot-fronted shape of
Roost. The adviser works inside one Chatwoot inbox; Roost speaks only
Chatwoot REST in both directions. This page is the architecture overview.
For the install runbook see [`docs/fa-laptop-install.md`](fa-laptop-install.md);
for the Chatwoot adapter reference see [`docs/chatwoot.md`](chatwoot.md).

## What FA edition is

A self-hosted bundle that delivers, on one laptop or VPS:

- **Chatwoot 4.14.1** as the unified inbox for WhatsApp / WeChat / Email.
  WhatsApp Cloud credentials live in Chatwoot's inbox setup — **not** in
  Roost's `.env`. Chatwoot owns the Meta relationship.
- **Tailscale Funnel** (laptop) or **Caddy + Let's Encrypt** (VPS) so
  Meta and other webhook senders can reach Chatwoot at a stable HTTPS URL
  without DNS or port-forwarding gymnastics.
- **Roost** as the agentic layer behind Chatwoot — qualification, lead
  pipeline, RPA, CRM sync, scheduling, draft-approval queue — replying
  back through the same Chatwoot conversation thread the adviser sees.

The salesperson reads and writes in Chatwoot; Roost is the off-stage
agent posting drafts and follow-ups into the same thread.

## Architecture

```
                       ┌──────────────────────┐
                       │ WhatsApp customer    │
                       │ (or WeChat / email)  │
                       └──────────┬───────────┘
                                  │ Meta Cloud API
                                  ▼
                       ┌──────────────────────┐
                       │ Chatwoot (Rails)     │   ← adviser works here
                       │  inbox: WhatsApp     │
                       └──────┬──────┬────────┘
       webhook (signed)       │      │  REST (api_access_token)
                              ▼      ▲
                       ┌──────────────────────┐
                       │ Roost (FastAPI)      │
                       │  /api/chatwoot/...   │
                       │  /api/leads, /api/.. │
                       │  MCP server          │
                       │  Telegram bot (opt)  │
                       └──────┬───────────────┘
                              │
                              ▼
                       ┌──────────────────────┐
                       │ data/roost.db        │
                       │ (sqlite, bind-mount) │
                       └──────────────────────┘
```

Network plumbing in front of Chatwoot:

- **Laptop:** `tailscale/serve.json` publishes Chatwoot at
  `https://<host>.<tailnet>.ts.net` via Funnel. Tailscale handles TLS.
- **VPS:** Caddy in `docker-compose.fa-vps.yml` terminates TLS on
  `${PUBLIC_DOMAIN}` and reverse-proxies to Chatwoot. Roost itself binds
  loopback-only (`127.0.0.1:8080`); Chatwoot proxies the operator UI if
  needed, otherwise Roost is reached via SSH tunnel.

## Inbound path

Every customer message flows through five stages. File:line references
are exact for the FA edition as shipped.

1. **Meta → Chatwoot.** WhatsApp Cloud delivers the message to Chatwoot's
   own webhook (`/webhooks/whatsapp/<phone-number-id>`). Chatwoot writes
   the message to its postgres and shows it in the adviser's inbox.
2. **Chatwoot → Roost (signed webhook).** Chatwoot fires its outbound
   webhook to `https://<funnel-host>/api/chatwoot/webhook` with HMAC
   signature in `X-Chatwoot-Signature: sha256=<HMAC_SHA256(secret,
   "<ts>.<body>")>`, replay-protected by `X-Chatwoot-Timestamp`. See
   `roost/extras/messaging_external/services/chatwoot.py::verify_webhook_signature`
   for the 300s replay window and constant-time digest compare.
3. **Envelope filter.** Only `event=message_created` AND
   `message_type=incoming` runs the AI pipeline (router in
   `roost/extras/messaging_external/web/api_chatwoot.py`). Everything
   else — outgoing messages (echo loop avoidance), `conversation_updated`
   (chatty), lifecycle events — acks 200 and is ignored. Pinned by
   `tests/test_chatwoot_inbound.py::test_post_ignores_outgoing_message`.
4. **Inbound debounce.** Real customers fragment messages ("hi", "i was
   wondering", "about retirement"). `inbound_buffer.submit` waits 20s
   (default) for follow-ups, with terminal-punctuation fast path at 4s
   and a 90s hard cap. Tests stub this with an inline pass-through.
5. **Lead ingest + AI processing.** The debounced text is handed to
   `lead_nurture.services.leads.ingest_lead` with `channel="chatwoot"`,
   then to the classifier and the qualification engine. Anything urgent
   fires a Telegram hot-alert.

## Outbound path (post FA-G)

When `CHATWOOT_ENABLED=true`, every WhatsApp outbound from Roost is a
Chatwoot REST call. There is no Meta direct path in FA edition.

| Roost call site | What it sends | Chatwoot endpoint |
|---|---|---|
| `whatsapp.send_text_message(to, body)` | text reply | `POST /conversations/:cid/messages` `{content, message_type: outgoing}` |
| `whatsapp.send_template_message(to, name, lang, components)` | first-touch template (cold start outside 24h window) | `POST /conversations/:cid/messages` `{content, template_params: {name, category, language, processed_params}}` |
| `whatsapp.send_document(to, path=...)` / `send_image(...)` | file attachment | `POST /conversations/:cid/messages` multipart with `attachments[]` |
| `whatsapp.mark_as_read(wamid)` | clear unread flag | **no-op** — Chatwoot owns inbound receipts |
| `chatwoot.list_templates(inbox_id)` | discover approved templates | `GET /inboxes/:iid` → `message_templates[]` |

All five dispatchers share the same find-or-create-contact +
reuse-open-conversation pattern via `route_text_to_whatsapp`,
`route_template_to_whatsapp`, and `route_media_to_whatsapp`.

**Auth.** Every call carries `api_access_token: <CHATWOOT_API_KEY>`. The
key is the adviser's Chatwoot Profile Access Token (Settings →
Profile → Access Token), not a per-app secret. Treat it like an Outlook
password.

**Loop safety.** A Roost-posted message fires a `message_created` webhook
back at Roost with `message_type="outgoing"`. The envelope filter
(step 3 above) drops it before the pipeline runs, so no echo loop.

## What lives where

| Concern | Where |
|---|---|
| Chatwoot adapter (REST + webhook + signature) | `roost/extras/messaging_external/services/chatwoot.py` |
| WhatsApp dispatcher (delegates to Chatwoot when FA) | `roost/extras/messaging_external/services/whatsapp.py` |
| FastAPI router for inbound | `roost/extras/messaging_external/web/api_chatwoot.py` |
| Webhook payload samples (4.14.1) | `docs/chatwoot-webhook-samples/` |
| End-to-end test (sign → POST → ingest → reply → REST capture) | `tests/test_chatwoot_end_to_end.py` |
| Operator smoke script (against live stack) | `scripts/smoke_chatwoot.py` |
| Laptop install + runbook | `scripts/install-fa.sh` + `docs/fa-laptop-install.md` |
| VPS overlay (Caddy + Let's Encrypt) | `docker-compose.fa-vps.yml` |

## Configuration surface

Set in `.env` (template at `env-templates/fa.env`):

| Variable | Source | Purpose |
|---|---|---|
| `CHATWOOT_ENABLED` | `true` for FA edition | Master flag — flips all WhatsApp dispatch through Chatwoot |
| `CHATWOOT_URL` | Funnel URL or VPS domain | Where Roost POSTs Chatwoot REST. Trailing slash optional |
| `CHATWOOT_API_KEY` | Chatwoot Profile → Access Token | Auth header on every REST call |
| `CHATWOOT_ACCOUNT_ID` | Visible in Chatwoot URL (`/app/accounts/<id>`) | Path component in REST URLs |
| `CHATWOOT_INBOX_ID` | Inbox settings URL last segment | Which inbox to read/write |
| `CHATWOOT_WEBHOOK_SECRET` | Chatwoot Settings → Integrations → Webhooks (per-row, shown once) | HMAC verification on inbound |
| `CHATWOOT_FRONTEND_URL` | Same as `CHATWOOT_URL` | Used in admin-facing links |
| `TAILSCALE_AUTHKEY` | `tskey-auth-...` from Tailscale admin | Funnel sidecar auth (laptop only) |
| `CHATWOOT_POSTGRES_PASSWORD` | Auto-generated `hex(24)` | Chatwoot's database |
| `CHATWOOT_SECRET_KEY_BASE` | Auto-generated `hex(64)` | Rails session signing |

Auto-generation runs once in `scripts/install-fa.sh` and is idempotent
(`CHANGE_ME_*` sentinels are only replaced on their full value, so a
re-run won't churn them).

## What FA edition does NOT do

- **No direct Meta WhatsApp creds in Roost.** Chatwoot holds them. If you
  rotate Meta tokens, you rotate them in Chatwoot's inbox settings.
- **No template authoring from Roost.** Roost fires templates by name;
  creation, edits, and Meta-side approval status all live in Chatwoot's
  WhatsApp template UI. `chatwoot.list_templates` surfaces the approved
  set so callers don't hard-code names.
- **No Meta media id reuse.** Chatwoot's REST takes file bytes, not Meta
  media ids or external URLs. Pass a local path; for `link=` callers,
  download the file first.
- **No raw Meta webhook into Roost.** The `/api/whatsapp/webhook` route
  is for non-FA installs. FA installs only accept `/api/chatwoot/webhook`.

## See also

- [`docs/chatwoot.md`](chatwoot.md) — adapter reference: signature
  verification, envelope quirks, full outbound API surface.
- [`docs/fa-laptop-install.md`](fa-laptop-install.md) — one-command
  laptop install with Tailscale Funnel.
- [`docs/whatsapp-adapter.md`](whatsapp-adapter.md) — direct-to-Meta path
  used when `CHATWOOT_ENABLED=false`.
- [`docs/lead-nurture.md`](lead-nurture.md) — cadence engine that
  consumes inbound messages once the adapter ingests them.
- [`docs/deployment.md`](deployment.md) — laptop / hosted-by-you /
  VPS+domain deployment shapes outside FA edition.
