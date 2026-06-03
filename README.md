# Roost

[![tests](https://github.com/crazyguy106/roost/actions/workflows/test.yml/badge.svg)](https://github.com/crazyguy106/roost/actions/workflows/test.yml)
[![mcp-inventory](https://github.com/crazyguy106/roost/actions/workflows/mcp-inventory.yml/badge.svg)](https://github.com/crazyguy106/roost/actions/workflows/mcp-inventory.yml)

**Build your AI nest.** A self-hosted productivity platform that gives your AI agent persistent memory, tools, and integrations — all running safely inside Docker.

## What Roost Does

Roost is **domain-agnostic** — the list below mixes **core capabilities** with **bundled vertical toolkits** (currently shipping: Property-Agent for Singapore CEA salespersons, and SME Ops for small/medium businesses). New verticals are just additional services + cadences + RPA flows on top of the same engine.

Roost gives Claude Code (or any MCP-compatible AI) a persistent workspace with:

- **Task Management** — tasks, projects, subtasks, dependencies, focus mode, shelving
- **Contact CRM** — contacts, entities, multi-identifier support, communication timeline
- **OKR Framework** — objectives, key results, scoring, dashboards
- **Wellbeing** — routines, spoon budgets (energy management), streaks, shutdown/resume
- **Time Tracking** — timers, entries, weekly summaries
- **Email** — Gmail and/or Outlook (search, read, draft-first send)
- **Calendar** — Google Calendar and/or Microsoft Calendar
- **Cloud Storage** — Google Drive and/or OneDrive
- **AI Tools** — Gemini generation, research, summarization, vision
- **Messaging** — WhatsApp Cloud API and WeChat Official Account with AI CDR pipeline
- **Response Templates** — canned messages with {{variable}} placeholders, AI-selected by intent
- **Automation Recipes** — user-defined rules with risk tiers (read_only, internal_write, external_write)
- **Natural Language Scheduling** — plain English to cron recipes ("Every Monday at 9am, summarize emails")
- **AI CDR Pipeline** — Content Disarm & Reconstruct for safe inbound message classification
- **Guardian AI** — pre-flight safety checks on every tool call (block dangerous commands, bulk deletes, bulk emails) plus a **draft-and-approve queue** for money-moving writes (Stripe refunds, Shopify order cancellations, non-DRAFT Xero invoices) — agents draft, humans approve via `/sme/sync-status` or `/api/sme/drafts/{id}/approve`
- **Cost Tracking** — per-run and daily token cost limits with automatic enforcement
- **Autonomy Levels** — supervised / assisted / autonomous modes for agent confirmation behaviour
- **Proactive Monitoring** — risk alerts for guardian blocks, tool bursts, cost spikes, failed tools
- **Checkpoints + Rollback** — snapshot agent write actions with one-click undo via `/rollback`
- **Property-Agent Toolkit (Singapore)** _(vertical bundle)_ — IRAS stamp-duty calculator (post-Apr-2023 ABSD/SSD/lease), PDPC DNC scrub, CDD sanctions/PEP screening, HDB EIP/SPR quota check. See [property-agent-toolkit.md](docs/property-agent-toolkit.md).
- **SME Ops** _(vertical bundle)_ — operational glue for small/medium businesses: Zapier ingress + outbound bridge (universal coexistence with 6,000+ apps), native Stripe / Shopify / Xero adapters with read + write + signed webhooks (writes that move money — refunds, order cancellations, non-DRAFT invoices — route through a Guardian draft-and-approve queue). HitPay, HubSpot, Mailchimp, Lazada, Shopee planned. See [sme-ops.md](docs/sme-ops.md).
- **Self-improving Skills** — auto-extract reusable patterns from successful agent runs (approval workflow)
- **Background Agents** — spawn sub-agents with concurrency and duration limits
- **Cross-channel Memory** — context that persists across Telegram, Web, and MCP (7-day auto-expiry, pinning)
- **Kanban Board** — drag-and-drop task board at `/tasks/board`
- **SOP Event Triggers** — fire recipes on email_received, task_completed, calendar events, webhooks
- **Telegram Bot** — mobile access to all features via 60+ commands
- **Notion Sync** — pages, databases, blocks
- **Infrastructure** — SSH, Docker, Kubernetes management for remote servers

All accessed through **4 interfaces**: Web UI, Telegram Bot, CLI, and MCP Server (300+ tools — see [`docs/mcp-inventory.md`](docs/mcp-inventory.md) for the canonical generated list).

**New to Roost?** Run `roost-onboard` for the interactive setup wizard, or manage everything from the `/settings` page after deployment.

## Quick Start

### Option A: Roost Lite (fastest, laptop-friendly)

One command, one container, no public URL, no chromium. Good for first-time
evaluation and workshops.

```bash
git clone https://github.com/crazyguy106/roost.git
cd roost
./scripts/run-lite.sh
# Seeds .env from env-templates/gemini-minimal.env on first run
# Prompts you to set GEMINI_API_KEY (free: https://aistudio.google.com/apikey)
open http://127.0.0.1:8080
```

Trade-offs and migration path to full compose: [`docs/roost-lite.md`](docs/roost-lite.md).

### Option B: One-command Installer (fresh Ubuntu/Debian host)

Provisions a fresh box — installs Docker, clones the repo, seeds `.env`:

```bash
curl -fsSL https://raw.githubusercontent.com/crazyguy106/roost/main/scripts/install.sh | bash
```

### Option C: Interactive Setup Wizard

```bash
git clone https://github.com/crazyguy106/roost.git
cd roost
pip install -e .
roost-onboard
```

The wizard walks you through:
1. **AI provider** — Gemini (free), Claude, OpenAI, or Ollama
2. **Telegram bot** — paste your @BotFather token, validates via API
3. **Web credentials** — set admin username and password
4. **Launch** — generates `.env` (0600 permissions) and starts Docker

### Option D: Manual Configuration

```bash
git clone https://github.com/crazyguy106/roost.git
cd roost
cp env-templates/gemini-minimal.env .env   # or claude-full.env, openai.env, ollama-local.env
# Edit .env with your API keys
docker compose up -d
```

Pre-built `.env` templates live in [`env-templates/`](env-templates/README.md) —
pick the one matching your AI provider and which integrations you want on.

### Access

```
http://localhost:8080              # Web UI
http://localhost:8080/settings     # Integrations, flags, personality
http://localhost:8080/terminal     # Claude Code (browser terminal)
ssh -p 2222 dev@127.0.0.1          # Claude Code (SSH — loopback-only, see docs/container-ssh-access.md)
```

## Feature Flags

Toggle features via build args or `.env`:

| Feature | Flag | Tools | What You Get |
|---------|------|:-----:|---|
| **Core** | always on | 101 | Tasks, projects, contacts, notes, OKR, wellbeing, time tracking |
| **Google** | `ENABLE_GOOGLE=true` | 34 | Gmail, Calendar, Drive, Slides, Sheets, Docs |
| **Microsoft** | `ENABLE_MICROSOFT=true` | 38 | Outlook, Calendar, OneDrive, Teams, SharePoint, Excel |
| **AI** | `ENABLE_AI=true` | 10 | Gemini generate, research, summarize, vision, image |
| **Telegram** | `ENABLE_TELEGRAM=true` | 2+bot | Full Telegram bot with 50+ commands |
| **Notion** | `ENABLE_NOTION=true` | 16 | Pages, databases, blocks, comments |
| **Guardian** | `GUARDIAN_ENABLED=true` | 5 | Pre-flight safety checks, cost tracking, autonomy levels |
| **WhatsApp** | `WHATSAPP_ENABLED=true` | webhook | WhatsApp Cloud API messaging + AI CDR |
| **WeChat** | `WECHAT_ENABLED=true` | webhook | WeChat Official Account messaging + AI CDR |
| **Infra** | `ENABLE_INFRA=true` | 18 | SSH/SCP, Docker, Kubernetes |

## Security Model

Roost runs Claude Code inside a Docker container — **the container IS the sandbox**:

1. **Container isolation** — Claude Code can only affect what's inside the container
2. **Guardian AI** — rules-based pre-flight check on every tool call: blocks dangerous commands (`rm -rf`, `curl|bash`), bulk deletes, bulk emails, warns on sensitive file access, and **drafts** money-moving writes (Stripe refunds, Shopify cancellations, non-DRAFT Xero invoices) for human approval via the `/sme/sync-status` page or `/api/sme/drafts/*` endpoints
3. **Outbound guard hook** — emails, SSH commands, Teams messages require explicit user confirmation
4. **Autonomy levels** — `supervised` (confirm everything), `assisted` (confirm destructive only, default), `autonomous` (no confirmation)
5. **AI CDR pipeline** — inbound messages classified in a tool-less AI sandbox (prompt injection can't trigger tools)
6. **Draft-first messaging** — external_write recipes hold drafts for human approval via Telegram
7. **Cost caps** — per-run ($0.50 default) and daily ($5.00 default) token cost limits with automatic enforcement
8. **Tool scope tiers** — Gemini agent tools restricted by trust level (FULL / INTERNAL_WRITE / READ_ONLY / NONE)
9. **Checkpoints** — every write tool call is checkpointed; reversible actions can be undone via `/rollback`
10. **No permission bypass** — Claude Code runs with standard permission prompts
11. **Secrets stay outside** — `.env` is mounted at runtime, never baked into the image
12. **Encrypted credentials** — API keys stored with Fernet (AES-128-CBC), tied to SESSION_SECRET
13. **Admin-gated settings** — only admin/owner roles can manage credentials and feature flags

## Access Methods

| Method | URL/Command | Use Case |
|--------|-------------|----------|
| **Web UI** | `http://localhost:8080` | Dashboard, tasks, contacts, projects, calendar |
| **Files** | `http://localhost:8080/files` | Drag-drop reference files into `UPLOADS_DIR` for the agent to use |
| **Settings** | `http://localhost:8080/settings` | Credentials, feature flags, personality editor |
| **Browser Terminal** | `http://localhost:8080/terminal/` | Claude Code via ttyd → tmux |
| **Web tty (xterm.js)** | `http://localhost:8080/tty` | xterm.js bridged through a PTY to a persistent per-user tmux session (FA-edition default — [docs/web-tty.md](docs/web-tty.md)) |
| **SSH** | `ssh -p 2222 dev@127.0.0.1` ([guide](docs/container-ssh-access.md)) | Claude Code via tmux attach (loopback-only; remote via ProxyJump) |
| **Telegram** | Talk to your bot | Mobile access to all features |

Both browser terminal and SSH connect to the same persistent tmux session. Disconnect and reconnect — Claude keeps working.

## Architecture

```
┌─ Docker Container ──────────────────────┐
│                                          │
│  tmux session "ai-claude"                │
│    └─ Claude Code (persistent)           │
│        └─ MCP Server (270+ tools)        │
│                                          │
│  Web UI (:8080) ─── Dashboard + Settings  │
│  ttyd (:7681) ───── Browser Terminal     │
│  sshd (:22) ─────── SSH Access (loopback)│
│  Telegram Bot ───── Mobile Access        │
│  SQLite ─────────── Persistent Storage   │
│                                          │
└──────────────────────────────────────────┘
```

## Tests

```bash
pip install -r requirements/test.txt
pytest tests/ -v
```

140+ tests covering database schema, CAGE context framework, encrypted credential storage, onboard wizard, AI CDR pipeline, response templates, automation recipes, tool scope tiers, messaging integrations, Guardian AI, cost tracking, and checkpoints.

## Documentation

See `docs/` for detailed guides:
- [Container SSH Access](docs/container-ssh-access.md) — SSH straight into the `ai-claude` tmux session; loopback-only port, ProxyJump for remote, `CAP_AUDIT_WRITE` gotcha
- [Deployment Shapes](docs/deployment.md) — laptop / hosted-by-you / VPS+domain (Caddy auto-TLS bundled via `docker-compose.public.yml`)
- [FA Edition — architecture](docs/fa-edition.md) — the Chatwoot-fronted shape: inbound webhook + outbound REST (text, templates, media all consolidated)
- [FA Edition (laptop)](docs/fa-laptop-install.md) — one-command Roost + Chatwoot + Tailscale Funnel install; WhatsApp goes through Chatwoot
- [What It Costs](docs/costs.md) — realistic pricing breakdown in SGD
- [Platform Overview](docs/platform-overview.md) — architecture, APIs, file structure
- [Onboarding Guide](docs/onboarding-guide.md) — first login, connecting integrations
- [User Guide](docs/user-guide.md) — daily workflow, features, tips
- [Settings & Credentials](docs/settings.md) — setup wizard, integration management, encryption
- [MCP Server Reference](docs/mcp-server.md) — narrative + grouping
- [MCP Tool Inventory](docs/mcp-inventory.md) — generated canonical list (run `scripts/gen_mcp_inventory.py` to refresh)
- [Google OAuth Setup](docs/setup-google-oauth.md)
- [Microsoft Graph Setup](docs/setup-microsoft-graph.md)
- [Telegram Bot Setup](docs/setup-telegram-bot.md)
- [Gmail Automation](docs/gmail-automation.md)
- [Multi-Tenancy](docs/multi-tenancy.md)
- [Neurodivergent Features](docs/neurodivergent-features.md)
- [RPA / Browser Automation](docs/rpa.md) — data-driven flows, OTP pause/resume, Singpass-assisted via `await_user_session`
- [WhatsApp Adapter](docs/whatsapp-adapter.md) — Meta Cloud API integration; STOP/HELP + `mark_inbound` parity for lead-nurture
- [Messaging Adapters](docs/messaging-adapters.md) — Discord / Slack / Signal / Matrix standalone-process bots over the shared agent base
- [Chatwoot Adapter](docs/chatwoot.md) — self-hosted helpdesk front-end (FA edition); HMAC-signed inbound webhooks + REST outbound, only `message_created`/`incoming` drives the pipeline
- [Telegram Customer Channel](docs/telegram-customer-channel.md) — same bot serves operators (allowlisted) and customer DMs (STOP/HELP, qualification, lead-capture, nurture)
- [Property-Agent Toolkit](docs/property-agent-toolkit.md) — Singapore IRAS / PDPC / CEA compliance tools
- [Lead Nurture](docs/lead-nurture.md) — multi-channel ingest + cadence engine on Attio Free, Telegram approval gate
- [Daily Summary](docs/daily-summary.md) — end-of-day Telegram digest covering nurture, tasks, leads, recipes, RPA
- [SME Ops](docs/sme-ops.md) — vertical bundle for SMBs: Zapier ingress + native Stripe/Shopify/Xero (read + write + signed webhooks, money-moving writes drafted for human approval) + planned HitPay/HubSpot/Mailchimp/Lazada/Shopee
- [Agentic Workflow Strategy](docs/agentic-workflow-strategy.md) — *(strategy memo)* product direction for Roost's own agentic surface: Plan-Approve-Execute, live file ops, streaming tool calls. Three vertical skins (admin / agent / SME) on one engine.
- [Agentic Workflow — Phase 1 Spec](docs/agentic-workflow-phase1.md) — *(build brief)* self-contained implementation spec for the planner + per-tool event stream + `/agentic` route. Hand to a Claude agent in `roost/` to implement.

## License

MIT — see [LICENSE](LICENSE). Third-party dependency licenses are
listed in [NOTICES](NOTICES).
