# Chatwoot Adapter

[Chatwoot](https://www.chatwoot.com/) is a self-hosted open-source helpdesk.
The Roost FA edition fronts WhatsApp / WeChat / Email behind a single
Chatwoot inbox so the salesperson sees one unified queue, and the Roost
agent talks to **one** channel (the Chatwoot REST API) instead of one per
provider.

This adapter is the Roost side of that arrangement:

- **Inbound:** Chatwoot fires HMAC-signed webhooks at
  `/api/chatwoot/webhook` whenever a customer message arrives in a watched
  inbox. The adapter validates the signature, parses the envelope, and
  hands the message to the same inbound pipeline used by the WhatsApp /
  Telegram adapters (lead-ingest → recipes → Telegram notify).
- **Outbound:** Roost talks back to Chatwoot via REST — replying in an
  existing conversation, finding-or-creating a contact, opening a new
  conversation, or marking a conversation read / resolved.

Reference payloads from a live Chatwoot 4.14.1 sandbox are checked into
[`docs/chatwoot-webhook-samples/`](chatwoot-webhook-samples/) — one
sanitised JSON file per event, plus a field-summary table and the
extraction script used to produce them.

## Components

| Path | Role |
|---|---|
| `roost/extras/messaging_external/services/chatwoot.py` | `verify_webhook_signature`, `parse_webhook_event`, `send_message`, `create_conversation`, `find_or_create_contact`, `mark_as_read`, `mark_as_resolved`. |
| `roost/extras/messaging_external/web/api_chatwoot.py` | FastAPI router at `/api/chatwoot/webhook` (GET liveness + POST event handler). |
| `tests/test_chatwoot_inbound.py` | 14 tests — parser shapes for all 7 captured events, signature round-trip + replay rejection, end-to-end routing. |
| `docs/chatwoot-webhook-samples/` | One sanitised JSON sample per event + `_field_summary.md` + `_extract.py`. |

## Configuration

```bash
# .env
CHATWOOT_ENABLED=true
CHATWOOT_URL=https://chatwoot.example.com         # no trailing slash
CHATWOOT_API_KEY=<user access token>              # Profile > Profile Settings > Access Token
CHATWOOT_ACCOUNT_ID=1                             # numeric account id from the URL
CHATWOOT_INBOX_ID=2                               # the inbox the adapter watches
CHATWOOT_WEBHOOK_SECRET=<per-webhook secret>      # see "Webhook setup" below
```

`CHATWOOT_ENABLED` is a sub-flag of the `messaging_external` bundle — the
bundle's master flag must also be on. The flag appears in
[Settings > Feature Flags](settings.md) so you can toggle it without
editing `.env`.

## Webhook setup

In Chatwoot UI: **Settings → Integrations → Webhooks → Add new webhook**.

| Field | Value |
|---|---|
| URL | `https://<your-roost-host>/api/chatwoot/webhook` |
| Subscriptions | At minimum: `message_created`. Optional: `conversation_created`, `conversation_updated`, `conversation_status_changed`, `contact_created`, `contact_updated` — the adapter ignores them safely but enabling them keeps your Chatwoot Audit Log complete. |

When you save the webhook Chatwoot generates a **secret** (visible once at
create-time, then again in the webhook's edit view). Copy it verbatim into
`CHATWOOT_WEBHOOK_SECRET` and restart Roost.

**Secret rotation:** the secret is per-webhook-row, not per-account. If
you delete and recreate the webhook in Chatwoot the secret changes — update
`.env` and restart.

## Signature verification

Chatwoot 4.14.1 signs each delivery with three headers:

| Header | Meaning |
|---|---|
| `X-Chatwoot-Timestamp` | Unix seconds when the webhook fired |
| `X-Chatwoot-Signature` | `sha256=<HMAC_SHA256(secret, "<ts>.<body>")>` |
| `X-Chatwoot-Delivery` | UUID per delivery; use for **idempotency / dedup**, never expect it to repeat |

The adapter rejects any request where `|now − ts| > 300s` (replay window)
or the digest doesn't match. Rejection returns **401**, not 500 — so
Chatwoot's retry logic does the right thing.

`User-Agent` is just `Ruby`; do not gate on it.

## What runs the inbound pipeline

Only `event == "message_created"` **and** `message_type == "incoming"`
runs lead-ingest / recipes / Telegram notify. Everything else (outgoing
agent replies, lifecycle events, contact updates) is acked with 200 and
otherwise ignored.

The critical case is `conversation_updated` — Chatwoot fires it on
**every** conversation mutation: status flips, agent assignment, labels,
even who's currently typing. Routing it through the recipe pipeline would
flood the operator with notifications and run drafts on no new content.
The adapter pins this rule explicitly; the test suite asserts it.

## Envelope quirks to know about

1. **`message_type` is a string at top level, an int when nested.**
   Top-level `"incoming"` / `"outgoing"` / `"template"` / `"activity"` is
   safe to switch on; `conversation.messages[].message_type` (0/1/2/3) is
   for Chatwoot's UI and shouldn't be read.
2. **`conversation.channel` is a Rails STI string.** `Channel::Whatsapp`
   (production), `Channel::Api` (Roost's sandbox), `Channel::WebWidget`,
   `Channel::Email`, … The adapter records this in the inbound dict so
   downstream code can branch.
3. **`source_id` lives in `conversation.contact_inbox.source_id`.** For
   `Channel::Whatsapp` it's the E.164 phone; for `Channel::Api` it's a
   UUID Chatwoot generates. This is the canonical "who-to-reply-to" key
   — `sender.phone_number` only exists for contacts (not anonymous
   web-widget visitors).
4. **`sender` shape varies by direction.** Incoming →
   `payload.sender.type == "contact"` with `phone_number` / `email`.
   Outgoing → `payload.sender` is an agent `User`. The parser projects
   the customer contact from `conversation.meta.sender` on outgoing
   events so the contact view stays consistent.
5. **`contact_created` has no top-level `account`.** The account id has
   to come from elsewhere (env `CHATWOOT_ACCOUNT_ID` for single-account
   installs).

For the full list with worked examples, see
[`docs/chatwoot-webhook-samples/README.md`](chatwoot-webhook-samples/README.md).

## Outbound routing (FA edition)

When `CHATWOOT_ENABLED=true`, Roost's WhatsApp service routes outbound
calls **through Chatwoot** instead of going direct to Meta. This keeps the
FA edition on one channel for both directions — the salesperson sees
Roost's replies in the same Chatwoot conversation thread as their own.

| `services/whatsapp.py` function | `CHATWOOT_ENABLED=false` | `CHATWOOT_ENABLED=true` |
|---|---|---|
| `send_text_message(to, body)` | direct to Meta Cloud API | `chatwoot.route_text_to_whatsapp` — find-or-create contact → reuse open conversation if one exists, else open a new one with `initial_message=body` |
| `send_template_message(to, template_name, language_code, components)` | direct to Meta | `chatwoot.route_template_to_whatsapp` — find-or-create contact → reuse/open conv → `POST /messages` with `template_params` (Chatwoot fires the template against WABA). Meta-style `components` are translated to Chatwoot's `processed_params={"1": "...", "2": "..."}` shape. Header/button params drop; for those use Chatwoot REST directly |
| `send_document(...)` / `send_image(...)` with `path=` | direct to Meta | `chatwoot.route_media_to_whatsapp` — multipart `POST /messages` with `attachments[]`. Chatwoot handles the upload-to-Meta dance internally |
| `send_document(...)` / `send_image(...)` with `link=` or `media_id=` | direct to Meta | **errors** — Chatwoot accepts file bytes, not Meta media ids or external URLs. Download the file first and pass `path=` |
| `mark_as_read(wamid)` | direct to Meta | **no-op** — the api_chatwoot inbound handler already bumps `update_last_seen` on the Chatwoot side |
| `upload_media(...)` | unchanged | unchanged (Meta-only primitive; not reached when Chatwoot owns the upload) |

**Template listing.** `chatwoot.list_templates(inbox_id)` returns the
approved-template list synced from WABA via
`GET /api/v1/accounts/:aid/inboxes/:iid` (the `message_templates` field).
Use this to surface template names to operators rather than hard-coding.

Callers don't need to branch on `CHATWOOT_ENABLED` — the redirect happens
inside the service. The return shape from `send_text_message` stays
`{"ok": True, "message_id": <opaque>, "via": "chatwoot", "conversation_id": int}`;
`message_id` is Chatwoot's numeric id (not a Meta wamid) when routed —
callers should treat it as opaque, and they already do (it's only used
for logging).

**Loop safety:** A Roost-posted message fires a `message_created` webhook
with `message_type="outgoing"`. The api_chatwoot router filters this out
of the inbound pipeline (covered by `test_post_ignores_outgoing_message`),
so no echo-loop risk.

**Known limitations:**

- **Media via `link=` / `media_id=`** — Chatwoot's REST takes file bytes,
  not external URLs or Meta media ids. Callers that already hold a URL
  must download to a local path first and pass `path=`. RPA flows with
  `last_download` already have a local path, so they Just Work.
- **Template authoring** — Roost fires templates by name but does not
  create them. Template creation, edits, and approval status live in
  Chatwoot's WhatsApp template UI (which proxies WABA's submit-for-review
  flow). Use `chatwoot.list_templates` to discover what's available.
- **Rich template components** — Meta-style header media or button
  parameters don't translate to Chatwoot's flat `processed_params` shape.
  Body text variables work; for anything richer, talk to Chatwoot's REST
  directly with a hand-built `template_params` dict.

## Outbound API surface

| Function | Endpoint | When to use |
|---|---|---|
| `send_message(conv_id, content, message_type="outgoing", private=False)` | `POST /api/v1/accounts/:aid/conversations/:cid/messages` | Reply in an existing conversation. `private=True` posts an internal note (timeline-visible, customer-invisible) — useful for agent handoff. |
| `create_conversation(source_id=, inbox_id=, contact_id=, initial_message=)` | `POST /api/v1/accounts/:aid/conversations` | Open a new conversation against a contact_inbox (Roost-initiated outreach). |
| `find_or_create_contact(phone=, email=, name=, inbox_id=)` | `GET /contacts/search` then `POST /contacts` | Used before `create_conversation` to make sure the contact exists. Returns `source_id` for the target inbox so the caller knows the channel-specific routing key. |
| `mark_as_read(conv_id)` | `POST /conversations/:cid/update_last_seen` | Clear the unread badge after Roost processes a message. The Chatwoot adapter does this automatically inside `_process_inbound`. |
| `mark_as_resolved(conv_id)` | `POST /conversations/:cid/toggle_status` | Close a conversation. Use when an automated flow has reached a terminal state and there's nothing for a human agent to do. |

All return `{"ok": True, ...}` on success or `{"error": str, "details": ...}`
on failure. Network errors and non-2xx responses are logged at WARNING but
never raise — callers can decide whether to retry.

## FA edition context

The FA (Financial Advisor) edition install bundles Chatwoot in
`docker-compose.fa.yml` with Postgres / Redis / Sidekiq alongside Roost
itself. WhatsApp Cloud credentials are entered into Chatwoot's inbox
setup, not Roost's `.env` — Chatwoot owns the relationship with Meta, and
Roost speaks to Chatwoot. This is why `env-templates/fa.env` has both
`CHATWOOT_*` and `WHATSAPP_*` keys but only the former drive the Roost
adapter.

Other deployment shapes (laptop / hosted-by-you / VPS+domain) do not ship
Chatwoot by default — set `CHATWOOT_ENABLED=false` and use the WhatsApp
adapter directly.

## See also

- [WhatsApp Adapter](whatsapp-adapter.md) — the direct-to-Meta path, used
  when there's no Chatwoot in the picture.
- [Lead Nurture](lead-nurture.md) — the cadence engine that consumes
  inbound messages once the adapter ingests them.
- [`chatwoot-webhook-samples/README.md`](chatwoot-webhook-samples/README.md)
  — captured payloads, sandbox reproduction recipe (including the Rails
  runner bypass for Chatwoot's onboarding silent-rescue).
