# Roost — Live Demo Runbook

A three-track tour for showing Roost on `roost.ethanseow.com` to prospects.
Pick the track that matches the audience; switch mid-demo if the room reacts.

> **Audience options**
> - **Track A — Singapore property agent** (CEA-registered salesperson)
> - **Track B — SME owner / operator** (cafe, agency, kiosk reseller)
> - **Track C — Generic agent-platform / productivity prospect**

Each track is ~7 minutes. Together with intro + Q&A: ~30 min.

---

## 0 — Pre-demo checklist (do once, day-of)

```bash
# 1. Stack healthy?
curl -fsS https://roost.ethanseow.com/health     # → {"ok":true}

# 2. Login works?
#    Open https://roost.ethanseow.com in a clean browser tab
#    Username: admin
#    Password: <from /home/dev/roost/.env on VPS — WEB_PASSWORD>

# 3. Claude Code CLI authenticated inside container?
ssh root@178.105.175.105 \
  'cd /home/dev/roost && docker compose exec -u dev roost claude --version'
#    → 2.1.150 (Claude Code)
#    If "Please run claude login first" appears, do it:
ssh -t root@178.105.175.105 \
  'cd /home/dev/roost && docker compose exec -u dev -it roost claude login'

# 4. Demo data seeded?
ssh root@178.105.175.105 'cd /home/dev/roost && docker compose exec -T -u dev roost python -c "
from roost.services import projects, tasks, contacts
print(f\"Projects: {len(projects.list_projects())}, Tasks: {len(tasks.list_tasks())}, Contacts: {len(contacts.list_contacts())}\")
"'
#    → Projects: 3, Tasks: 11, Contacts: 5

# 5. Chromium sidecar (for RPA pause demo) healthy?
curl -fsS -o /dev/null -w "%{http_code}\n" https://roost.ethanseow.com/sidecar
#    → 307 (auth-gated; 307 is expected, you'll log in via the UI)
```

If any step fails, see **§Troubleshooting** below.

### Wire status — what's live vs. narrate-only

Check this before each demo. "Live" = real API call returns real data. "Narrate" = page renders the UI but the underlying call is gated; describe the flow verbally.

| Bundle / Surface           | Status                | How to flip live                                   |
|----------------------------|-----------------------|----------------------------------------------------|
| Local CRM (contacts/orgs)  | **Live** (seeded)     | Already on. 5 contacts × 2 orgs in the local DB.   |
| Property-Agent IRAS calc   | **Live**              | Pure-Python. No external dep.                      |
| Property-Agent HDB EIP RPA | **Live**              | Browser flow via chromium sidecar.                 |
| Property-Agent DNC scrub   | **Narrate**           | Requires IMDA registration — keep off for demo.    |
| Property-Agent CDD screen  | **Narrate**           | Requires ComplyAdvantage key — keep off for demo.  |
| Lead Nurture pipeline      | **Live** (in-platform)| Already on. Webhook ingest + cadence drafts.       |
| Guardian draft queue       | **Live**              | Already on. Wraps any money-moving tool call.      |
| RPA framework              | **Live**              | YAML library seeded. Sidecar healthy.              |
| Agentic chat (`/agentic`)  | **Live** *after `claude login`* | `docker compose exec -u dev -it roost claude login` |
| Attio CRM                  | **Narrate** *(flip-ready)* | Get key → fill `integrations.env.template` → run `./apply-integrations.sh` |
| Stripe (test mode)         | **Narrate** *(flip-ready)* | Same as above. Test-mode key only — no card needed.|
| WhatsApp Cloud             | **Narrate** *(flip-ready)* | Same as above. Refresh Meta access token before demo (24h validity). |
| Shopify / Xero / WeChat    | **Narrate**           | Page renders unconfigured; not in this demo's scope. |

To flip a "flip-ready" row live: SSH to the VPS, edit `/home/dev/roost/integrations.env.template`, run `./apply-integrations.sh` from that directory. Script auto-restarts roost and runs a health check.

---

## Intro (90 seconds, every track)

> "Roost is a self-hosted AI agent platform. Three interfaces share one brain:
> a web chat at the URL you see, a Telegram bot, and a 270+ tool MCP server
> for Claude Code. What makes it interesting is two things —
>
> **One** — it bills against *your existing* Claude / Gemini / Codex
> subscription, so there's no separate per-token API bill.
>
> **Two** — verticals like the Property-Agent toolkit and SME Ops are
> *bundles*. They turn on or off with a single env flag. The same engine
> can be configured for any team."

Show: `/settings` page — point at the Feature Flags column → "every box you see is a bundle that can be flipped on or off."

---

## Track A — Singapore property agent (~7 min)

**Persona:** Mei Ling Tan, CEA-registered salesperson, mid-career.
**Premise:** She has 12 cold leads from a roadshow and a buyer asking about ABSD.

### A1 — Compliance is the moat (1 min)
Navigate: `/property-agent/stamp-duty`

> "Every salesperson in Singapore has to know IRAS ABSD/BSD rates cold.
> Roost does it instantly and shows the working — so she can paste this
> in WhatsApp to the buyer."

Type in: **buyer profile**, second property, **$1.8M**, SC + PR couple.
Click **Calculate**.

> *Show the ABSD breakdown with the post-27-Apr-2023 rates.*

### A2 — Pre-cold-call DNC scrub (1.5 min)
Navigate: `/property-agent/dnc-scrub`

> "PDPA Section 43 — you can be personally fined for cold-calling a number
> on the Do-Not-Call list. Roost batches up to 100 numbers, scrubs in
> seconds, and gives her a clean call sheet."

Paste 5 sample numbers (or load the seeded prospect list). Click **Scrub**.

> *Point to the per-number disposition: which are clear, which to remove.*

> "Right now this is in **dry-run mode** — to make it live, you provide
> your own org's PDPC DNC API credentials. Sub-$200/year for unlimited
> lookups."

### A3 — CDD / AML check (1.5 min)
Navigate: `/property-agent/cdd-screen`

> "CEA PC 01-21 / 02-23: agents must do customer due diligence for AML.
> Sanctions, PEP screening, adverse media. We use ComplyAdvantage under
> the hood but the adapter is vendor-agnostic — swap for Refinitiv,
> Dow Jones, whatever your firm pays for."

Type in: **Mei Ling's buyer** name. Click **Screen**.

> *(Currently in dry-run; show the expected output shape.)*

### A4 — RPA: HDB EIP quota check with Singpass pause (2 min)

This is the **wow moment**. Navigate: `/rpa` → run **HDB EIP Quota Check**.

> "Some checks need Singpass. The flow runs as far as it can, then *pauses*
> and hands the live browser back to the agent. She logs in via Singpass
> through her phone in the live window — Roost picks up where it left off."

Click run. When the flow hits `await_user_session`, the chromium sidecar
opens. Point at it:

> "That's the live browser inside the container. She drives it. The agent
> watches the page state and resumes when she's done."

(For demo, skip the actual login — just narrate what would happen.)

### A5 — One-liner takeaway (30s)

> "Property-Agent bundle: IRAS + PDPC + CEA compliance in one surface,
> plus the RPA scaffold for any Singpass-gated workflow. Two of these
> alone would justify the subscription. CDD/DNC need org-level API
> credentials; the rest works out of the box."

---

## Track B — SME owner / operator (~7 min)

**Persona:** Sarah Lim, owner of Brew Coffee Co, 5 staff, two outlets.
**Premise:** End of month — she needs to reconcile, send invoices, see cashflow.

### B1 — Daily morning summary (1 min)

Show: Telegram bot (or `/sme/sync-status`).

> "Every morning at 7am, the bot pings her a one-tap summary:
> revenue yesterday, refunds pending, Stripe payouts due, calendar.
> If something needs her attention, it's right there."

### B2 — Stripe + Shopify + Xero unified (2 min)
Navigate: `/sme/sync-status`

> "Three of the most painful SaaS bills for any small business —
> Stripe for payments, Shopify for the storefront, Xero for accounting.
> Roost is a bridge: it pulls from each and shows her one truth."

> "Refunds, order disputes, recurring invoices — they all happen here.
> She approves; Roost executes."

Navigate: `/sme/orders` → show seeded order list.
Navigate: `/sme/cashflow` → show seeded cashflow view.

### B3 — Guardian approval queue (1.5 min)

> "Now the *interesting* part. AI agents that can move money are scary.
> Roost ships with **Guardian** — anything that touches money is queued
> as a draft. The agent doesn't refund the customer; it *proposes* a
> refund. Sarah approves with one tap."

Navigate: `/settings` → Guardian tab → show the draft queue widget.

> "There are limits: a max per run, a max per day. Set by you, enforced
> by the engine. This is the difference between 'demo' and 'I'd trust
> this with my cashflow'."

### B4 — Zapier passthrough (1 min)

> "We can't natively integrate with every SaaS — there are hundreds.
> So we ship a generic Zapier connector. Anything Zapier supports,
> Roost can trigger or receive. That's the 'long tail' covered."

### B5 — Demo data on the page (30s)

> "What you're seeing isn't a placeholder screen — that's real seed data
> in the database. The agent can query it, summarise it, draft outreach
> based on it. Try it: I'll ask the bot to summarise this week's refunds."

> *(Switch to the agent chat tab — see Track C, step C2.)*

### B6 — One-liner takeaway (30s)

> "SME Ops bundle: Stripe/Shopify/Xero in one place, Guardian for safety,
> Zapier for the long tail. Plug in your three test-mode API keys and
> the whole thing lights up in five minutes."

---

## Track C — Generic agent-platform / productivity prospect (~7 min)

**Persona:** Tech lead or COO evaluating "should we host our own agent?"

### C1 — The settings page is the pitch (1 min)
Navigate: `/settings`

> "Before I show anything dynamic — look at this page. Every flag here
> is a feature that can be on or off. WhatsApp adapter, Slack adapter,
> Telegram bot, CRM provider, property-agent bundle, SME bundle, agentic
> workflow. Self-hosted means *you* decide what's compiled in."

### C2 — Agentic chat with plan-approve-execute (3 min)

Navigate: `/agentic`

> "This is the agent surface. You give it a goal; it builds a plan;
> you approve; it executes — streaming tool calls live."

Type:
```
Summarise my open tasks and group them by project. Then suggest the top
3 I should focus on today and why.
```

> *(The agent will read tasks, group them, return a focus shortlist.
> Live tool calls — list_tasks, get_focus_tasks, suggest_focus — stream
> across the screen.)*

> "Three tool calls in 2.4 seconds. The plan was visible *before* execution.
> If I'd disapproved, nothing would have happened. This is what
> 'agent you can trust with prod' looks like."

### C3 — The MCP tool catalogue (1.5 min)
Navigate: `/settings` → MCP tab (or the agentic page's tool list)

> "270+ MCP tools — calendar, email, tasks, contacts, files, Stripe,
> Xero, Shopify, Attio, Notion, AML screening, IRAS stamp duty. Every
> one of these is callable by *any* MCP-aware agent. Right now Claude
> Code, Gemini CLI and Codex CLI can all use them — your choice of vendor."

### C4 — Subscription billing, not API billing (1 min)

Show settings → AI Provider tab.

> "The default is `claude_cli` — that means the Claude Code subscription
> you already pay for. No separate API bill. Same goes for Gemini CLI
> and Codex. Roost is the *only* platform I'm aware of that wires
> subscription auth into a multi-channel agent surface."

> "If you want API-mode for higher concurrency, flip to `claude` or
> `openai` — same code path, different billing model."

### C5 — Deployment story (30s)

Show: `docs/deployment.md` (have it open in another tab).

> "Three deployment shapes: laptop (Docker Desktop), hosted-by-you (cloud
> VM + Caddy auto-TLS, what you're looking at now), or fully VPS+domain
> for a business. The middle one — what you see live — is one
> `docker compose up` and a DNS record. ~5 minutes."

### C6 — One-liner takeaway (30s)

> "Roost = self-hosted agent + 270 MCP tools + bundles for verticals.
> If your team needs an agent that can touch your data without leaking
> it to a vendor, this is what it looks like."

---

## Optional: closing demo flourish (2 min, any track)

> "One more thing — Roost ships its own RPA engine. YAML-defined browser
> flows with pause/resume for human-in-the-loop OTPs or Singpass logins."

Navigate: `/rpa` → pick **HDB EIP Quota Check** → run.

> "The flow tells you exactly where it paused, hands you a live browser,
> waits as long as it needs to, then continues from the same state.
> No 'restart from scratch' if the OTP took two minutes."

---

## To make WhatsApp / CRM / Stripe go *live* before the demo

The current VPS env has the WhatsApp / Attio / Stripe slots **empty**.
For a live walkthrough you need credentials. None of this is mandatory —
the bundle pages render fine without — but a live `whatsapp_send` is a
better demo than a screenshot.

### WhatsApp Cloud API (Meta test number) — 15 min

1. Go to https://developers.facebook.com/ → log in → My Apps → **Create App**
2. Type: **Business** → name it `roost-demo` (or anything)
3. In the app dashboard → **Add Product** → find **WhatsApp** → Set up
4. **Quickstart** tab gives you:
   - `phone_number_id` (the "Test number" Meta provides free)
   - A temporary access token (24h)
   - **Important:** generate a permanent token under Business Settings → System Users
5. Set the **webhook**:
   - URL: `https://roost.ethanseow.com/api/whatsapp/webhook`
   - Verify token: pick any string, copy it into `.env` as `WHATSAPP_VERIFY_TOKEN`
   - Subscribe to: `messages` event
6. Get the **App Secret** from App Settings → Basic
7. On the VPS, add to `/home/dev/roost/.env`:
   ```
   WHATSAPP_ENABLED=true
   WHATSAPP_PHONE_NUMBER_ID=<from step 4>
   WHATSAPP_ACCESS_TOKEN=<permanent token from step 4>
   WHATSAPP_VERIFY_TOKEN=<your random string from step 5>
   WHATSAPP_APP_SECRET=<from step 6>
   ```
8. `docker compose restart roost`
9. Test: WhatsApp message your Meta test number from your phone → it
   should appear in the Roost agent's incoming queue.

> **Note:** Meta's free test number can only message numbers you've added
> as recipients in the app dashboard. For broadcast you'd need a verified
> business — out of scope for demo.

### Attio CRM — 3 min

1. https://app.attio.com → sign up free → create a workspace `roost-demo`
2. Settings (top right) → **Developers** → API tokens → **Generate**
3. Copy the token
4. On the VPS:
   ```
   CRM_PROVIDER=attio
   ATTIO_API_KEY=<paste here>
   ```
5. `docker compose restart roost`
6. Test: in `/agentic`, ask the agent to create a person in Attio.

### Stripe (test mode) — 5 min

1. https://dashboard.stripe.com → sign up (no card required for test mode)
2. Toggle the dashboard to **Test mode** (top right)
3. Developers → API keys → reveal **Secret key** (`sk_test_...`)
4. Developers → Webhooks → **Add endpoint**:
   - URL: `https://roost.ethanseow.com/api/stripe/webhook`
   - Events: `payment_intent.succeeded`, `charge.refunded`,
     `invoice.payment_failed`
   - Copy the **signing secret** (`whsec_...`)
5. On the VPS:
   ```
   STRIPE_ENABLED=true
   STRIPE_API_KEY=sk_test_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```
6. `docker compose restart roost`
7. Test: send a webhook from Stripe dashboard → see it land at
   `/sme/sync-status`.

---

## Troubleshooting

### "Container restarting"
Almost always permissions on the bind-mounted auth dirs.
```bash
ssh root@178.105.175.105
chown -R 1000:1000 /home/dev/roost/{claude,gemini,codex}-auth
docker compose restart roost
```

### "Agent chat returns 'No AI provider configured'"
The CLI hasn't been logged in. Inside the container:
```bash
ssh -t root@178.105.175.105 \
  'cd /home/dev/roost && docker compose exec -u dev -it roost claude login'
```

### "RPA flow hangs forever"
The chromium sidecar didn't start. Check:
```bash
ssh root@178.105.175.105 'cd /home/dev/roost && docker compose ps chromium'
```
Restart if `Exited`: `docker compose restart chromium`.

### Stack lockout — can't log in to web UI
1. SSH to VPS → read password: `grep WEB_PASSWORD /home/dev/roost/.env`
2. If forgotten, regenerate:
   ```bash
   cd /home/dev/roost
   NEWPW=$(openssl rand -hex 12)
   sed -i "s|^WEB_PASSWORD=.*|WEB_PASSWORD=$NEWPW|" .env
   docker compose restart roost
   echo "New password: $NEWPW"
   ```

---

## After the demo

If you want to leave the demo VPS running and accessible to the prospect
for a "try-it-yourself" period:

- The current WEB_PASSWORD is shared with you privately
- Tell them: `https://roost.ethanseow.com`, username `admin`, password as given
- Set a calendar reminder to tear down if not converting: see
  `roost_demo_vps.md` for the `hcloud server delete` one-liner

To tear down completely:
```bash
hcloud server delete roost-vps
doctl compute domain records delete ethanseow.com 1819957024
```
