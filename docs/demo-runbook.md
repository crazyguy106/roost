# Roost — Live Demo Runbook

A tour for showing Roost on `roost.ethanseow.com` to prospects.
Pick the track that matches the audience; switch mid-demo if the room reacts.

> **Audience options**
> - **⭐ Track A — Singapore property agent** (CEA-registered salesperson — *what the current box is wired for*)
> - **Track FA — Financial adviser** (solo FA) — *needs FA mode, see note*
> - **Track B — SME owner / operator** (cafe, agency, kiosk reseller) — *needs SME mode, see note*
> - **Track C — Generic agent-platform / productivity prospect**

Each track is ~7 minutes. Together with intro + Q&A: ~30 min. **The demo box is
currently in property mode** (`PROPERTY_AGENT_ENABLED=true`, `DEFAULT_VERTICAL=property`),
so run **Track A**. To switch editions it's `.env`-only, no rebuild — see the note after Track A.

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

# 4. Property demo data correct?
ssh root@178.105.175.105 'cd /home/dev/roost && docker compose exec -T -u dev roost python -c "
from roost.services import projects, tasks
print(f\"Projects: {len(projects.list_projects())}, Tasks: {len(tasks.list_tasks())}\")
from roost.extras.property_agent.services import iras_stamp_duty as i
b=i.calc_bsd(1_800_000); a,r=i.calc_absd(1_800_000,\"sc\",2)
print(f\"IRAS check — 2nd prop \$1.8M SC: BSD \${b:,} + ABSD \${a:,} ({r})\")
"'
#    → Projects: 3 (incl. Property Demo — Mei Ling), Tasks: 7
#    → IRAS check — 2nd prop $1.8M SC: BSD $59,600 + ABSD $360,000 (20%)
#    → Roost → People (/contacts) shows 4 property prospects ("Synced live from
#      Attio"). /leads should be EMPTY (populates live).

# 5. Chromium sidecar (for RPA pause demo) healthy?
curl -fsS -o /dev/null -w "%{http_code}\n" https://roost.ethanseow.com/sidecar
#    → 307 (auth-gated; 307 is expected, you'll log in via the UI)
```

If any step fails, see **§Troubleshooting** below.

### Wire status — what's live vs. disabled

Check this before each demo. "Live" = real API call returns real data. "Disabled" = bundle flag off; routes 404 (re-enable + rebuild to use).

| Bundle / Surface                       | Status        | Notes                                              |
|----------------------------------------|---------------|----------------------------------------------------|
| **IRAS stamp duty calc**               | **Live**      | Pure-Python ABSD/BSD/SSD (post-27-Apr-2023). The headline tool. |
| **HDB EIP/SPR quota check (RPA)**      | **Live**      | Browser flow + Singpass pause via the chromium sidecar. |
| PDPC DNC scrub                         | **Narrate**   | `DNC_ENABLED=false` — page renders dry-run; needs an IMDA org account. |
| CEA CDD / AML screen                   | **Narrate**   | `CDD_ENABLED=false` — dry-run; needs a ComplyAdvantage key. |
| **WhatsApp inbound (via Chatwoot)**    | **Live**      | `CHATWOOT_ENABLED=true` — real two-way WhatsApp.   |
| **Lead-nurture pipeline (`/leads`)**   | **Live**      | Property-buyer auto-qualify + cadence. Empty until a lead lands. |
| **Guardian hold → Telegram approve**   | **Live**      | Holds every free-form AI reply for approval.       |
| **Attio CRM + live People sync**       | **Live**      | 4 property prospects + deals + AI lead-scores; `/contacts` reads Attio directly. |
| Voice-memo → CRM                       | **Live**      | Telegram `memo:` → meeting note in Attio.          |
| Agentic chat (`/agentic`)              | **Live**      | `AGENT_PROVIDER=claude_cli`, already logged in.    |
| SME Ops (Stripe / Shopify / Xero)      | **Disabled**  | `SME_OPS_ENABLED=false` — routes 404.              |

Everything in the FA hero flow is **Live** — no flip-ready stand-ins. To bring a **Disabled** bundle back (e.g. to run the legacy property/SME tracks), flip its flag in `/home/dev/roost/.env` (`PROPERTY_AGENT_ENABLED=true` / `SME_OPS_ENABLED=true`) and rebuild: `docker compose up -d --build roost`.

---

## Intro (90 seconds, every track)

> "Roost is a self-hosted AI agent platform. Three interfaces share one brain:
> a web chat at the URL you see, a Telegram bot, and a 270+ tool MCP server
> for Claude Code. What makes it interesting is two things —
>
> **One** — it runs on *your existing* Claude / Gemini / Codex subscription.
> Headless agent runs draw from your plan's monthly **Agent SDK Credit**
> (Claude Pro $20 / Max $100–200); no API key, and only heavy use beyond that
> credit is pay-as-you-go.
>
> **Two** — verticals like the Property-Agent toolkit and SME Ops are
> *bundles*. They turn on or off with a single env flag. The same engine
> can be configured for any team."

Show: `/settings` page — point at the Feature Flags column → "every box you see is a bundle that can be flipped on or off."

---

## Track FA — Financial Adviser (the hero flow) (~7 min)

> **Needs FA mode.** The box is currently in property mode. To run this track, flip
> `DEFAULT_VERTICAL=financial_advisor` in `.env`, re-seed the FA prospects in Attio,
> then `docker compose up -d roost`. (No rebuild — it's an `.env` change.) The
> lead-nurturing loop below is identical; only the vertical's wording differs.

**Persona:** Rachel, a licensed financial adviser running solo. She fields WhatsApp
enquiries between client meetings and can't babysit a keyboard.
**Premise:** A prospect messages on WhatsApp. Rachel wants to qualify them, reply
*compliantly*, and have it land in her CRM — hands-free, but with her in control.

### FA-prep — day-of (1 min)
- `/leads` is **empty** — it fills live, don't pre-seed.
- Your **operator Telegram** app is open (this is where approvals land — clients never see it).
- The **Attio "Roost"** workspace is open in a second tab.
- Automations are **not paused**:
  `ssh root@178.105.175.105 'rm -f /home/dev/roost/data/automations_paused'`
- Your demo phone is a **whitelisted WhatsApp recipient** (the Meta test number only
  messages numbers you've pre-added in the app dashboard).

### FA1 — The empty pipeline (30s)
Sidebar → **Financial Advisor → Lead Pipeline** (`/leads`).
> "This is my live pipeline. Empty right now — watch it fill. I won't type a thing."

### FA2 — A lead messages on WhatsApp (1.5 min)
From your phone, WhatsApp the business line:
> *"Hi, saw your post on retirement planning. I'm 45 with no real plan yet — can you help?"*

It lands in Chatwoot; Roost picks it up. Refresh `/leads`:
> "There it is. Roost created the lead and it's already qualifying — zero data entry."

### FA3 — Compliant AI draft, held by Guardian (2 min) — *the hero beat*
Roost drafts a reply with Gemini; your **Telegram** pings with **Approve / Reject**.
Read the draft aloud, then:
> "Notice what it does *not* say — no product names, no return figures, no advice.
> MAS/FAA rules say you don't recommend before a fact-find, and the model respects
> that: it asks for a short call instead."
>
> "And it never reached my client. **Guardian holds every free-form reply** until I
> approve. That's the line between a demo and something I'd trust with my licence."

Tap **Approve** → it goes out on WhatsApp. Show it arrive on your phone.

### FA4 — Already in my CRM — synced live (1.5 min)
In Roost, open **People** (sidebar `/contacts`) and refresh.
> "There's my new lead. See the badge — **Synced live from Attio**. This isn't a local
> copy or an import; Roost is reading my CRM directly. The lead landed here with zero
> data entry on my part."

Then switch to the **Attio "Roost"** tab for the deal + score:
> "Same record in Attio — a deal in the pipeline and an **AI lead-score**: intent,
> urgency, confidence. Roost and my CRM are one system, kept in sync."

### FA5 — Voice-memo → CRM (1.5 min)
After a meeting, send the Telegram bot a **voice memo**:
> *"memo: met Marcus Tan, wants to proceed with the retirement plan, send the proposal by Friday."*

Roost transcribes it, pulls out the action items, and appends a **meeting note** to
Marcus's Attio record. Open Marcus in Attio:
> "Every meeting logged to the right client with the follow-ups — by talking, not typing."

### FA6 — The safety rails (30s)
> "Two controls make this safe to leave running: a **global pause** — one switch kills
> all automation if I'm away — and a **recency gate** so it won't cold-message someone
> who went quiet weeks ago. Anything client- or money-facing goes through Guardian."

### FA7 — One-liner takeaway (30s)
> "Roost turns your WhatsApp into a qualified, compliant, CRM-synced pipeline — and you
> approve everything from one Telegram chat. The whole job, automated up to the last
> safe step."

---

> **Track A below is the live track for the current box.** Tracks B (SME) and C
> (generic) need their bundles re-enabled (`SME_OPS_ENABLED=true`, …); Track FA above
> needs `DEFAULT_VERTICAL=financial_advisor`. All are `.env`-only flips
> (`docker compose up -d roost`) — no rebuild.

---

## Track A — Singapore property agent (the hero track) ⭐ (~7 min)

**This is the primary track for the current demo box** (`PROPERTY_AGENT_ENABLED=true`,
`DEFAULT_VERTICAL=property`). The toolkit (IRAS calc + HDB EIP RPA) is live; DNC + CDD
run in dry-run (narrate). See the wire-status table above.

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

> "The default is `claude_cli` — it runs on the Claude subscription you
> already pay for. Headless agent runs draw from your plan's monthly **Agent
> SDK Credit** (Pro $20 / Max $100–200, since 2026-06-15), not a per-token API
> bill — and only heavy use beyond that credit is pay-as-you-go. Same idea for
> Gemini CLI and Codex. Roost wires subscription auth into a multi-channel
> agent surface."

> "If you want uncapped throughput, flip to `claude` or `openai` API mode —
> same code path, pay-as-you-go billing."

> **Heads-up:** the subscription token can expire — if `/agentic` says *"Not
> logged in"*, re-run `claude login --device-auth` in the container (see
> Troubleshooting). Don't leave this to demo time.

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
