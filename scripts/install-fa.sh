#!/usr/bin/env bash
# install-fa.sh — One-command bootstrap for the Roost FA (Financial Adviser) edition.
#
# What this brings up:
#   * Roost (web UI + MCP + Telegram operator bot)
#   * Chatwoot 4.14.1 + Sidekiq + Postgres + Redis (fronts WhatsApp / WeChat / Email)
#   * Tailscale sidecar exposing Chatwoot over a Tailscale Funnel URL
#
# Surfaces: Chatwoot = customer-facing (their WhatsApp lands there).
#           Telegram = adviser-facing (alerts + /nlist draft approvals + brief).
#
# What this script handles:
#   * Distro / docker / openssl preflight
#   * Clone or update the repo (default branch: feature/fa-edition)
#   * Seed .env from env-templates/fa.env if missing
#   * Auto-generate SESSION_SECRET, CHATWOOT_POSTGRES_PASSWORD, CHATWOOT_SECRET_KEY_BASE
#     (only if they still hold the CHANGE_ME sentinel — re-running is safe)
#   * Interactively prompt for TAILSCALE_AUTHKEY when stdin is a TTY
#   * Create the host bind-mount dirs (data/, claude-auth/, gemini-auth/, codex-auth/, backups/)
#   * `docker compose -f docker-compose.yml -f docker-compose.fa.yml pull && up -d`
#   * Wait for chatwoot to become healthy, then surface the Tailscale Funnel URL
#
# What stays manual (see docs/fa-laptop-install.md):
#   * Chatwoot first-boot wizard (super-admin account)
#   * Creating the WhatsApp Cloud inbox INSIDE Chatwoot (Meta creds go there, not here)
#   * Generating the Chatwoot API token + webhook secret and pasting into .env
#   * Setting CHATWOOT_FRONTEND_URL to the Funnel host
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/crazyguy106/roost/feature/fa-edition/scripts/install-fa.sh | bash
#
#   Or locally:
#     bash scripts/install-fa.sh
#
# Environment overrides:
#   ROOST_DIR        — where to clone (default: $HOME/roost)
#   ROOST_BRANCH     — git branch (default: feature/fa-edition)
#   ROOST_REPO       — git URL (default: https://github.com/crazyguy106/roost.git)
#   SKIP_DOCKER      — set to 1 to skip Docker install (already have it)
#   SKIP_BRINGUP     — set to 1 to stop after .env/secrets are ready (no docker compose)

set -euo pipefail

ROOST_DIR="${ROOST_DIR:-$HOME/roost}"
ROOST_BRANCH="${ROOST_BRANCH:-feature/fa-edition}"
ROOST_REPO="${ROOST_REPO:-https://github.com/crazyguy106/roost.git}"
SKIP_DOCKER="${SKIP_DOCKER:-0}"
SKIP_BRINGUP="${SKIP_BRINGUP:-0}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
step()  { echo -e "${BLUE}[>]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }

have() { command -v "$1" &>/dev/null; }

sudo_cmd() {
    if [[ $EUID -eq 0 ]]; then
        "$@"
    elif have sudo; then
        sudo "$@"
    else
        error "need root or sudo to run: $*"
    fi
}

# Replace exactly one .env line that currently holds the CHANGE_ME sentinel.
# Matches anchored on the full sentinel string so partial CHANGE_ME values
# (e.g. WEB_PASSWORD=CHANGE_ME) are NOT touched by the wrong key's replacement.
# Idempotent: a second invocation does nothing because the sentinel is gone.
replace_sentinel() {
    local key="$1" sentinel="$2" value="$3" file="$4"
    # Use # as sed delimiter — generated secrets are hex, no embedded # / /
    if grep -q "^${key}=${sentinel}\$" "$file"; then
        sed -i "s#^${key}=${sentinel}\$#${key}=${value}#" "$file"
        info "  ${key} → generated"
    fi
}

echo ""
echo "============================================"
echo "  Roost FA Edition — Installer"
echo "============================================"
echo ""

# ── 1. Distro check ──
if [[ ! -f /etc/os-release ]]; then
    error "Cannot detect distro — /etc/os-release missing. Only Ubuntu and Debian are supported."
fi
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-}" in
    ubuntu|debian) info "Distro: $PRETTY_NAME" ;;
    *)             warn "Distro '$ID' not tested. Continuing, but YMMV." ;;
esac

# ── 2. Base packages ──
step "Checking base packages (git, curl, openssl, ca-certificates)..."
MISSING=()
for pkg in git curl openssl ca-certificates; do
    have "$pkg" || MISSING+=("$pkg")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    info "Installing: ${MISSING[*]}"
    sudo_cmd apt-get update -qq
    sudo_cmd apt-get install -y -qq "${MISSING[@]}"
else
    info "Base packages already present"
fi

# ── 3. Docker ──
if [[ "$SKIP_DOCKER" == "1" ]]; then
    info "SKIP_DOCKER=1 — skipping Docker install"
elif have docker && docker compose version &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    step "Installing Docker Engine + compose plugin..."
    sudo_cmd install -m 0755 -d /etc/apt/keyrings
    KEY_URL="https://download.docker.com/linux/${ID}/gpg"
    curl -fsSL "$KEY_URL" | sudo_cmd gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
    sudo_cmd chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME:-stable} stable" \
        | sudo_cmd tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo_cmd apt-get update -qq
    sudo_cmd apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    info "Docker installed: $(docker --version)"
fi

if [[ $EUID -ne 0 ]] && have docker; then
    if ! groups | grep -q docker; then
        step "Adding $USER to the docker group..."
        sudo_cmd usermod -aG docker "$USER"
        warn "You may need to log out and back in for docker group membership to take effect."
    fi
fi

# ── 4. Clone or update repo ──
if [[ -d "$ROOST_DIR/.git" ]]; then
    step "Updating existing clone at $ROOST_DIR..."
    git -C "$ROOST_DIR" fetch --quiet origin "$ROOST_BRANCH"
    git -C "$ROOST_DIR" checkout --quiet "$ROOST_BRANCH"
    git -C "$ROOST_DIR" pull --quiet --ff-only origin "$ROOST_BRANCH" || \
        warn "Could not fast-forward — you may have local changes. Skipping pull."
else
    step "Cloning $ROOST_REPO ($ROOST_BRANCH) to $ROOST_DIR..."
    git clone --quiet --branch "$ROOST_BRANCH" "$ROOST_REPO" "$ROOST_DIR"
fi

cd "$ROOST_DIR"

# ── 5. Seed .env from fa.env template ──
if [[ -f .env ]]; then
    info ".env already exists — leaving it alone (will still try to fill CHANGE_ME sentinels)"
else
    if [[ -f env-templates/fa.env ]]; then
        cp env-templates/fa.env .env
        chmod 600 .env
        info "Seeded .env from env-templates/fa.env (0600)"
    else
        error "env-templates/fa.env missing — branch $ROOST_BRANCH may not have the FA edition. Try ROOST_BRANCH=feature/fa-edition."
    fi
fi

# ── 6. Auto-generate secrets that still hold the CHANGE_ME sentinel ──
step "Filling auto-generated secrets (skips any already set)..."
replace_sentinel SESSION_SECRET                CHANGE_ME_OPENSSL_RAND_HEX_32 "$(openssl rand -hex 32)" .env
replace_sentinel CHATWOOT_POSTGRES_PASSWORD    CHANGE_ME_OPENSSL_RAND_HEX_24 "$(openssl rand -hex 24)" .env
replace_sentinel CHATWOOT_SECRET_KEY_BASE      CHANGE_ME_OPENSSL_RAND_HEX_64 "$(openssl rand -hex 64)" .env

# ── 7. Tailscale auth key prompt ──
if grep -q '^TAILSCALE_AUTHKEY=CHANGE_ME$' .env; then
    if [[ -t 0 && -t 1 ]]; then
        echo ""
        warn "Tailscale Funnel needs an auth key to come up."
        warn "Generate one at: https://login.tailscale.com/admin/settings/keys"
        warn "Pick 'Reusable' off, 'Ephemeral' off, 'Pre-approved' on (if your tailnet uses device approval)."
        read -r -p "Paste TAILSCALE_AUTHKEY (or leave blank to fill in later): " _tskey
        if [[ -n "$_tskey" ]]; then
            replace_sentinel TAILSCALE_AUTHKEY CHANGE_ME "$_tskey" .env
        else
            warn "Skipped — Tailscale sidecar will fail to register until TAILSCALE_AUTHKEY is set."
        fi
    else
        warn "TAILSCALE_AUTHKEY=CHANGE_ME and stdin is not a TTY — set it manually in .env before bring-up."
    fi
fi

# ── 7b. Telegram operator bot prompts ──
# Telegram is the adviser's notification channel in FA edition. Two values
# come from outside the laptop:
#   * Bot token  — @BotFather → /newbot → copy.
#   * User id    — DM @userinfobot from your own Telegram → copy the number.
# Skipping is OK: entrypoint.sh logs a warning and brings the rest of the stack
# up. You can paste these into .env later and `docker compose ... restart roost`.
if grep -q '^TELEGRAM_BOT_TOKEN=CHANGE_ME_AFTER_BOTFATHER$' .env; then
    if [[ -t 0 && -t 1 ]]; then
        echo ""
        warn "Telegram operator bot needs a token to come up."
        warn "Open @BotFather in Telegram, send /newbot, follow the prompts."
        read -r -p "Paste TELEGRAM_BOT_TOKEN (or leave blank to fill in later): " _tgtoken
        if [[ -n "$_tgtoken" ]]; then
            replace_sentinel TELEGRAM_BOT_TOKEN CHANGE_ME_AFTER_BOTFATHER "$_tgtoken" .env
        else
            warn "Skipped — Telegram operator bot will not start until TELEGRAM_BOT_TOKEN is set."
        fi
    else
        warn "TELEGRAM_BOT_TOKEN=CHANGE_ME_AFTER_BOTFATHER and stdin is not a TTY — set it manually in .env before bring-up."
    fi
fi

if grep -q '^TELEGRAM_ALLOWED_USERS=CHANGE_ME_YOUR_TELEGRAM_USER_ID$' .env; then
    if [[ -t 0 && -t 1 ]]; then
        echo ""
        warn "Telegram needs your numeric user id so only you can drive the bot."
        warn "Open @userinfobot in Telegram, send any message, copy the 'Id' value."
        read -r -p "Paste TELEGRAM_ALLOWED_USERS (numeric, comma-separated for multi; blank to skip): " _tgusers
        if [[ -n "$_tgusers" ]]; then
            replace_sentinel TELEGRAM_ALLOWED_USERS CHANGE_ME_YOUR_TELEGRAM_USER_ID "$_tgusers" .env
        else
            warn "Skipped — bot will deny all commands until TELEGRAM_ALLOWED_USERS is set."
        fi
    else
        warn "TELEGRAM_ALLOWED_USERS=CHANGE_ME_YOUR_TELEGRAM_USER_ID and stdin is not a TTY — set it manually in .env before bring-up."
    fi
fi

# ── 8. Host bind-mount dirs ──
# docker-compose.fa.yml mounts these as `./data`, `./claude-auth`, etc. Pre-create
# so Docker doesn't auto-create them as root-owned (which would lock the host user
# out of editing CLI auth files later).
step "Creating host bind-mount directories..."
mkdir -p data claude-auth gemini-auth codex-auth backups roost-config
chmod 700 claude-auth gemini-auth codex-auth backups
info "  data/ claude-auth/ gemini-auth/ codex-auth/ backups/ roost-config/"

# ── 9. Bring up the stack ──
if [[ "$SKIP_BRINGUP" == "1" ]]; then
    info "SKIP_BRINGUP=1 — stopping here. To start manually:"
    echo "    docker compose -f docker-compose.yml -f docker-compose.fa.yml up -d"
    exit 0
fi

step "Pulling images (may take a few minutes on first run)..."
docker compose -f docker-compose.yml -f docker-compose.fa.yml pull

step "Bringing up the FA stack..."
docker compose -f docker-compose.yml -f docker-compose.fa.yml up -d

# ── 10. Wait for chatwoot to be healthy ──
step "Waiting for Chatwoot to become healthy (up to 5 min)..."
DEADLINE=$(( $(date +%s) + 300 ))
HEALTHY=0
while [[ $(date +%s) -lt $DEADLINE ]]; do
    state=$(docker compose -f docker-compose.yml -f docker-compose.fa.yml ps --format '{{.Service}} {{.Health}}' 2>/dev/null \
            | awk '$1=="chatwoot"{print $2}')
    if [[ "$state" == "healthy" ]]; then
        HEALTHY=1; break
    fi
    sleep 5
done

if [[ "$HEALTHY" -ne 1 ]]; then
    warn "Chatwoot didn't reach healthy state in 5 minutes."
    warn "Check logs: docker compose -f docker-compose.yml -f docker-compose.fa.yml logs chatwoot"
else
    info "Chatwoot is healthy."
fi

# ── 11. Surface the Tailscale Funnel URL ──
step "Looking up the Tailscale Funnel URL..."
FUNNEL_URL=""
# Give Tailscale a moment to register and write the URL to its log.
for _ in 1 2 3 4 5 6; do
    FUNNEL_URL=$(docker compose -f docker-compose.yml -f docker-compose.fa.yml logs --tail 200 tailscale 2>/dev/null \
                 | grep -oE 'https://[a-z0-9.-]+\.ts\.net' | tail -n 1 || true)
    [[ -n "$FUNNEL_URL" ]] && break
    sleep 5
done
# Fallback: ask `tailscale status` inside the container.
if [[ -z "$FUNNEL_URL" ]]; then
    FUNNEL_URL=$(docker compose -f docker-compose.yml -f docker-compose.fa.yml exec -T tailscale tailscale status --json 2>/dev/null \
                 | grep -oE '"DNSName": *"[^"]+"' | head -n 1 | sed 's/.*: *"\([^"]*\)".*/https:\/\/\1/' | sed 's/\.$//' || true)
fi

echo ""
echo "============================================"
echo "  FA Edition is up"
echo "============================================"
echo ""
echo "  Roost (local):     http://127.0.0.1:8080"
if [[ -n "$FUNNEL_URL" ]]; then
    echo "  Chatwoot (public): $FUNNEL_URL"
else
    warn "Could not auto-detect the Funnel URL."
    warn "Run:  docker compose -f docker-compose.yml -f docker-compose.fa.yml logs tailscale"
    warn "      and look for the https://*.ts.net line."
fi
echo ""
echo "  Next steps (manual — see docs/fa-laptop-install.md):"
echo ""
echo "    1. Open the Chatwoot URL above and complete the first-boot wizard"
echo "       (super-admin email + password)."
echo ""
echo "    2. Profile → Access Token → copy. Paste into .env:"
echo "         CHATWOOT_API_KEY=<token>"
echo ""
echo "    3. Settings → Inboxes → Add Inbox → WhatsApp → Cloud API."
echo "       Paste Meta WhatsApp credentials INTO CHATWOOT (not into Roost's .env)."
echo "       Note the inbox ID from the URL and paste into .env:"
echo "         CHATWOOT_INBOX_ID=<id>"
echo ""
echo "    4. Settings → Integrations → Webhooks → Add."
echo "       URL:           ${FUNNEL_URL:-https://<funnel-host>}/api/chatwoot/webhook"
echo "       Subscriptions: at minimum message_created"
echo "       Copy the secret it generates. Paste into .env:"
echo "         CHATWOOT_WEBHOOK_SECRET=<secret>"
echo ""
echo "    5. Set CHATWOOT_FRONTEND_URL in .env to the Funnel URL above."
echo ""
echo "    6. Restart Roost to pick up the new .env values:"
echo "         docker compose -f docker-compose.yml -f docker-compose.fa.yml restart roost"
echo ""
echo "  Full runbook: docs/fa-laptop-install.md"
echo ""
