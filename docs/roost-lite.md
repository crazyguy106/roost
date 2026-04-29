# Roost Lite

A slimmed-down, laptop-friendly version of the full Roost stack. Designed for
first-time evaluation, workshops, and classroom walkthroughs where you don't
want to deal with public webhooks or a second chromium container.

## TL;DR

```bash
git clone https://github.com/crazyguy106/roost.git
cd roost
./scripts/run-lite.sh
# The script copies env-templates/gemini-minimal.env to .env,
# prompts you to set GEMINI_API_KEY, then starts Docker.
open http://127.0.0.1:8080
```

That's it. No SSH keys, no public DNS, no chromium. One container, one port.

## What Lite Gives Up

| Feature | Full Compose | Lite |
|---|:-:|:-:|
| Web UI | ✓ | ✓ |
| Task / project / CRM / OKR | ✓ | ✓ |
| Gemini / Claude / OpenAI / Ollama | ✓ | ✓ |
| Notes, templates, recipes | ✓ | ✓ |
| Static-page scraping (`scrape_url`) | ✓ | ✓ |
| MCP server | ✓ | ✓ |
| Telegram **outbound** | ✓ | ✓ |
| JS-rendered scraping (`scrape_js`) | ✓ | — returns `unavailable` |
| WhatsApp / WeChat **inbound** | ✓ | — needs a public URL |
| Bind to LAN | ✓ | — localhost only |
| Second container (chromium) | ✓ | — |

Everything else is identical — same image, same code, same database layout.
The only run-time differences are in `docker-compose.lite.yml`:

- `CDP_ENDPOINT=""` — tells the scrape service to fail fast instead of
  waiting 30s for a chromium that isn't there
- `ports: 127.0.0.1:8080:8080` — not exposed on the LAN
- `cpus: 1.5`, `memory: 1G` — comfortable for a laptop

## Why the Split

Full compose exists because some people want to run Roost as a production
service on a VPS: public webhooks, full JS scraping, multiple integrations,
LAN-visible ports. That stack assumes Docker experience and a domain name.

Lite exists because most people who try Roost for the first time just want
to see it work. They don't have a public URL. They don't care about
WhatsApp yet. They want `docker compose up` and a working web UI. Lite is
that path.

When they're ready for the full experience, they migrate.

## Migrating from Lite to Full

```bash
# Stop Lite
docker compose -f docker-compose.lite.yml down

# Your data volumes (roost-lite-data, roost-lite-config) stay intact.
# If you want a clean slate for the full stack, start here:
docker compose up -d

# If you want to KEEP your Lite data, copy it across first.
# Lite and full use different volume names to avoid accidental mixing.
```

The two stacks use **separate volume names**
(`roost-lite-data` vs the default `roost_data`) so you can run them
back-to-back on the same host without collision — but it also means a
simple `docker compose up` starts fresh.

To migrate the data manually:

```bash
# Dump from Lite
docker run --rm -v roost-lite-data:/src -v "$PWD":/out alpine \
    tar -czf /out/lite-data.tar.gz -C /src .

# Start full compose so its volume exists
docker compose up -d --no-start

# Restore into the full volume
docker run --rm -v roost_data:/dst -v "$PWD":/in alpine \
    tar -xzf /in/lite-data.tar.gz -C /dst

docker compose up -d
```

## When NOT to Use Lite

- You want WhatsApp / WeChat **inbound** messaging (needs public URL → full compose)
- You need JS-rendered scraping of single-page apps (needs chromium → full compose)
- You're running on a VPS accessible from the LAN or internet (use full compose and a reverse proxy)
- You want to expose the Telegram bot webhook (full compose)

## Common Commands

```bash
./scripts/run-lite.sh              # Start (seeds .env on first run)
./scripts/run-lite.sh status       # Show service state
./scripts/run-lite.sh logs         # Tail logs
./scripts/run-lite.sh down         # Stop

# Or use docker compose directly
docker compose -f docker-compose.lite.yml up -d
docker compose -f docker-compose.lite.yml logs -f roost
docker compose -f docker-compose.lite.yml down
```

## Picking an AI Provider

Lite defaults to Gemini because it has a free tier with no credit card.
If you want a different provider, replace `.env` before starting:

```bash
cp env-templates/claude-full.env .env     # Claude (paid)
cp env-templates/openai.env .env          # OpenAI (paid)
cp env-templates/ollama-local.env .env    # Ollama (local, free, offline)
```

See [`env-templates/README.md`](../env-templates/README.md) for the full matrix.

## Troubleshooting

**"Cannot connect to http://127.0.0.1:8080"**
- Wait 15-30s after `up` — the container has a health check with a start period
- Run `./scripts/run-lite.sh status` — the service should show `(healthy)`
- Run `./scripts/run-lite.sh logs` to see what it's doing

**"scrape_js returned unavailable"**
- Expected in Lite mode. Use `scrape_url` (static fetch) instead, or switch to full compose if you genuinely need JS rendering.

**"AI agent says it can't find GEMINI_API_KEY"**
- Edit `.env` and set `GEMINI_API_KEY=...` (free from <https://aistudio.google.com/apikey>)
- Run `./scripts/run-lite.sh down && ./scripts/run-lite.sh up`

**"I want to access this from my phone"**
- Lite binds to localhost only, by design. Either:
  - SSH tunnel: `ssh -L 8080:127.0.0.1:8080 you@your-laptop`
  - Or switch to full compose and put it behind a reverse proxy
