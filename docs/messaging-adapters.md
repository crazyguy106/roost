# Messaging adapters — Discord / Slack / Signal / Matrix

Roost ships four standalone-process adapters that expose the AI agent
over consumer messaging platforms. They sit alongside the in-tree
Telegram bot but run as **separate processes**, not as part of the main
`roost-web` or `roost-bot` services.

| Adapter | Module | Required env | Required pip dep | Start command |
|---|---|---|---|---|
| Discord | `roost.adapters.discord_bot` | `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS` | `discord.py` | `python -m roost.adapters.discord_bot` |
| Slack | `roost.adapters.slack_bot` | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_ALLOWED_USERS` | `slack-bolt` | `python -m roost.adapters.slack_bot` |
| Signal | `roost.adapters.signal_bot` | `SIGNAL_API_URL`, `SIGNAL_PHONE_NUMBER`, `SIGNAL_ALLOWED_NUMBERS` | `httpx` (already bundled) | `python -m roost.adapters.signal_bot` |
| Matrix | `roost.adapters.matrix_bot` | `MATRIX_HOMESERVER`, `MATRIX_USER_ID`, `MATRIX_ACCESS_TOKEN` *or* `MATRIX_PASSWORD`, `MATRIX_ALLOWED_USERS` | `matrix-nio` | `python -m roost.adapters.matrix_bot` |

Env vars are declared in `roost/config.py` (search for `DISCORD_BOT_TOKEN`,
`SLACK_BOT_TOKEN`, `SIGNAL_API_URL`, `MATRIX_HOMESERVER`). The
`env-templates/gemini-minimal.env` template lists all of them with empty
defaults so a self-hoster can copy-paste.

## Shared base

All four adapters import from `roost/adapters/__init__.py`, which provides:

- `PLATFORM_PROMPTS` — per-platform system-prompt suffixes (Discord
  markdown vs Slack mrkdwn vs Matrix vs Signal vs web). The web chat
  uses the same map.
- `BASE_SYSTEM_PROMPT` — tool-use guidance shared across every surface.
- `create_agent(mode, session_id, system_prompt, ...)` — provider
  factory honouring `AGENT_PROVIDER` (`gemini` / `claude` / `claude_cli` /
  `gemini_cli` / `codex_cli` / `openai` / `ollama`).
- `run_agent(prompt, user_id, platform, on_progress=None)` — the entry
  point each adapter calls per inbound message. Persists chat history,
  applies the charter, dispatches to the agent.
- `truncate(text, limit)` — clip output to the platform's message-length
  cap.

This means a new platform adapter is ~60 LoC of platform plumbing plus
`run_agent(...)` — message-handling, authz, formatting, and provider
selection are already done.

## Auth model

Each adapter enforces its own allowlist before calling `run_agent`:

- **Discord** — `DISCORD_ALLOWED_USERS` is a comma-separated list of
  Discord user snowflakes. Empty = anyone with DM access.
- **Slack** — `SLACK_ALLOWED_USERS` is a comma-separated list of Slack
  user IDs (`U…`). Empty = anyone the bot is in a channel with.
- **Signal** — `SIGNAL_ALLOWED_NUMBERS` is comma-separated E.164
  numbers. Empty = any sender.
- **Matrix** — `MATRIX_ALLOWED_USERS` is comma-separated MXIDs
  (`@user:server`). Empty = invite-only via Matrix room ACLs.

`DISCORD_BOT_TOKEN` etc. are runtime credentials — never check them
into the repo. Use `.env` or a secret manager.

## Deployment

These are not started by `docker-compose.yml`. Run them as:

- **systemd unit** alongside `roost-bot.service` and `roost-web.service`,
- **a second container** in your compose stack (`command: python -m
  roost.adapters.discord_bot`), or
- **a tmux pane** for laptop / classroom installs.

They share the same SQLite database, so chat history and tool calls
are visible across surfaces (`/files`, `/tty`, Telegram, Web chat). The
event bus (`roost.events`) broadcasts task lifecycle to subscribers,
which is how adapters relay `TASK_CREATED` / `TASK_UPDATED` /
`TASK_COMPLETED` back to their channel.

## When to use which

- **Telegram bot** (in-tree, `roost/bot/`) — primary surface for
  Ethan-style single-operator workflows. OTP-confirmation flow, draft
  approvals, file uploads.
- **Discord / Slack** — team workspaces where multiple users want to
  hit the same Roost. Stick to `_ALLOWED_USERS` allowlists.
- **Signal** — high-privacy 1:1 use; requires running
  `signal-cli-rest-api` as a sidecar container.
- **Matrix** — federated alternative for self-hosting purists.

## Not implemented

DingTalk and Feishu were sketched in `roost/bot/adapters/` for a future
Chinese-market rollout but were removed on 2026-06-03 — they sat as
empty stubs for seven weeks with no config, no tests, no docs.
Re-implement under `roost/adapters/<name>_bot.py` when there's actual
demand, following the Discord / Slack pattern above.
