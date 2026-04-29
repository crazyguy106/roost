# Roost `.env` Templates

Drop-in starting configs so you don't have to fill every field in `.env.example`.

## Which one should I pick?

| Template | Use when… | AI provider | Extras |
|---|---|---|---|
| [`gemini-minimal.env`](gemini-minimal.env) | **First-time user.** Cheapest path to a working agent. | Gemini (free tier) | Telegram only |
| [`claude-full.env`](claude-full.env) | You pay for Claude Pro and want the full stack. | Claude API | Telegram + Google OAuth + messaging |
| [`openai.env`](openai.env) | You pay for ChatGPT Pro / have OpenAI credits. | OpenAI API | Telegram |
| [`ollama-local.env`](ollama-local.env) | **Offline / privacy-first.** No cloud AI calls. | Local Ollama | Telegram |
| [`demo.env`](demo.env) | Classroom walkthrough — every feature toggled on with placeholder creds. | Gemini | Everything (placeholders) |

## How to use

```bash
# Copy the template you want into place
cp env-templates/gemini-minimal.env .env

# Open .env and fill in the placeholder values
# (look for lines ending in =CHANGE_ME or =)
$EDITOR .env

# Start Roost
docker compose up -d
```

## What each template guarantees

Every template sets:

- `SESSION_SECRET=CHANGE_ME` — **you must replace this** with `openssl rand -hex 32`
- `WEB_USERNAME=admin` / `WEB_PASSWORD=CHANGE_ME` — admin login to the web UI
- `HOST=0.0.0.0` / `PORT=8080`
- Exactly one AI provider enabled (`AI_ENABLED=true`)
- `TELEGRAM_ENABLED=false` by default — flip to `true` once you have a bot token

Flags for integrations that require OAuth setup (`GOOGLE_ENABLED`, `MS_ENABLED`) stay `false` unless the template explicitly enables them, so Roost boots cleanly even with empty credentials.

## What's still missing after you copy a template

| Variable | Where to get it |
|---|---|
| `SESSION_SECRET` | `openssl rand -hex 32` |
| `WEB_PASSWORD` | Anything you'll remember (8+ chars) |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey (free) |
| `CLAUDE_API_KEY` | https://console.anthropic.com/settings/keys |
| `OPENAI_API_KEY` | https://platform.openai.com/api-keys |
| `TELEGRAM_BOT_TOKEN` | Chat with @BotFather on Telegram, `/newbot` |
| `TELEGRAM_ALLOWED_USERS` | Your numeric Telegram user ID (ask @userinfobot) |

## Not sure which to pick?

Use `gemini-minimal.env`. You can always re-run the setup wizard (`roost-onboard`) or overwrite `.env` with a different template later.
