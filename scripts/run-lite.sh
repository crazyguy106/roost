#!/usr/bin/env bash
# run-lite.sh — Start Roost in Lite mode
#
# Lite mode is a slimmed-down stack for first-time evaluation:
#   - No chromium sidecar (scrape_js returns "unavailable")
#   - No WhatsApp/WeChat webhooks (no public URL needed)
#   - Ports bound to 127.0.0.1 only (not the LAN)
#   - Reduced resource limits (1.5 CPU, 1GB RAM) for laptops
#
# Usage:
#   ./scripts/run-lite.sh            # seed .env and start
#   ./scripts/run-lite.sh down       # stop
#   ./scripts/run-lite.sh logs       # tail logs
#   ./scripts/run-lite.sh status     # show service state

set -euo pipefail

cd "$(dirname "$0")/.."

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; exit 1; }

COMPOSE_FILE="docker-compose.lite.yml"
[[ -f "$COMPOSE_FILE" ]] || error "$COMPOSE_FILE not found — are you in the roost project root?"

cmd="${1:-up}"

case "$cmd" in
    up|start)
        # Seed .env from the minimal template if it doesn't exist
        if [[ ! -f .env ]]; then
            if [[ -f env-templates/gemini-minimal.env ]]; then
                cp env-templates/gemini-minimal.env .env
                chmod 600 .env
                info "Created .env from env-templates/gemini-minimal.env"
                warn "Edit .env and set GEMINI_API_KEY before the agent will work"
                warn "Get a free key at https://aistudio.google.com/apikey"
                echo ""
                echo "  \$EDITOR .env"
                echo ""
                read -r -p "Press Enter once .env is ready, or Ctrl-C to abort..." _
            else
                error ".env missing and env-templates/gemini-minimal.env not found"
            fi
        fi

        info "Starting Roost Lite..."
        docker compose -f "$COMPOSE_FILE" up -d

        echo ""
        info "Roost Lite is starting. Check status with:"
        echo "    ./scripts/run-lite.sh status"
        echo ""
        info "Once healthy, open: http://127.0.0.1:8080"
        ;;

    down|stop)
        info "Stopping Roost Lite..."
        docker compose -f "$COMPOSE_FILE" down
        ;;

    logs)
        docker compose -f "$COMPOSE_FILE" logs -f --tail=100
        ;;

    status|ps)
        docker compose -f "$COMPOSE_FILE" ps
        ;;

    *)
        error "Unknown command: $cmd (use: up | down | logs | status)"
        ;;
esac
