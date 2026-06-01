# Chatwoot 4.14.1 — Captured Webhook Payloads

Reference shapes for the `messaging_external` bundle's Chatwoot adapter (FA-A).
Captured 2026-06-01 against the live sandbox at `chat.roost.ethanseow.com`
using an account-level webhook + a `Channel::Api` inbox (no real WhatsApp
credentials needed — the envelope is identical, only `conversation.channel`
changes from `Channel::Api` to `Channel::Whatsapp` in production).

Re-running: `_extract.py` parses `raw-webhook-site-dump.json` into the
per-event files. The raw dump and `.creds.local` are gitignored.

## Files

| File | Event | Fired by |
|---|---|---|
| `01_contact_created.json` | `contact_created` | `POST /api/v1/accounts/{aid}/contacts` |
| `02_conversation_created.json` | `conversation_created` | `POST /api/v1/accounts/{aid}/conversations` |
| `03_message_created_incoming.json` | `message_created` (customer side) | `POST /conversations/{cid}/messages` with `message_type=incoming` |
| `04_message_created_outgoing.json` | `message_created` (agent side) | `POST /conversations/{cid}/messages` with `message_type=outgoing` |
| `05_contact_updated.json` | `contact_updated` | `PATCH /contacts/{id}` |
| `06_conversation_updated.json` | `conversation_updated` | Side-effect — fires alongside almost every conversation mutation |
| `07_conversation_status_changed.json` | `conversation_status_changed` | `POST /conversations/{cid}/toggle_status` |

`message_updated` and `webwidget_triggered` exist as subscriptions but
weren't worth a separate fire here — the envelope mirrors `message_created`
/ `conversation_created` respectively.

## Headers (what the adapter actually receives)

| Header | Example | Notes |
|---|---|---|
| `Content-Type` | `application/json` | always |
| `User-Agent` | `Ruby` | unhelpful — don't gate on it |
| `X-Chatwoot-Delivery` | `4b7078a4-ed3f-4f47-82cc-f38d3c36cf2e` | UUID per delivery — use for **idempotency / dedup**, never expect it to repeat |
| `X-Chatwoot-Timestamp` | `1780328843` | Unix seconds. Reject if `\|now − ts\| > 300s` (replay window). |
| `X-Chatwoot-Signature` | `sha256=4097ae...` | HMAC-SHA256, see below |

### Signature verification

`/app/lib/webhooks/trigger.rb` (Chatwoot 4.14.1) signs like:

```ruby
ts   = Time.now.to_i.to_s
body = payload.to_json
sig  = "sha256=#{OpenSSL::HMAC.hexdigest('SHA256', secret, "#{ts}.#{body}")}"
```

So the adapter must:

```python
def verify(secret: str, ts: str, body: bytes, header: str) -> bool:
    if not (secret and ts and body and header.startswith("sha256=")):
        return False
    expected = hmac.new(
        secret.encode(),
        f"{ts}.{body.decode()}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(header, f"sha256={expected}")
```

The shared secret is the `webhooks.secret` column, returned to the user
once at webhook-create time via the Chatwoot UI/API. Mirror this into the
self-hoster's `.env` as `CHATWOOT_WEBHOOK_SECRET` — never store it in
the adapter's database (no need; verify on the request thread).

Webhook secret **is per-webhook-row**, not per-account. If the user
rotates by deleting + recreating the webhook in Chatwoot UI, the secret
changes. Treat `CHATWOOT_WEBHOOK_SECRET` as a rotatable env value.

## Envelope quirks worth pinning down

These are 4.14.1-specific shapes the adapter has to handle:

1. **`message_type` is a string at the top level, an int nested.**
   - Top-level (commit-safe to switch on): `"incoming"` | `"outgoing"` | `"template"` | `"activity"`
   - Inside `conversation.messages[].message_type`: `0` (incoming) / `1` (outgoing) / `2` (activity) / `3` (template). Don't read these — use the string.

2. **Conversation channel is the gatekeeper for adapter behavior.**
   `conversation.channel` is a Rails STI-style string: `Channel::Api`,
   `Channel::Whatsapp`, `Channel::FacebookPage`, `Channel::WebWidget`,
   `Channel::Email`, etc. The adapter only cares about
   `Channel::Whatsapp` (Roost → outbound) and possibly `Channel::Api` for
   the sandbox; everything else can early-return.

3. **`sender` shape varies by direction.**
   - Incoming: `sender.type == "contact"`, has `phone_number`, `email`,
     no `account_id` at the sender level (it's at top-level
     `account.id`).
   - Outgoing: `sender` is an agent (`type` not present in our sample —
     it's a `User`); has `name`, `available_name`, no `phone_number`.
   - The adapter should branch on top-level `message_type`, not try to
     sniff sender shape.

4. **`source_id` is in `conversation.contact_inbox.source_id`.**
   For `Channel::Whatsapp` that string IS the customer's E.164 phone
   number (`+6591234567`). For `Channel::Api` it's a UUID Chatwoot
   generates. The adapter's "who did this message come from / who to
   route the reply to" lookup goes through this field, not through
   `sender.phone_number` (which only exists for contacts, not for
   anonymous web-widget visitors).

5. **`conversation_created`, `conversation_updated`,
   `conversation_status_changed` share the same envelope** (24 vs 23
   top-level keys — `changed_attributes` is the differentiator on
   updated/status_changed). Don't write three parsers; one parser, three
   thin event hooks.

6. **`conversation_updated` is **chatty**.** Almost every mutation fires
   one. The adapter must NOT trigger AI / draft / Telegram-notify
   pipelines on this event — only `message_created` (incoming) should.
   Treat `conversation_updated` as a structural sync ping, not an
   "action" event.

7. **`contact_created` has no `account` key** — only `account_id` at
   top level isn't even there. Get the account from `inbox.account_id`
   (if your account is multi-tenant). Single-account installs can
   hardcode account 1 from env.

8. **No `inbox_identifier` on the API-channel webhook** (it's part of
   the inbox API response, not the event envelope). Per-inbox routing
   uses the integer `inbox.id`.

## Sandbox state for reproduction

- Chatwoot URL: `https://chat.roost.ethanseow.com` (Caddy → chatwoot:3000)
- Admin: stored in `.creds.local` (gitignored)
- Account ID: `4` (the failed onboarding attempts advanced both sequences)
- Inbox: `1` (`FA-A Sandbox`, `Channel::Api`)
- Webhook ID: `1`, secret in `.creds.local`, fires to webhook.site UUID also there

To reproduce on a fresh Chatwoot install:

```
# 1. Reset onboarding lock in Redis if you've already used it
docker exec roost-chatwoot-redis-1 redis-cli set CHATWOOT_INSTALLATION_ONBOARDING true

# 2. Bypass the silent rescue + password validator by creating the admin
#    via Rails runner with a strong password (uppercase + special required).
docker exec roost-chatwoot-1 bundle exec rails runner '
  user, account = AccountBuilder.new(
    account_name: "Verixiom",
    user_full_name: "Ethan Seow",
    email: "ethan+chatwoot@verixiom.com",
    user_password: "<strong-pw>",
    super_admin: true,
    confirmed: true
  ).perform
  puts user.access_token.token
'
```

The HTML form at `/installation/onboarding` will silently redirect to `/`
on password-validation failure (`Installation::OnboardingController#create`
rescues `StandardError`, sets a flash, returns 302). Bypass via Rails
runner or use a password matching `/[A-Z]/`, `/[!@#$%^&*()_+\-=\[\]{}|"\/\\.,`<>:;?~']/`.
