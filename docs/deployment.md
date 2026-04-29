# Deployment Shapes

Roost is built to run in three places. Pick the one that matches what
the user can realistically operate.

| Shape | Who it's for | Domain? | TLS? | Live browser URL |
|---|---|---|---|---|
| [1. Laptop](#1-laptop) | Solo agent on their own machine | No | No (localhost) | `http://localhost:3000` |
| [2. Hosted-by-you](#2-hosted-by-you) | You operate Roost-as-a-service for clients | Yes (yours) | Yes (your wildcard / per-tenant) | `https://<tenant>.roost.example/sidecar` |
| [3. Their own VPS + domain](#3-their-own-vps--domain) | Solo user, full self-host. **Caddy auto-TLS bundled** via `docker-compose.public.yml` | Yes (theirs) | Yes (Let's Encrypt, automatic) | `https://roost.<theirs>.com/sidecar` |

The legacy "VPS without a domain" path (raw IP, Tailscale, Cloudflare
Tunnel) still works — `SIDECAR_PUBLIC_URL` accepts any URL — but it
isn't a default-supported shape. If you find yourself there, see the
[fallback section](#fallback-vps-without-a-domain).

---

## 1. Laptop

**The default for solo property / insurance agents.** Roost runs in
Docker on the user's own machine alongside whatever browser they
already use. Singpass, broker logins, captcha — all happen in the
same browser they're already sitting at.

```bash
git clone https://github.com/<you>/roost.git
cd roost
cp env-templates/.env.example .env
# fill in TELEGRAM_BOT_TOKEN, ATTIO_API_KEY (or other CRM), etc.
docker compose up -d
```

That's it. No env tweaks needed for the live-browser flow:
`docker-compose.yml` ships with `127.0.0.1:3000:3000` for the chromium
sidecar and `SIDECAR_PUBLIC_URL` defaults to `http://localhost:3000`.

When an `await_user_session` step pauses, the Telegram prompt links to
`http://localhost:3000/devtools/...` — they click, complete Singpass
in the visible browser, flow resumes.

**Tradeoffs:** flows only run when the laptop is on. No remote access
from the agent's phone. Recipes still fire from cron, but only while
the laptop is awake.

---

## 2. Hosted-by-you

**For multi-tenant operators** — you run a Roost cluster, customers
sign up, each gets their own instance.

The domain belongs to **you**. Each tenant lives at a subdomain
(`alice.roost.example`, `bob.roost.example`) or path (`/t/alice/`),
fronted by your reverse proxy + TLS.

In each tenant's `.env`:

```bash
SIDECAR_PUBLIC_URL=https://alice.roost.example/sidecar
```

The built-in `/sidecar` proxy (FastAPI + WS bridge in
`roost/web/api_sidecar.py`) handles auth, devtools assets, and the CDP
WebSocket through the same domain Roost itself runs on. No separate
sidecar hostname required.

**The user never thinks about infra.** They sign up, click the
Telegram link when an RPA flow asks for Singpass, complete it in
their phone-based Singpass app, the flow resumes.

**Operator checklist:**
- TLS terminates at your reverse proxy (Caddy / nginx / Cloudflare).
  WebSocket upgrade must be passed through unchanged.
- Each tenant gets its own `roost-data` volume and its own chromium
  sidecar instance — do **not** share a sidecar across tenants
  (cookie/storage state would leak).
- `SESSION_SECRET` and credentials are per-tenant; `cryptography`
  Fernet keys must be unique per tenant.

---

## 3. Their own VPS + domain

**For technical solo users who want full self-host** — they own a
small cloud VPS (Hetzner, DO, Vultr, OVH — any $5–10/mo box) and a
cheap domain (`$10/year`).

**Roost ships a Caddy sidecar for this case** (`docker-compose.public.yml`)
so the user doesn't install or configure Caddy manually. Three steps:

```bash
# 1. DNS — point an A record at the VPS
roost.<theirdomain>.com  →  <VPS public IP>

# 2. .env (append to whatever model template you started with)
ROOST_DOMAIN=roost.example.com
ROOST_ADMIN_EMAIL=you@example.com   # Let's Encrypt account

# 3. Bring it up with the public overlay
docker compose -f docker-compose.yml -f docker-compose.public.yml up -d
```

That's the whole setup. The overlay:

- Adds a Caddy 2 container that auto-issues + auto-renews Let's
  Encrypt certs for `ROOST_DOMAIN`.
- Stops binding `8080` and `3000` on the host — only Caddy is publicly
  reachable, so an unconfigured firewall isn't a foot-gun.
- Sets `SIDECAR_PUBLIC_URL=https://${ROOST_DOMAIN}/sidecar`
  automatically — no separate env tweak needed.
- Passes WebSocket upgrades through to the chromium proxy with 10-min
  timeouts (Singpass / payment flows can idle).

The Telegram link becomes `https://roost.example.com/sidecar/devtools/...`
— clean URL, green padlock, works on phone.

Sample template: [`env-templates/public-vps.env`](../env-templates/public-vps.env).

**Why this is genuinely fine for non-technical users:**

- A `.com` domain at a budget registrar (Porkbun / Cloudflare
  Registrar / Namecheap) is ~US$10–12/year.
- Caddy auto-renews TLS forever — zero ongoing cert work.
- VPS is ~US$5/month for an instance that comfortably runs Roost +
  chromium sidecar.
- Domain + VPS together cost less than one Netflix subscription per
  month.

The only operational job is "keep the box patched" — and even that
can be `docker compose pull && docker compose up -d` on a cron.

**Compared to laptop:** Roost is reachable from phone, runs while
laptop is asleep, scheduled flows actually fire reliably.

---

## Fallback: VPS without a domain

Still supported, just not the default. `SIDECAR_PUBLIC_URL` accepts
anything that resolves from the user's browser:

| Approach | `SIDECAR_PUBLIC_URL` | Notes |
|---|---|---|
| Raw IP + port | `http://203.0.113.10:8080/sidecar` | Browser warns "Not secure" but works. Auth still gates the proxy. |
| Cloudflare Tunnel | `https://random-name.trycloudflare.com/sidecar` | Free HTTPS hostname, one command: `cloudflared tunnel --url http://localhost:8080`. |
| Tailscale Funnel | `https://roost.tailfeather-xyz.ts.net/sidecar` | Free, auto-TLS, only works if user has Tailscale on their other devices. |
| `mDNS` (LAN only) | `http://roost.local:8080/sidecar` | Same-network only. Flaky on Windows. |

These are escape hatches, not the recommended path. **For most users,
the answer is "laptop" or "VPS + cheap domain".**

---

## Decision tree

```
Will the user need to reach Roost from devices other than their laptop?
├─ No  → Shape 1 (Laptop). Done.
└─ Yes
   ├─ Are they paying you to run Roost?
   │  └─ Yes → Shape 2 (Hosted-by-you).
   └─ No, they self-host
      ├─ Will they buy a domain (~$10/yr)?
      │  └─ Yes → Shape 3 (VPS + domain). Recommended.
      └─ No → Fallback (Cloudflare Tunnel is the least painful).
```

---

## Common knobs

| Env var | Default | When to change |
|---|---|---|
| `SIDECAR_PUBLIC_URL` | `http://localhost:3000` | Anytime you're not on the laptop default |
| `SIDECAR_INTERNAL_HTTP_URL` | `http://chromium:3000` | Only if the sidecar isn't named `chromium` in compose |
| `WEB_HOST` | `0.0.0.0` | Set to `127.0.0.1` when fronting with Caddy / nginx |
| `WEB_PORT` | `8080` | Free — only matters in the reverse-proxy block |
| `SESSION_SECRET` | random | Set explicitly when persisting across restarts |

See `env-templates/` for the full list per integration.
