#!/usr/bin/env bash
# deploy-remote.sh — Deploy Roost to a remote server
#
# Usage: ./deploy-remote.sh <IP> <DOMAIN> <EMAIL>
#
# Assumes setup-host.sh has already been run on the target server.
# This script:
#   1. Copies the Roost source to the remote
#   2. Runs setup-host.sh (if not already done)
#   3. Configures nginx + SSL
#   4. Starts Roost via Docker Compose
#   5. Prints the one-time setup URL

set -euo pipefail

if [[ $# -lt 3 ]]; then
    echo "Usage: $0 <IP> <DOMAIN> <EMAIL>"
    echo ""
    echo "  IP      Server IP address"
    echo "  DOMAIN  Domain name (e.g., palvinder.ethanseow.com)"
    echo "  EMAIL   User's email (for SSL cert + Google OAuth allowlist)"
    exit 1
fi

IP="$1"
DOMAIN="$2"
EMAIL="$3"
USERNAME="dev"

# Generate credentials
SETUP_TOKEN="$(openssl rand -hex 32)"
WEB_PASSWORD="$(openssl rand -base64 24 | tr -d '=+/')"
SESSION_SECRET="$(openssl rand -hex 32)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[+]${NC} $*"; }
warn()  { echo -e "${YELLOW}[!]${NC} $*"; }
error() { echo -e "${RED}[x]${NC} $*"; exit 1; }

SSH_OPTS="-o StrictHostKeyChecking=no -o ConnectTimeout=30"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

info "Deploying Roost to $IP ($DOMAIN) for $EMAIL"

# ── 1. Copy Roost source ──
info "Syncing Roost source to remote..."
rsync -az \
    --exclude='.env' --exclude='data/' --exclude='__pycache__' \
    --exclude='*.pyc' --exclude='.git' --exclude='.pytest_cache' \
    --exclude='tests/' \
    -e "ssh $SSH_OPTS" \
    "$PROJECT_DIR/" "root@${IP}:/tmp/roost-src/"

# ── 2. Remote setup ──
info "Running remote setup..."
ssh $SSH_OPTS "root@${IP}" bash -s << REMOTEEOF
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

USERNAME="$USERNAME"
DOMAIN="$DOMAIN"
EMAIL="$EMAIL"
WEB_PASSWORD="$WEB_PASSWORD"
SESSION_SECRET="$SESSION_SECRET"
SETUP_TOKEN="$SETUP_TOKEN"
HOMEDIR="/home/\$USERNAME"
ROOST_DIR="\$HOMEDIR/roost"

echo ""
echo "=== Roost Remote Deploy ==="
echo ""

# ── Run host hardening if not already done ──
if ! command -v docker &>/dev/null; then
    echo "[+] Running host setup..."
    bash /tmp/roost-src/scripts/setup-host.sh
else
    echo "[+] Docker already installed, skipping host setup"
fi

# ── Install Roost source ──
echo "[+] Installing Roost..."
if [[ -d "\$ROOST_DIR" ]]; then
    echo "[!] Roost already exists, updating..."
    rsync -a /tmp/roost-src/ "\$ROOST_DIR/" \
        --exclude='.env' --exclude='data/'
else
    cp -r /tmp/roost-src "\$ROOST_DIR"
fi
chown -R "\$USERNAME:\$USERNAME" "\$ROOST_DIR"

# ── Generate .env ──
echo "[+] Writing .env..."
cat > "\$ROOST_DIR/.env" << ENVEOF
# Web UI
WEB_USERNAME=\$USERNAME
WEB_PASSWORD=\$WEB_PASSWORD
SESSION_SECRET=\$SESSION_SECRET

# Google OAuth (login only)
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
ALLOWED_EMAILS=\$EMAIL

# Feature flags
ENABLE_GOOGLE=false
ENABLE_MICROSOFT=false
ENABLE_AI=true
ENABLE_TELEGRAM=false
ENABLE_NOTION=false
ENABLE_INFRA=false

# Gemini AI
GEMINI_API_KEY=
GEMINI_MODEL=gemini-3-flash-preview

# Telegram (fill in later)
TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USERS=

# Microsoft Graph (fill in later)
MS_CLIENT_ID=
MS_CLIENT_SECRET=
MS_TENANT_ID=common
MS_ENABLED=false

# Browser
CDP_ENDPOINT=http://chromium:3000
ENVEOF
chown "\$USERNAME:\$USERNAME" "\$ROOST_DIR/.env"
chmod 600 "\$ROOST_DIR/.env"

# ── Write setup token ──
echo "[+] Writing setup token..."
sudo -u "\$USERNAME" mkdir -p "\$ROOST_DIR/data"
echo "\$SETUP_TOKEN" > "\$ROOST_DIR/data/.setup_token"
chown "\$USERNAME:\$USERNAME" "\$ROOST_DIR/data/.setup_token"
chmod 600 "\$ROOST_DIR/data/.setup_token"

# ── Build and start Docker ──
echo "[+] Building and starting Roost..."
cd "\$ROOST_DIR"
sudo -u "\$USERNAME" docker compose up -d --build

# Wait for health check
echo "[+] Waiting for Roost to be healthy..."
for i in {1..30}; do
    if curl -sf http://localhost:8080/health > /dev/null 2>&1; then
        echo "[+] Roost is healthy"
        break
    fi
    sleep 2
done

# ── Nginx ──
echo "[+] Configuring nginx..."
apt-get install -y -qq nginx certbot python3-certbot-nginx > /dev/null 2>&1

cat > "/etc/nginx/sites-available/\$DOMAIN" << 'NGXEOF'
server {
    server_name DOMAIN_PLACEHOLDER;

    location /terminal/ {
        proxy_pass http://127.0.0.1:8080/terminal/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    listen 80;
}
NGXEOF

sed -i "s/DOMAIN_PLACEHOLDER/\$DOMAIN/g" "/etc/nginx/sites-available/\$DOMAIN"
ln -sf "/etc/nginx/sites-available/\$DOMAIN" "/etc/nginx/sites-enabled/\$DOMAIN"
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

# ── SSL ──
echo "[+] Requesting SSL certificate..."
certbot --nginx -d "\$DOMAIN" --non-interactive --agree-tos -m "\$EMAIL" || \
    echo "[!] Certbot failed — DNS may not have propagated. Run manually later."

# ── Git config ──
sudo -u "\$USERNAME" git config --global user.name "\$USERNAME"
sudo -u "\$USERNAME" git config --global user.email "\$EMAIL"

# ── AI session manager ──
echo "[+] Installing ai session manager..."
if [[ -f "/tmp/roost-src/scripts/ai" ]]; then
    cp /tmp/roost-src/scripts/ai /usr/local/bin/ai
    cp /tmp/roost-src/scripts/ai-start /usr/local/bin/ai-start
    chmod +x /usr/local/bin/ai /usr/local/bin/ai-start
fi

cat > "\$HOMEDIR/.bash_aliases" << 'ALIASEOF'
# AI session shortcuts
alias aic='ai claude'
alias aig='ai gemini'
alias aip='ai peek'
alias ais='ai status'
ALIASEOF
chown "\$USERNAME:\$USERNAME" "\$HOMEDIR/.bash_aliases"

# Clean up
rm -rf /tmp/roost-src

echo ""
echo "============================================"
echo "[+] Roost deployed at \$DOMAIN"
echo "============================================"
echo ""
echo "  Web UI:   https://\$DOMAIN"
echo "  SSH:      ssh \$USERNAME@\$(hostname -I | awk '{print \$1}')"
echo "  Docker:   docker compose -f \$ROOST_DIR/docker-compose.yml ps"
echo ""
REMOTEEOF

info "Deploy complete for $IP ($DOMAIN)"
echo ""
info "=== One-Time Setup URL ==="
echo ""
echo "  https://${DOMAIN}/auth/setup?token=${SETUP_TOKEN}"
echo ""
echo "  Send this URL to the user. They will set their own password."
echo "  The link expires after first use."
echo ""
