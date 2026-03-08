# Roost

**Build your AI nest.** A self-hosted productivity platform that gives your AI agent persistent memory, tools, and integrations — all running safely inside Docker.

## What Roost Does

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
- **Telegram Bot** — mobile access to all features via 50+ commands
- **Notion Sync** — pages, databases, blocks
- **Infrastructure** — SSH, Docker, Kubernetes management for remote servers

All accessed through **4 interfaces**: Web UI, Telegram Bot, CLI, and MCP Server (219 tools).

## Quick Start

```bash
# Clone
git clone https://github.com/crazyguy106/roost.git
cd roost

# Configure
cp .env.example .env
# Edit .env with your API keys

# Run (core only)
docker compose up -d

# Run with features
ENABLE_GOOGLE=true ENABLE_TELEGRAM=true docker compose up -d

# Access
open http://localhost:8080          # Web UI + Setup Wizard
open http://localhost:8080/terminal  # Claude Code (browser terminal)
ssh -p 2222 dev@localhost           # Claude Code (SSH)
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
| **Infra** | `ENABLE_INFRA=true` | 18 | SSH/SCP, Docker, Kubernetes |

## Security Model

Roost runs Claude Code inside a Docker container — **the container IS the sandbox**:

1. **Container isolation** — Claude Code can only affect what's inside the container
2. **Outbound guard hook** — emails, SSH commands, Teams messages require explicit user confirmation
3. **No permission bypass** — Claude Code runs with standard permission prompts
4. **Secrets stay outside** — `.env` is mounted at runtime, never baked into the image

## Access Methods

| Method | URL/Command | Use Case |
|--------|-------------|----------|
| **Web UI** | `http://localhost:8080` | Dashboard, settings, setup wizard |
| **Browser Terminal** | `http://localhost:8080/terminal/` | Claude Code via ttyd → tmux |
| **SSH** | `ssh -p 2222 dev@localhost` | Claude Code via tmux attach |
| **Telegram** | Talk to your bot | Mobile access to all features |

Both browser terminal and SSH connect to the same persistent tmux session. Disconnect and reconnect — Claude keeps working.

## Architecture

```
┌─ Docker Container ──────────────────────┐
│                                          │
│  tmux session "ai-claude"                │
│    └─ Claude Code (persistent)           │
│        └─ MCP Server (219 tools)         │
│                                          │
│  Web UI (:8080) ─── Setup Wizard         │
│  ttyd (:7681) ───── Browser Terminal     │
│  sshd (:22) ─────── SSH Access           │
│  Telegram Bot ───── Mobile Access        │
│  SQLite ─────────── Persistent Storage   │
│                                          │
└──────────────────────────────────────────┘
```

## Documentation

See `docs/` for detailed guides:
- [Platform Overview](docs/platform-overview.md)
- [Onboarding Guide](docs/onboarding-guide.md)
- [User Guide](docs/user-guide.md)
- [MCP Server Reference](docs/mcp-server.md)
- [Google OAuth Setup](docs/setup-google-oauth.md)
- [Microsoft Graph Setup](docs/setup-microsoft-graph.md)
- [Telegram Bot Setup](docs/setup-telegram-bot.md)
- [Gmail Automation](docs/gmail-automation.md)
- [Multi-Tenancy](docs/multi-tenancy.md)
- [Neurodivergent Features](docs/neurodivergent-features.md)

## License

MIT
