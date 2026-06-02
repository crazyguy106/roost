# Roost FA Edition — Laptop Install

The **FA (Financial Adviser) edition** runs Roost on a laptop with Chatwoot
fronting WhatsApp / WeChat / Email and Tailscale Funnel making the Chatwoot
inbox reachable to providers like Meta. The salesperson sees one queue in
Chatwoot; Roost speaks Chatwoot REST for both directions.

This page is the laptop runbook. For the VPS shape — Caddy + Let's Encrypt
instead of Tailscale — start from [`docs/deployment.md`](deployment.md) and
add `docker-compose.fa-vps.yml`.

## What you get

- **Roost** on `http://127.0.0.1:8080` (web UI + MCP + scheduler; Telegram off).
- **Chatwoot 4.14.1** + Sidekiq + Postgres (pgvector) + Redis — the unified
  inbox the adviser actually works in.
- **Tailscale sidecar** publishing Chatwoot at a `https://<hostname>.<tailnet>.ts.net`
  Funnel URL so Meta / WhatsApp Cloud API can reach the webhook without DNS or
  port-forwarding.
- **Roost ↔ Chatwoot wiring** ready to go: signature-verified inbound
  (`/api/chatwoot/webhook`) and REST outbound. WhatsApp creds live in
  Chatwoot's inbox setup, **not** in Roost's `.env`. See
  [`docs/chatwoot.md`](chatwoot.md) for the adapter detail.

## Prerequisites

- Ubuntu or Debian laptop (other distros may work; not tested).
- ~6 GB RAM and 10 GB free disk (Chatwoot + Postgres + pgvector image alone is
  ~3 GB pulled).
- A [Tailscale](https://tailscale.com/) account on the free tier. The tailnet
  must have **MagicDNS** and **Funnel** enabled (Admin → DNS → MagicDNS;
  Admin → Settings → Funnel).
- A Meta Business / WhatsApp Cloud API account with at least one phone
  number ready to wire into Chatwoot's inbox setup (you can do this after
  the install).
- A Gemini API key (or another supported provider — edit `.env` after).

The laptop stays online to receive webhooks. Suspend kills the Funnel; close
the lid and Meta retries until the laptop comes back.

## One-line install

```bash
curl -fsSL https://raw.githubusercontent.com/crazyguy106/roost/feature/fa-edition/scripts/install-fa.sh | bash
```

The script:

1. Installs `git`, `curl`, `openssl`, Docker engine + compose plugin if missing.
2. Clones `feature/fa-edition` into `~/roost` (override with `ROOST_DIR=...`).
3. Copies `env-templates/fa.env` → `.env` at mode 0600.
4. Generates `SESSION_SECRET` (hex 32), `CHATWOOT_POSTGRES_PASSWORD` (hex 24),
   and `CHATWOOT_SECRET_KEY_BASE` (hex 64) — only if they still hold the
   `CHANGE_ME_*` sentinel, so re-runs are safe.
5. Prompts for `TAILSCALE_AUTHKEY` (skipped silently in non-interactive mode).
6. Creates host bind-mount dirs (`data/`, `claude-auth/`, `gemini-auth/`,
   `codex-auth/`, `backups/`, `roost-config/`).
7. `docker compose -f docker-compose.yml -f docker-compose.fa.yml pull && up -d`.
8. Waits up to 5 minutes for the Chatwoot healthcheck to flip green.
9. Greps the Tailscale sidecar log for the `https://*.ts.net` Funnel URL and
   prints it.

What it does **not** do (Chatwoot wizard steps below):

- Create the Chatwoot super-admin account.
- Create the WhatsApp inbox.
- Generate the API token or webhook secret.

## Generate a Tailscale auth key

Before the script prompts you:

1. Visit <https://login.tailscale.com/admin/settings/keys>.
2. **Generate auth key** → leave "Reusable" off; "Ephemeral" off; turn
   "Pre-approved" on if your tailnet requires device approval. Tag it with
   whatever name you use to recognise this laptop.
3. Copy the `tskey-auth-...` string and paste it when the installer asks.

If you skip it, the Tailscale sidecar will sit in a retry loop; fill it into
`.env` later and `docker compose ... restart tailscale`.

## Chatwoot first-boot wizard

When the installer prints the Funnel URL, open it in a browser. Chatwoot
shows a setup screen the first time it's hit.

1. **Create the super-admin user** — name, email, password. Email doesn't
   need to be deliverable for the wizard itself, but Chatwoot uses it for
   later notifications.
2. You'll land in the Chatwoot dashboard with an empty inbox list. Note your
   `account_id` (visible in the URL — usually `1` for a fresh install).
3. **Profile menu → Profile Settings → Access Token** → copy. This is the
   value of `CHATWOOT_API_KEY` in `.env`.

## Create the WhatsApp Cloud inbox in Chatwoot

WhatsApp Cloud credentials live **inside Chatwoot**, not in Roost. Chatwoot
owns the Meta relationship; Roost talks to Chatwoot.

1. **Settings → Inboxes → Add Inbox → WhatsApp → Cloud API**.
2. Paste the four values from your Meta WhatsApp Business app:
   - Phone Number ID
   - Business Account ID
   - Access Token (the permanent system-user token, not the temporary one)
   - App Secret
3. Save. Chatwoot will sync the phone number and surface a webhook URL it
   needs you to register on the Meta side.
4. **Open the inbox you just created.** The numeric inbox id is the last
   segment of the URL — `/app/accounts/1/settings/inboxes/<INBOX_ID>`.
   That's `CHATWOOT_INBOX_ID` in Roost's `.env`.

### Point Meta at Chatwoot

In the Meta App dashboard for your WhatsApp Business app:

- **WhatsApp → Configuration → Webhook URL**: the value Chatwoot showed in
  the inbox setup screen (it's a `https://<funnel-host>/webhooks/whatsapp/<phone-id>`
  endpoint — Chatwoot's, not Roost's).
- **Verify Token**: the value Chatwoot displays alongside.
- Subscribe to at least the `messages` field.

Once Meta verifies the webhook, every inbound WhatsApp message flows:

```
WhatsApp customer → Meta Cloud API → Chatwoot (via Funnel) → Chatwoot inbox
                                                    ↓
                                       Chatwoot webhook → Roost (/api/chatwoot/webhook)
```

## Register Roost as a Chatwoot webhook consumer

This is what gets inbound messages into Roost so the AI pipeline runs.

1. **Settings → Integrations → Webhooks → Add new webhook**.
2. **URL**: `https://<funnel-host>/api/chatwoot/webhook`
3. **Subscriptions**: at minimum `message_created`. Enabling
   `conversation_created`, `conversation_updated`, `conversation_status_changed`,
   `contact_created`, `contact_updated` is fine — Roost safely ignores them.
4. Save. Chatwoot shows the **secret** once at create-time. Copy it.
5. Paste into `.env` as `CHATWOOT_WEBHOOK_SECRET=<secret>`.

**The secret is per-webhook-row.** If you delete and recreate the webhook,
the value rotates and you must update `.env`.

## Finish wiring `.env`

After the wizard, edit `~/roost/.env` and fill in the values you collected:

```bash
CHATWOOT_API_KEY=<your access token>
CHATWOOT_INBOX_ID=<the inbox id you noted>
CHATWOOT_WEBHOOK_SECRET=<the per-webhook secret>
CHATWOOT_FRONTEND_URL=https://<funnel-host>     # the Tailscale URL
```

Other values worth checking now:

- `WEB_PASSWORD=` — change from `CHANGE_ME` to whatever password you want
  for the Roost web UI login.
- `GEMINI_API_KEY=` — paste your Gemini API key (or flip provider).
- `ATTIO_API_KEY=` — only if you want the Attio CRM integration.

Restart Roost so it picks up the new env vars:

```bash
cd ~/roost
docker compose -f docker-compose.yml -f docker-compose.fa.yml restart roost
```

## Smoke test

1. From a phone, WhatsApp the number tied to your Chatwoot inbox.
2. Open Chatwoot — the message should appear in the inbox.
3. Open Roost (`http://127.0.0.1:8080`) → `/leads` — the contact should appear
   with the inbound message logged on the conversation thread.
4. If Telegram is on, you should also get a notification with the parsed
   intent/urgency.

If nothing reaches Roost, check `roost` logs:

```bash
docker compose -f docker-compose.yml -f docker-compose.fa.yml logs roost
```

A `401` from `/api/chatwoot/webhook` means the secret in `.env` doesn't match
what Chatwoot is signing with — re-copy from the webhook edit page.

## Updates

Use `scripts/roost-update.sh` instead of raw `docker compose pull`. It
snapshots `.env`, the CLI auth dirs, `data/`, and a `pg_dump` of the Chatwoot
postgres into `backups/pre-update-<ISO>.tar.gz` (mode 0600) before pulling.

```bash
cd ~/roost
./scripts/roost-update.sh -f docker-compose.yml -f docker-compose.fa.yml
```

Override retention with `ROOST_BACKUP_KEEP=N` (default 5).

## Troubleshooting

**Chatwoot never goes healthy.**
The healthcheck is `wget -qO- http://127.0.0.1:3000/api`. If it stays
`starting` for >5 minutes, look at:

```bash
docker compose -f docker-compose.yml -f docker-compose.fa.yml logs chatwoot-init
docker compose -f docker-compose.yml -f docker-compose.fa.yml logs chatwoot
```

Most common cause: `CHATWOOT_POSTGRES_PASSWORD` or `CHATWOOT_SECRET_KEY_BASE`
still holds a `CHANGE_ME` sentinel. Edit `.env`, then `down && up -d` (a
plain `restart` won't re-read `.env` for env-file values).

**Tailscale sidecar can't register.**
`docker compose ... logs tailscale` will show the failure. The usual cause
is a missing or expired `TAILSCALE_AUTHKEY`. Generate a new key, paste into
`.env`, then `docker compose ... up -d --force-recreate tailscale`.

**Webhook returns 401.**
Either the secret in `.env` doesn't match the Chatwoot webhook row, or the
signed timestamp drifted >300s from the laptop clock. Confirm `date -u` is
sane; re-copy the secret from Chatwoot's webhook edit screen.

**Webhook returns 200 but nothing happens.**
The adapter intentionally ignores everything except `message_created` +
`incoming`. `conversation_updated` is chatty (typing indicators, label
changes) and would flood the AI pipeline if routed. This is by design — see
[`docs/chatwoot.md`](chatwoot.md) § What runs the inbound pipeline.

## See also

- [`docs/chatwoot.md`](chatwoot.md) — adapter reference: signature
  verification, envelope quirks, outbound routing.
- [`docs/whatsapp-adapter.md`](whatsapp-adapter.md) — direct-to-Meta path,
  used when there's no Chatwoot in the picture.
- [`docs/deployment.md`](deployment.md) — laptop / hosted-by-you /
  VPS+domain deployment shapes.
- [`docs/lead-nurture.md`](lead-nurture.md) — the cadence engine that
  consumes inbound messages once the adapter ingests them.
