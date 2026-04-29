#!/usr/bin/env bash
# install.sh — One-command Roost bootstrap for a fresh Ubuntu/Debian host
#
# What it does:
#   1. Verifies the distro (Ubuntu or Debian)
#   2. Installs git, curl, docker-compose plugin (if missing)
#   3. Clones the repo to ~/roost (or updates if already present)
#   4. Seeds .env from env-templates/gemini-minimal.env
#   5. Prints clear next steps (it does NOT start the stack automatically —
#      the user needs to edit .env first)
#
# Usage (as a regular user with sudo access):
#   curl -fsSL https://raw.githubusercontent.com/crazyguy106/roost/main/scripts/install.sh | bash
#
#   Or locally:
#     bash scripts/install.sh
#
# Idempotent: safe to re-run. Skips steps that are already done.
#
# Environment overrides:
#   ROOST_DIR      — where to clone (default: $HOME/roost)
#   ROOST_BRANCH   — git branch to check out (default: main)
#   ROOST_REPO     — git URL (default: https://github.com/crazyguy106/roost.git)
#   SKIP_DOCKER    — set to 1 to skip Docker install (already have it)

set -euo pipefail

ROOST_DIR="${ROOST_DIR:-$HOME/roost}"
ROOST_BRANCH="${ROOST_BRANCH:-main}"
ROOST_REPO="${ROOST_REPO:-https://github.com/crazyguy106/roost.git}"
SKIP_DOCKER="${SKIP_DOCKER:-0}"

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

echo ""
echo "========================================"
echo "  Roost — One-command Installer"
echo "========================================"
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

# ── 2. Install base packages ──
step "Checking base packages (git, curl, ca-certificates)..."
MISSING=()
for pkg in git curl ca-certificates; do
    have "$pkg" || MISSING+=("$pkg")
done
if [[ ${#MISSING[@]} -gt 0 ]]; then
    info "Installing: ${MISSING[*]}"
    sudo_cmd apt-get update -qq
    sudo_cmd apt-get install -y -qq "${MISSING[@]}"
else
    info "Base packages already present"
fi

# ── 3. Install Docker ──
if [[ "$SKIP_DOCKER" == "1" ]]; then
    info "SKIP_DOCKER=1 — skipping Docker install"
elif have docker && docker compose version &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    step "Installing Docker Engine + compose plugin..."
    sudo_cmd install -m 0755 -d /etc/apt/keyrings
    # Pick the keyring URL based on distro
    KEY_URL="https://download.docker.com/linux/${ID}/gpg"
    curl -fsSL "$KEY_URL" | sudo_cmd gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
    sudo_cmd chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} ${VERSION_CODENAME:-stable} stable" \
        | sudo_cmd tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo_cmd apt-get update -qq
    sudo_cmd apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    info "Docker installed: $(docker --version)"
fi

# Make sure current user can run docker without sudo
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
    step "Cloning $ROOST_REPO to $ROOST_DIR..."
    git clone --quiet --branch "$ROOST_BRANCH" "$ROOST_REPO" "$ROOST_DIR"
fi

cd "$ROOST_DIR"

# ── 5. Seed .env ──
if [[ -f .env ]]; then
    info ".env already exists — leaving it alone"
else
    if [[ -f env-templates/gemini-minimal.env ]]; then
        cp env-templates/gemini-minimal.env .env
        chmod 600 .env
        info "Seeded .env from env-templates/gemini-minimal.env (0600)"
    else
        warn "No env-templates/gemini-minimal.env — you'll need to create .env manually"
    fi
fi

# ── 6. Optional: enable Claude Code auto mode ──
# Roost is built for continuous, autonomous Claude Code workflows (recipes,
# RPA flows, MCP-tool chains) where pausing on every tool call breaks the
# loop. If Claude Code is installed, offer to set auto mode as the default —
# written to ~/.claude/settings.json (user scope, opt-in, never silent).
if have claude; then
    echo ""
    if [[ -t 0 && -t 1 ]]; then
        read -r -p "Enable Claude Code auto mode as your default? [y/N] " _reply
    else
        _reply="${ROOST_ENABLE_AUTO_MODE:-n}"
    fi
    if [[ "$_reply" =~ ^[Yy]$ ]]; then
        CC_SETTINGS="$HOME/.claude/settings.json"
        mkdir -p "$HOME/.claude"
        if have python3; then
            python3 - "$CC_SETTINGS" <<'PY'
import json, sys, os
path = sys.argv[1]
data = {}
if os.path.exists(path):
    try:
        with open(path) as f: data = json.load(f) or {}
    except Exception: data = {}
data.setdefault("permissions", {})["defaultMode"] = "auto"
with open(path, "w") as f: json.dump(data, f, indent=2)
PY
            info "Set permissions.defaultMode=\"auto\" in $CC_SETTINGS"
        else
            warn "python3 not found — skipped settings.json write"
        fi
    else
        info "Skipped — run 'claude --permission-mode auto' to opt in per-session"
    fi
fi

# ── Done ──
echo ""
echo "========================================"
echo "  Installed"
echo "========================================"
echo ""
echo "  Repo:     $ROOST_DIR"
echo "  Branch:   $ROOST_BRANCH"
if have docker; then
    echo "  Docker:   $(docker --version)"
fi
echo ""
echo "  Next steps:"
echo ""
echo "    1. Edit .env and set GEMINI_API_KEY (or switch to another template):"
echo "         cd $ROOST_DIR"
echo "         \$EDITOR .env"
echo ""
echo "    2a. Start Roost Lite (simplest, localhost-only):"
echo "         ./scripts/run-lite.sh"
echo ""
echo "    2b. Or start the full stack (chromium + LAN-visible ports):"
echo "         docker compose up -d"
echo ""
echo "    3. Open http://127.0.0.1:8080"
echo ""
echo "  Pick a different AI provider:"
echo "    cp env-templates/claude-full.env .env    # Claude"
echo "    cp env-templates/openai.env .env         # OpenAI"
echo "    cp env-templates/ollama-local.env .env   # Ollama (offline)"
echo ""
