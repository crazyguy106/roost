# Roost — Platform Architecture Overview

How Roost works as a system. Written for someone technical joining the team who needs the big picture before diving into code.

**Last updated:** 2026-02-23

---

## 1. What Roost Is

A personal productivity platform with four interfaces — CLI, Web, Telegram Bot, and MCP Server — sharing a single service layer and SQLite database. Built for a VPS, designed around neurodivergent-friendly task management, and extended with integrations for email, calendar, files, AI, and infrastructure management.

```
CLI (task) ──────┐
Web (FastAPI/PWA) ├── task_service.py ── SQLite (WAL) ── data/roost.db
Bot (Telegram) ──┤         │
MCP (FastMCP) ───┘         ├── events.py ── notifier.py (cross-interface sync)
                            │
```

All four interfaces call the same service functions, emit the same events, and read/write the same database. Changes from any interface are visible everywhere.

---

## 2. Integration Landscape

```
Roost (Central Hub)
├── 4 interfaces: CLI, Web (FastAPI/PWA), Telegram Bot, MCP Server (210 tools)
├── SQLite (WAL) — tasks, projects, contacts, calendar, OAuth, time tracking
│
├── Google Workspace (Dev VPS only)
│   ├── Gmail — auto-labelling, action cycling, email-to-task, 5-min poller
│   ├── Calendar — read all calendars, CRUD events, task deadline sync
│   ├── Drive — list/download/upload/search via rclone
│   ├── Slides — PPTX + Google Slides template ops
│   ├── Sheets — read/write/append/template
│   └── Docs — read/replace/template/append/image
│
├── Microsoft 365 (Remote instances, 29 tools)
│   ├── Outlook — search, read, send, folder management
│   ├── Calendar — CRUD events, search, list calendars
│   ├── OneDrive — list, download, upload, search
│   ├── Excel — worksheets, read/write ranges
│   ├── Teams — teams, channels, messages, chats
│   └── SharePoint — sites, files, download, upload
│
├── Gemini AI (gemini-3-flash-preview)
│   ├── Document mode — multi-turn agentic processing
│   ├── Research mode — 5-phase pipeline (FRAME/GATHER/STRUCTURE/DEVELOP/COLLATE)
│   ├── Image generation — Gemini + Imagen 4 (5 models, reference images)
│   ├── Single-shot generation — gemini_generate with output_file
│   └── Utilities — summarize, clean text, compare, process documents
│
├── Notion — bidirectional sync (6 databases, push/pull/retry)
├── Otter.ai + Zapier + Dropbox — meeting transcription capture
│
└── Infrastructure Management
    ├── SSH — register servers, remote exec, SCP upload/download
    ├── Docker — ps, logs, pull, compose up/down
    └── Kubernetes — get, describe, logs, apply, delete
```

---

## 3. The VPS Infrastructure

### Hub-and-Spoke Model

```
                    ┌─────────────────────────────────────────────────┐
                    │         DEV VPS (Hub) — task.example.com      │
                    │                                                 │
                    │  Roost: CLI + Web + Bot + MCP (210 tools)    │
                    │  Claude Code: 4 persistent tmux sessions        │
                    │                                                 │
                    │                                                 │
                    │  GitHub push (origin)                           │
                    │  rsync/SCP deploys to all remotes ──────────┐   │
                    └─────────────────────────────────────────────┼───┘
                                                                  │
                         ┌────────────────────────────────────────┤
                         │                                        │
                         ▼                                        ▼
          ┌──────────────────────────┐         ┌──────────────────────────┐
          │  REMOTE: user-vps      │         │  REMOTE: user-vps     │
          │  user.example.com    │         │  user.example.com   │
          │                          │         │                          │
          │  Web + Bot + Claude Code │         │  Web + Bot + Claude Code │
          │  Google ✗  Microsoft ✓   │         │  Google ✗  Microsoft ✓   │
          │                          │         │                          │
          │  Hetzner CAX11 (ARM)     │         │  Hetzner CAX11 (ARM)     │
          │  ~EUR 4/mo               │         │  ~EUR 4/mo               │
          └──────────────────────────┘         └──────────────────────────┘
```

### What Runs Where

| Component | Dev VPS | Remote Instances |
|-----------|---------|-----------------|
| Roost Web (FastAPI on :8080) | Yes | Yes |
| Roost Telegram Bot | Yes | Yes (per-user bot) |
| Roost MCP Server | Yes | No |
| Roost CLI | Yes | Yes |
| Claude Code | Yes | Yes |
| nginx + SSL (Let's Encrypt) | Yes | Yes |
| Google OAuth + Workspace | Yes | No |
| Microsoft Graph | No | Yes |
| Notion sync | Available | No |

### Feature Flags

Per-instance behaviour controlled by `.env`. Same codebase adapts to different deployments:

| Flag | Dev VPS | Remote | Effect |
|------|---------|--------|--------|
| `GOOGLE_ENABLED` | `true` | `false` | Master toggle for all Google services |
| `GMAIL_ENABLED` | `true` | `false` | Gmail automation + auto-labelling |
| `MS_ENABLED` | `false` | `true` | Microsoft 365 (email, calendar, files, teams) |
| `NOTION_SYNC_ENABLED` | `false` | `false` | Bidirectional Notion sync |

When `GOOGLE_ENABLED=false` and `MS_ENABLED=true`, the Telegram bot automatically falls through to Microsoft Graph. `/cal` shows Outlook calendar, `/inbox` opens MS email triage, `/block` creates events via Graph API. No code changes needed — feature flags handle it.

### Deployment Model

- **Source of truth:** Git on the dev VPS. GitHub repo: `youruser/roost` (private).
- **Deploy to remotes:** `rsync` or `scp` from dev VPS via SSH. No git on remotes, no PAT needed.
- **Provisioning:** `scripts/deploy-remote.sh` sets up a new server from scratch (packages, user, venv, nginx, SSL, hardening).
- **Service restart:** `systemctl --user restart roost-web roost-bot` on the remote.
- **Remote management via MCP:** Servers registered in Roost — use `ssh_exec(server="user-vps", ...)` and `scp_upload()` from Claude Code.

### Security Hardening (All Instances)

- UFW firewall (SSH:22, HTTP:80, HTTPS:443 only)
- fail2ban (SSH brute-force: 3 strikes = 24h ban)
- SSH hardened (pubkey only, no password, no root login)
- Kernel hardening (sysctl: no redirects, SYN cookies)
- Unattended security upgrades
- MCP outbound guard hook (gates send_email, ssh_exec, kubectl_apply with confirmation)

### Claude Code (tmux Sessions)

Each instance runs Claude Code in persistent tmux sessions that survive SSH disconnects. Claude Code connects to Roost via the MCP Server (210 tools), giving it full access to tasks, email, calendar, files, AI, and infrastructure.

**Dev VPS:** 4 parallel sessions for concurrent AI work:

```
ai-claude-1    Primary session (usually attached)
ai-claude-2    Secondary — parallel tasks
ai-claude-3    Background work
ai-claude-4    Background work
```

**Remote instances:** 1 session per user, managed via the web UI `/sessions` page or SSH.

**How it connects:**

```
Claude Code (in tmux)
  → reads ~/.mcp.json
  → spawns roost-mcp (stdio transport)
  → MCP server loads 31 tool modules (deferred imports)
  → Claude Code can now call any of 210 tools
  → tools call service layer → SQLite / APIs / subprocess
```

**Key MCP tool modules (210 tools across 31 modules):**

| Category | Modules | Tools |
|----------|---------|:-----:|
| Task management | tasks, tasks_extended, activity | 23 |
| Wellbeing | routines, spoons, shutdown, streaks | 13 |
| People | contacts, communications, projects, entities | 13 |
| Google Workspace | gmail, calendar, calendar_write, drive, slides, sheets, docs | 32 |
| Microsoft 365 | ms_email, ms_calendar, ms_onedrive, ms_excel, ms_teams, ms_sharepoint | 29 |
| AI | gemini (9 tools incl. generate, image, research, agent) | 9 |
| Infrastructure | ssh, docker, k8s | 18 |
| Productivity | time tracking, stats, doc generation, context bundles, sessions | 18 |
| OKR | objectives, key results, check-ins, dashboards, scorecards | 12 |
| Presentations | deck generation, meeting notes, transcription | 3 |
| Scheduled Email | schedule, list, cancel | 3 |

---

## 4. VPS Software Stack

What needs to be installed and configured on each instance. The `scripts/setup-vps.sh` and `scripts/deploy-remote.sh` automate all of this — these tables document what they do.

### System Packages (apt)

`git`, `curl`, `wget`, `tmux`, `nginx`, `certbot`, `python3`, `python3-venv`, `python3-pip`, `build-essential`, `libffi-dev`, `libssl-dev`, `jq`, `unzip`, `pandoc`, `sqlite3`, `ufw`, `fail2ban`, `unattended-upgrades`

### Runtime Dependencies

| Component | Version | Purpose |
|-----------|---------|---------|
| **Node.js** | 22.x | Required by Claude Code CLI |
| **ttyd** | latest | Web terminal — proxied via nginx at `/terminal/` |
| **Python** | 3.10+ | Roost (virtualenv at `roost/venv/`) |
| **Claude Code** | latest | `npm install -g @anthropic-ai/claude-code` (installed to `~/.local/`) |
| **nginx** | system | Reverse proxy (`:443` → `:8080` for web, `:7681` for ttyd) |
| **certbot** | system | Let's Encrypt SSL auto-renewal |

### Python Packages (installed in virtualenv)

Core: `fastapi`, `uvicorn[standard]`, `python-dotenv`, `python-multipart`, `pydantic`, `SQLAlchemy`, `aiosqlite`
Bot: `python-telegram-bot`, `PyYAML`
Web: `Jinja2`, `itsdangerous`, `Authlib`, `Werkzeug`, `Flask`, `slowapi`
APIs: `httpx`, `cryptography`, `google-api-python-client`, `google-auth-oauthlib`, `msal`, `notion-client`, `google-genai`
Tools: `icalendar`, `python-dateutil`, `pytz`, `python-pptx`, `fastmcp<3`

### Configuration Files

| File | Purpose | Created by |
|------|---------|-----------|
| `.env` | All secrets, feature flags, integration credentials | `setup-vps.sh` (template) |
| `~/.mcp.json` | MCP server config — points to `roost-mcp` binary | `setup-vps.sh` |
| `~/.claude/settings.local.json` | Claude Code hooks (outbound guard) | Manual (dev VPS only) |
| `~/.claude/hooks/outbound-guard.sh` | Prompt injection guardrail for MCP | Manual (dev VPS only) |
| `~/.config/systemd/user/roost-web.service` | Web UI service | `setup-vps.sh` |
| `~/.config/systemd/user/roost-bot.service` | Telegram bot service | `setup-vps.sh` |
| `/etc/nginx/sites-available/<domain>` | Nginx reverse proxy config | `setup-vps.sh` |

### What Needs Manual Configuration After Setup

The setup script creates a template `.env` with generated secrets but leaves integration credentials blank:

| Setting | Where to get it | Required? |
|---------|----------------|-----------|
| `TELEGRAM_BOT_TOKEN` | Create bot via @BotFather on Telegram | Yes (for bot) |
| `TELEGRAM_ALLOWED_USERS` | Your Telegram user ID (use @userinfobot) | Yes (for bot) |
| `MS_CLIENT_ID` / `MS_CLIENT_SECRET` | Azure Portal → App Registration (shared app) | For MS integrations |
| `GEMINI_API_KEY` | Google AI Studio → API Keys | For AI features |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Google Cloud Console → OAuth | Dev VPS only |
| `NOTION_API_TOKEN` | Notion Settings → Integrations | If using Notion |

After filling in `.env`, restart services: `systemctl --user restart roost-web roost-bot`

For Microsoft 365, also visit `https://<domain>/auth/microsoft` to complete the OAuth consent flow.

---

## 5. Remote Instance Directory Structure


```
/home/<user>/
├── .claude/                    Claude Code config
│   └── settings.local.json    Hook settings (if configured)
├── .config/systemd/user/       Service definitions
│   ├── roost-web.service
│   └── roost-bot.service
├── .local/bin/
│   ├── claude                  Claude Code CLI
│   └── roost-mcp           MCP server entry point
├── .mcp.json                   MCP server config
└── projects/
    └── roost/               The ONLY project
        ├── .env                Secrets + feature flags (chmod 600)
        ├── deploy.sh           Quick restart script
        ├── setup.py            Package definition
        ├── data/
        │   └── roost.db    SQLite database
        ├── docs/               Documentation (visible via /m/docs)
        ├── roost/           Source code
        └── venv/               Python virtualenv
```

### The `/m/docs` Reader

The mobile web UI has a markdown file browser at `/m/docs`. It's controlled by the `DOCS_ROOTS` environment variable:

```python
# From pages_mobile.py
DOCS_ROOTS = os.getenv("DOCS_ROOTS", "~/projects").split(",")
```

**Default:** `~/projects` — so it scans everything under `~/projects/`.

On remote instances, `~/projects/` only contains `roost/`, so `/m/docs` shows just:
- `roost/docs/` — all documentation files (platform overview, user guide, onboarding, etc.)
- `roost/CLAUDE.md` — project instructions

This means any documentation you want team members to read via the web UI must live inside `roost/docs/`. That's why all platform docs (this file, user guide, onboarding guide) are kept here rather than in a separate project.

### Dev VPS vs Remote — Directory Comparison

| Path | Dev VPS | Remote |
|------|---------|--------|
| `~/projects/roost/` | Yes | Yes |
| `~/projects/siemless_v3/` | Yes (security product) | No |
| `~/projects/valet-nexaguard/` | Yes (client project) | No |
| `~/bin/git-backup.sh` | Yes (cron backup) | No |

---

## 6. REST API

The web server exposes a full REST API at `http://localhost:8080/api/`. All endpoints require authentication (session cookie, HTTP Basic, or dev token).

### Core Resources (`/api/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tasks` | List tasks (filters: status, project, priority, energy, effort) |
| POST | `/api/tasks` | Create task |
| GET | `/api/tasks/{id}` | Get task detail |
| PUT | `/api/tasks/{id}` | Update task |
| POST | `/api/tasks/{id}/done` | Mark complete |
| POST | `/api/tasks/{id}/wip` | Mark in-progress |
| DELETE | `/api/tasks/{id}` | Delete task |
| POST | `/api/tasks/{id}/shelve` | Move to someday |
| POST | `/api/tasks/{id}/unshelve` | Restore from someday |
| GET | `/api/tasks/{id}/subtasks` | List subtasks |
| GET | `/api/tasks/{id}/assignments` | List task assignments |
| POST | `/api/tasks/{id}/assignments` | Add assignment |
| GET | `/api/projects` | List projects (filters: status, type, entity) |
| POST | `/api/projects` | Create project |
| GET | `/api/projects/{id}` | Get project detail |
| PUT | `/api/projects/{id}` | Update project |
| DELETE | `/api/projects/{id}` | Delete project |
| GET | `/api/projects/{id}/tree` | Project hierarchy tree |
| GET | `/api/projects/{id}/assignments` | List project members |
| POST | `/api/projects/{id}/assignments` | Add member |
| GET | `/api/entities` | List entities |
| POST | `/api/entities` | Create entity |
| GET | `/api/entities/{id}` | Get entity detail |
| PUT | `/api/entities/{id}` | Update entity |
| GET | `/api/entities/{id}/tree` | Entity hierarchy |
| GET | `/api/entities/{id}/people` | People in entity |
| GET | `/api/contacts` | List contacts |
| POST | `/api/contacts` | Create contact |
| GET | `/api/contacts/{id}` | Get contact detail |
| PUT | `/api/contacts/{id}` | Update contact |
| GET | `/api/contacts/{id}/communications` | Communication history |
| POST | `/api/contacts/{id}/sync-emails` | Sync emails for contact |
| POST | `/api/contacts/{id}/sync-meetings` | Sync meetings for contact |
| GET | `/api/notes` | List notes |
| POST | `/api/notes` | Create note |
| DELETE | `/api/notes/{id}` | Delete note |

### Triage & Wellbeing (`/api/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/today` | Today's triage (overdue + due + in-progress) |
| GET | `/api/urgent` | Top tasks by urgency score |
| GET | `/api/focus` | Current focus tasks |
| POST | `/api/focus/{id}` | Set focus |
| DELETE | `/api/focus/{id}` | Clear focus |
| POST | `/api/energy-mode` | Set energy mode (low/medium/high) |
| GET | `/api/energy-mode` | Get current energy mode |
| POST | `/api/shutdown` | Shutdown protocol |
| POST | `/api/resume-day` | Resume from shutdown |
| GET | `/api/progress` | Progress summary |

### Calendar (`/api/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/calendar` | Today's events |
| GET | `/api/calendar/events` | Events for date range |
| GET | `/api/calendar/range` | Extended date range query |
| GET | `/api/calendar/export` | iCal export |

### Email (`/api/email/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/email/search` | Search emails |
| GET | `/api/email/thread/{id}` | Read email thread |
| POST | `/api/email/draft-ai` | AI-generated draft reply |
| POST | `/api/email/send` | Send email |
| POST | `/api/email/archive/{id}` | Archive thread |
| POST | `/api/email/task` | Create task from email |

### Integrations (`/api/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/gmail/status` | Gmail connection status |
| POST | `/api/gmail/send-digest` | Trigger digest email |
| POST | `/api/gmail/sync-calendar` | Force calendar sync |
| POST | `/api/notion/sync` | Trigger Notion sync |
| POST | `/api/notion/export` | Export to Notion |
| GET | `/api/notion/status` | Notion sync status |
| GET | `/api/curricula` | List curricula |
| POST | `/api/curricula/scan` | Scan for new curricula |

### Sessions (`/api/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/sessions` | List Claude Code sessions |
| POST | `/api/sessions` | Create new session |
| POST | `/api/sessions/{id}/connect` | Attach to session |
| POST | `/api/sessions/{id}/close` | Close session |

### Settings (`/api/settings/`)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings/integrations` | List integrations with credential status |
| POST | `/api/settings/credential/{key}` | Store encrypted credential (admin) |
| DELETE | `/api/settings/credential/{key}` | Remove credential (admin) |
| POST | `/api/settings/test/{integration}` | Test integration credentials |
| POST | `/api/settings/flag/{flag_name}` | Toggle feature flag (admin) |
| GET | `/api/settings/personality` | Get CAGE personality text |
| POST | `/api/settings/personality` | Save CAGE personality text |

### Webhooks (Open — no auth middleware)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/otter/ingest?token=xxx` | Otter.ai meeting ingest (Zapier webhook) |

### Auth Routes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/auth/login-page` | Login page |
| GET | `/auth/login` | Google OAuth start |
| GET | `/auth/callback` | Google OAuth callback |
| GET | `/auth/microsoft` | Microsoft OAuth start |
| GET | `/auth/microsoft/callback` | Microsoft OAuth callback |
| GET | `/auth/gmail` | Gmail scope consent |
| GET | `/auth/gmail/callback` | Gmail consent callback |
| GET | `/auth/logout` | Logout |

### Mobile PWA (`/m/`)

52 routes with HTMX-powered interactions. Full CRUD for all entities:

| Area | Routes | Features |
|------|:------:|----------|
| Dashboard | 4 | Overview, energy mode (low/medium/high), shutdown/resume, notes link |
| Tasks | 10 | List, detail, create, edit, complete, start, focus, shelve, delete |
| Contacts | 11 | List, detail, create, edit, delete, affiliations, communications, sync |
| Entities | 5 | List, detail, create, edit, delete |
| Projects | 8 | List, detail, create, edit, delete, pause/resume, team members |
| Notes | 3 | List with tag filter, create, delete |
| Calendar | 2 | Monthly view, events |
| Email | 3 | Inbox triage, thread view, actions |
| Docs | 3 | Browse, view, edit/save |
| Settings | 3 | Feature flags, integration credentials, personality editor |

### Public Forms (`/forms/`)

Unauthenticated forms for external stakeholders. Responses saved as timestamped JSON to `data/forms/`. `/forms` paths excluded from auth middleware and mobile redirect.

---

## 7. Data Flows

### Meeting Capture (Otter.ai)

```
Meeting happens (Zoom/Meet/Teams)
  → Otter.ai transcribes + summarises
  → Otter emails summary
  → Zapier (free tier) saves to Dropbox /Otter/
  → Roost poller picks up new .txt file
  → Parses sections: Summary, Action Items, Insights, Outline, Transcript
  → Creates note with tag="meeting"
  → Bot notifies via Telegram
```

### Email Lifecycle

```
Email arrives (Gmail or Outlook)
  → Gmail: auto-label by domain (14 rules) + action cycling
  → Telegram notification: sender, subject, preview
  → User triages via /inbox: view → reply / AI draft / archive / create task
  → If [task] in subject: auto-create task
  → If action required: label cycles to "(To Reply)"
  → After reply: label cycles to "(Waiting for Reply)"
```

### Morning Digest

```
Scheduled daily (or via /today, or morning_briefing MCP tool):
  → Fetch today's calendar events (Google or MS, depending on flags)
  → Fetch overdue tasks + due today + in-progress
  → Check energy budget (spoons remaining)
  → Check awaiting-reply emails
  → Assemble briefing → push to Telegram
```

### Task Event Bus

```
Task created/updated/completed (from any interface)
  → events.py fires event (with source field for dedup)
  → Telegram notifier: pushes notification (unless source = telegram)
  → Notion sync: pushes change to Notion database (if enabled)
  → Gmail subscriber: sends completion email / deadline reminder
  → All 4 interfaces see the change immediately (shared SQLite)
```

---

## 8. Integration Map

| Integration | Dev VPS | Remote | What It Does | Auth Method |
|-------------|:-------:|:------:|-------------|-------------|
| **Google Gmail** | Yes | No | Send/receive email, auto-label, triage | OAuth 2.0 |
| **Google Calendar** | Yes | No | Read events, CRUD, task sync | OAuth 2.0 |
| **Google Drive** | Yes | No | File storage via rclone | rclone config |
| **Google Slides** | Yes | No | Template ops, text/image replacement | OAuth 2.0 |
| **Google Sheets** | Yes | No | Read/write ranges, templates | OAuth 2.0 |
| **Google Docs** | Yes | No | Read/replace/append, templates | OAuth 2.0 |
| **MS Outlook** | No | Yes | Email search, read, send, folders | MSAL OAuth |
| **MS Calendar** | No | Yes | Events CRUD, search, list calendars | MSAL OAuth |
| **MS OneDrive** | No | Yes | File list, download, upload, search | MSAL OAuth |
| **MS Excel** | No | Yes | Worksheet read/write | MSAL OAuth |
| **MS Teams** | No | Yes | Channels, messages, chats | MSAL OAuth |
| **MS SharePoint** | No | Yes | Sites, files, download, upload | MSAL OAuth |
| **Gemini AI** | Yes | Yes | Generation, research, summarisation | API key |
| **Notion** | Yes | No | Bidirectional sync (6 databases) | API token |
| **Telegram** | Yes | Yes | Bot interface (84 commands) | Bot token |
| **SSH servers** | Yes | No | Remote execution, SCP, management | SSH keys |
| **Docker** | Yes | No | Container management | Local socket / SSH |
| **Kubernetes** | Yes | No | Cluster management | kubeconfig |
| **Otter.ai/Dropbox** | Yes | No | Meeting transcription capture | Dropbox API |
| **Claude Code MCP** | Yes | No | 210 tools for AI-assisted work | stdio (FastMCP) |

**Shared Azure app:** All MS Graph instances share one Azure App Registration. Each instance adds its own redirect URI and users OAuth independently.

---

## 9. File Structure

```
/home/dev/projects/roost/
├── .env                           Secrets + feature flags (chmod 600)
├── setup.py                       Package + 4 entry points
├── deploy.sh                      Quick restart script
├── data/
│   ├── roost.db                SQLite (WAL) — 37 tables
├── docs/                          35 documentation files
├── scripts/                       Deploy + seed scripts
├── tests/                         Test files
└── roost/
    ├── config.py                  Environment config + feature flags
    ├── database.py                SQLite + SQLAlchemy setup (37 tables)
    ├── models.py                  Pydantic models (shared by all interfaces)
    ├── task_service.py            All CRUD operations (2500+ lines)
    ├── events.py                  In-process event bus
    ├── calendar_service.py        Google Calendar hub
    ├── gemini_agent.py            Multi-turn Gemini (document + research)
    ├── dropbox_client.py          Dropbox API for Otter sync
    ├── otter_poll.py              Otter transcript + summary polling
    ├── gmail/                     Gmail + Calendar Write (8 files)
    ├── microsoft/                 MS Graph (3 files)
    ├── notion/                    Notion mirror (7 files)
    ├── mcp/                       MCP Server (36 tool modules, 210 tools)
    ├── web/                       FastAPI app + templates + static
    │   ├── app.py                 Middleware + auth + router setup
    │   ├── api.py                 REST API endpoints
    │   ├── api_settings.py        Settings API — credentials, flags, personality
    │   ├── api_otter.py           Otter webhook ingest
    │   ├── pages.py               Desktop HTML routes + docs browser
    │   ├── pages_mobile.py        Mobile PWA — 52 routes + HTMX
    │   ├── forms.py               Public forms (no auth)
    │   └── templates/             50 Jinja2 templates (desktop + mobile + forms)
    ├── bot/                       Telegram bot
    │   ├── main.py                Entry + handler registration
    │   ├── handlers/              17 handler modules (84 commands)
    │   ├── scheduler.py           Background jobs (digest, polling, reminders)
    │   └── notifier.py            Push notifications
    ├── services/
    │   ├── credentials.py         Fernet-encrypted credential storage
    │   ├── settings.py            User settings CRUD
    │   └── ...                    Tasks, contacts, notes, wellbeing, OKR, etc.
    └── cli/
        ├── main.py                CLI entry point
        └── onboard.py             Interactive setup wizard
```

### Key Config Files

| File | Purpose |
|------|---------|
| `.env` | All secrets and feature flags (generated by `roost-onboard` or manual) |
| `~/.mcp.json` | MCP server config (roost, playwright, notion) |
| `~/.claude/settings.local.json` | Claude Code hooks (outbound guard) |
| `~/.claude/hooks/outbound-guard.sh` | Prompt injection guardrail |
| `~/bin/git-backup.sh` | Automated git bundle backup (cron, 6h) |

---

## Appendix: Quick Reference

### Key URLs

| URL | What |
|-----|------|
| `task.example.com` | Dev VPS web UI |
| `user.example.com` | Ben Yeo remote instance |
| `user.example.com` | user remote instance |

### Common Operations

```bash
# Restart services (dev VPS)
./deploy.sh
# or: systemctl --user restart roost-web roost-bot

# Push update to remote
scp <file> dev@<IP>:/home/dev/projects/roost/<path>
ssh dev@<IP> 'XDG_RUNTIME_DIR=/run/user/$(id -u) systemctl --user restart roost-web roost-bot'

# Check MCP tool count
roost-mcp  # Should show FastMCP banner

# Provision a new remote instance
./scripts/deploy-remote.sh <IP> <DOMAIN> <USERNAME> <FULLNAME> <EMAIL> <WEB_PASSWORD>
```

### Related Documentation

| Document | Description |
|----------|-------------|
| `docs/README.md` | Full feature reference |
| `docs/mcp-server.md` | MCP server architecture + tool list |
| `docs/user-guide.md` | User guide for team members |
| `docs/onboarding-guide.md` | New team member onboarding |
| `docs/remote-deployments.md` | Multi-instance deployment details |
| `docs/gmail-automation.md` | Gmail OAuth + auto-labelling |
| `docs/setup-microsoft-graph.md` | Microsoft 365 setup |
| `docs/research-pipeline.md` | Gemini 5-phase research pipeline |
| `docs/threat-model-prompt-injection.md` | MCP security threat model |
