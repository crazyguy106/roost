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

## Operator surface vs customer surface

FA edition splits the two channels deliberately:

| Surface | Channel | Who uses it | What flows through |
|---|---|---|---|
| **Customer** | Chatwoot (web + mobile app) | Customers reaching the adviser | Inbound WhatsApp / WeChat / Email; outbound replies (text, templates, media); conversation threads |
| **Operator** | Telegram bot (FA's personal Telegram) | The adviser, on their phone | Hot-lead alerts, `/nlist` Guardian draft approvals, `/briefing` morning digest, conversational commands to Roost |

Why both: the adviser shouldn't have to sit inside Chatwoot all day to know
a warm lead arrived. Pings land where they already get personal Telegram
messages; they jump into Chatwoot to actually reply. Chatwoot stays the
single customer-facing inbox; Telegram is the agent talking *to the
adviser*, never to customers.

Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS` in `.env` — the
installer prompts for both. The allow-list scopes who can drive the bot;
non-listed Telegram users get a polite refusal.

**Three approval queues, all routed through Telegram.** Roost keeps
draft-and-approve queues for three different classes of action; the
adviser sees and approves all of them from the same operator surface:

| Queue | What sits in it | Approve command |
|---|---|---|
| **Recipe drafts** | Output of a recipe / SOP trigger that's marked "needs human review" before it sends | `/approve_<run_id>` in Telegram (link arrives with the draft) |
| **Cadence drafts** | Guardian-paused steps inside a nurture cadence (`status=awaiting_approval:N`); also surface in the morning brief | `/nlist` to list, `/napprove <enrollment_id>` / `/nskip <id>` to act |
| **Money-moving drafts** | SME-ops tools that hit `guardian_gate` — refunds, order cancels, authorised invoices, payouts | Web UI (`/sme/sync-status` pending-drafts card) + API (`/api/sme/drafts/*`); not chat-driven |

The morning brief surfaces backlog from all three queues (and now,
post-FA-H, also the raw open-conversation count from Chatwoot itself)
so the adviser starts the day with a single rollup.

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
                       │  Telegram operator   │──► adviser's phone
                       │  bot (alerts/nlist)  │    (Telegram app)
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
   fires a Telegram hot-alert. If a CRM provider is configured
   (`CRM_PROVIDER=attio|zoho|hubspot|...`), `ingest_lead` also calls
   `provider.create_person(...)` on first contact — the prospect lands
   in the CRM with no extra wiring. Subsequent inbound on the same
   phone is matched and appended via the CRM's communications log.

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

## Build status

The FA edition shipped on the `feature/fa-edition` branch as five
landable commits. Each one stands on its own — tests pass at every step,
so a self-hoster can fork at any tag and run.

| Tag | Commit | What landed |
|---|---|---|
| **FA-A** | `e60008a` | Chatwoot adapter — HMAC-signed inbound webhook (`/api/chatwoot/webhook`), REST outbound primitives (`send_message`, `find_or_create_contact`, `create_conversation`, `mark_as_read`/`_resolved`). 4.14.1 webhook payload samples captured under `docs/chatwoot-webhook-samples/` |
| **FA-B** | `4d91248` | `whatsapp.send_text_message` delegates to `chatwoot.route_text_to_whatsapp` when `CHATWOOT_ENABLED` — text outbound flows through Chatwoot, no caller changes needed |
| **FA-D** | `c96e734` | One-line laptop install: `scripts/install-fa.sh` + `docker-compose.fa.yml` (Chatwoot + Sidekiq + pgvector/Redis + Tailscale Funnel sidecar) + `docker-compose.fa-vps.yml` (Caddy variant) + `env-templates/fa.env` + `docs/fa-laptop-install.md` runbook |
| **FA-E** | `8f8a9ef` | End-to-end signed-POST round-trip test (`tests/test_chatwoot_end_to_end.py`) + operator smoke against a live stack (`scripts/smoke_chatwoot.py`) |
| **FA-G** | `4c57be2` | Consolidated outbound — templates (`template_params` payload), media (multipart `attachments[]`), template discovery (`list_templates`) all on `POST /conversations/:cid/messages`. One dispatch path; no Meta-direct surface left in FA edition |
| **FA-J** | `579c5db` | Telegram defaults on as the operator surface. `env-templates/fa.env` flips `TELEGRAM_ENABLED=true` and adds `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USERS` sentinels. `docker-compose.fa.yml` overrides `ENABLE_TELEGRAM=true` so the image builds with `python-telegram-bot`. Install script prompts for both values via @BotFather / @userinfobot |
| **FA-I** | `e186f84` | Lead-nurture dispatch handles `channel="chatwoot"`. Cadence engine, qualification questionnaire, and the `/leads` reply box now all recognise Chatwoot leads — they delegate to `whatsapp.send_text_message`, which since FA-G already routes through Chatwoot REST. Closes the gap where Chatwoot-sourced leads couldn't be replied to from `/nlist` or `/leads` |
| **FA-H** | `07fe2e0` | Morning brief now includes Chatwoot inbox backlog. Two new service helpers (`conversation_meta`, `list_open_conversations`) feed a `chatwoot` section in `daily_summary.build_summary` — open/pending counts and a preview list of the top open threads. Fails closed when the inbox is unreachable |
| **FA-K** | `8c9fd60` | MCP tool `chatwoot_list_templates(inbox_id=0)`. Lets agents/recipes discover WABA-approved WhatsApp templates synced into Chatwoot, instead of hard-coding template names that may have been retired |

Test suite at 715 green at FA-K.

**Open, not yet a commit:**

- **FA-F** — push `feature/fa-edition` to remote, cut a release tag, roll
  `[Unreleased]` into a dated section in `CHANGELOG.md`.
- **No real-laptop install yet.** Script is bash-clean, e2e + smoke pass,
  but no adviser has run it against a live Meta WhatsApp Cloud number.

## A day in the FA inbox

Concrete walkthrough for a Singapore financial adviser running FA
edition on a laptop. Times are illustrative.

**07:45 — laptop wakes.** Chatwoot, Roost, and the Tailscale Funnel
sidecar come back up. The Funnel URL is stable across reboots; Meta's
webhook deliveries that queued overnight start arriving.

**08:00 — overnight inbound lands.** Three new WhatsApp messages from
prospects hit Chatwoot. Meta → Chatwoot's webhook → Chatwoot inbox →
Chatwoot's outbound webhook (signed) → Roost. For each one Roost:

1. Verifies the HMAC signature.
2. Drops it into `inbound_buffer` (20s debounce window — fragmented
   "hi" / "i was thinking" / "about retirement" collapse to one).
3. Calls `lead_nurture.services.leads.ingest_lead(channel="chatwoot", ...)`.
4. Classifies intent + urgency via `ai_cdr.classify_message`.
5. If urgent, fires a Telegram hot-alert ("warm lead asking about
   endowment policies — see thread").

**08:05 — Telegram pings.** Of the three overnight messages, one is
flagged urgent ("warm lead asking about endowment policies — Mei, see
thread"). The adviser sees it on their phone before they've even opened
Chatwoot. The non-urgent two arrive as part of the 08:00 morning brief,
not as individual alerts.

**08:15 — adviser opens Chatwoot.** They see the three threads in their
inbox, each tagged by Roost. Hot ones float to the top via Chatwoot's
own priority sort. Roost's classification appears as a *private note*
(internal, customer-invisible) under the inbound, summarising what
the prospect asked and what stage they're at.

**08:30 — adviser approves a draft from Telegram.** For one of the warm
leads, Roost posted a suggested reply into the Guardian draft queue and
pinged the adviser on Telegram: *"Draft #42: 'Hi Mei, I'd love to walk
you through endowment options — does Thursday 2pm work?' — approve with
/napprove 42"*. The adviser taps `/napprove 42` from the Telegram app on
their phone; the message goes through `route_text_to_whatsapp` →
Chatwoot REST → Meta → Mei's phone. The full thread (their approved
reply alongside Mei's inbound) is visible in Chatwoot when they next
open it.

**11:00 — first-touch template fire.** The adviser has a list of three
referrals from last week. They ask Roost (via the web UI or Telegram)
to "send the fa_welcome template to each, with their name". Roost calls
`send_template_message(...)` which routes through
`route_template_to_whatsapp` — Chatwoot fires the template against
WABA, three new conversations appear in the inbox.

**14:00 — sending a policy document.** The adviser uploads a PDF
illustration via Roost's `/files` page, then asks Roost to "send it to
Mei". `send_document(to, path=...)` multipart-uploads to Chatwoot,
which forwards to WABA. The attachment appears in the conversation
thread (and on Mei's phone) within seconds.

**18:00 — adviser closes.** Threads they've handled get
`mark_as_resolved` in Chatwoot (manual or via Roost). Tomorrow morning
the daily summary (FA-H once landed) will tell them what was opened,
closed, and outstanding.

**Throughout the day:** Roost never echo-loops on its own outbound. Every
Roost-posted message fires a `message_created` webhook back at Roost
with `message_type="outgoing"` — the envelope filter drops it (covered
by `test_post_ignores_outgoing_message`), so the AI pipeline never
processes its own replies.

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
