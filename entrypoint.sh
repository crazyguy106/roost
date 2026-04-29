#!/usr/bin/env bash
# =============================================================================
# Roost — Container Entrypoint
# Starts sshd (as root), then drops to user 'dev' for:
#   tmux+Claude Code, ttyd, web server, and optionally Telegram bot
# =============================================================================

set -euo pipefail

echo "[roost] Starting Roost platform..."

# ---------------------------------------------------------------------------
# 1. Start sshd (requires root)
# ---------------------------------------------------------------------------
SSH_ENABLED="${SSH_ENABLED:-true}"
if [ "$SSH_ENABLED" = "true" ]; then
    echo "[roost] Starting sshd..."
    /usr/sbin/sshd
    echo "[roost] sshd started on port 22"
fi

# ---------------------------------------------------------------------------
# Drop to user 'dev' for all remaining services
# ---------------------------------------------------------------------------
exec gosu dev bash -c '
set -euo pipefail

# 1.5. Bootstrap setup token on first boot.
#      If no web password is configured (or still the default placeholder)
#      and no setup token already exists, mint one and log the URL the user
#      should hit to finish setup via the wizard.
SETUP_TOKEN_FILE="/app/data/.setup_token"
mkdir -p /app/data
if [ ! -f "$SETUP_TOKEN_FILE" ]; then
    PW_VAL="${WEB_PASSWORD:-}"
    if [ -z "$PW_VAL" ] || [ "$PW_VAL" = "change-me" ]; then
        TOKEN="$(python -c "import secrets; print(secrets.token_urlsafe(32))")"
        printf "%s" "$TOKEN" > "$SETUP_TOKEN_FILE"
        chmod 600 "$SETUP_TOKEN_FILE"
        echo "[roost] ┌──────────────────────────────────────────────────────────────"
        echo "[roost] │ First-run setup required."
        echo "[roost] │ Open this URL in your browser to set a password and pick"
        echo "[roost] │ a deployment shape:"
        echo "[roost] │"
        echo "[roost] │   http://localhost:8080/auth/setup?token=${TOKEN}"
        echo "[roost] │"
        echo "[roost] │ (token is single-use; lost? rm data/.setup_token and restart)"
        echo "[roost] └──────────────────────────────────────────────────────────────"
    fi
fi

# 2. Create tmux session running Claude Code
echo "[roost] Creating tmux session ai-claude with Claude Code..."
tmux new-session -d -s ai-claude -x 200 -y 50 "claude"
echo "[roost] tmux session ai-claude created"

# 3. Start ttyd (web terminal) on internal port 7681
echo "[roost] Starting ttyd on port 7681..."
ttyd \
    --port 7681 \
    --writable \
    --base-path /terminal/ \
    tmux attach -t ai-claude &
TTYD_PID=$!
echo "[roost] ttyd started (PID: $TTYD_PID)"

# 4. Start roost-web (FastAPI on port 8080)
echo "[roost] Starting roost-web on port 8080..."
python -m uvicorn roost.web.app:app \
    --host 0.0.0.0 \
    --port 8080 &
WEB_PID=$!
echo "[roost] roost-web started (PID: $WEB_PID)"

# 5. Start roost-bot if Telegram is enabled
TELEGRAM_ENABLED="${TELEGRAM_ENABLED:-false}"
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"

BOT_PID=""
if [ "$TELEGRAM_ENABLED" = "true" ] && [ -n "$TELEGRAM_BOT_TOKEN" ]; then
    echo "[roost] Starting roost-bot (Telegram)..."
    python -m roost.bot.main &
    BOT_PID=$!
    echo "[roost] roost-bot started (PID: $BOT_PID)"
else
    echo "[roost] Telegram bot disabled (TELEGRAM_ENABLED=$TELEGRAM_ENABLED)"
fi

# 6. Start messaging adapters (each is conditional on its token/config)
ADAPTER_PIDS=""

DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN:-}"
if [ -n "$DISCORD_BOT_TOKEN" ]; then
    echo "[roost] Starting Discord adapter..."
    python -m roost.adapters.discord_bot &
    ADAPTER_PIDS="$ADAPTER_PIDS $!"
    echo "[roost] Discord adapter started (PID: $!)"
fi

SLACK_BOT_TOKEN="${SLACK_BOT_TOKEN:-}"
SLACK_APP_TOKEN="${SLACK_APP_TOKEN:-}"
if [ -n "$SLACK_BOT_TOKEN" ] && [ -n "$SLACK_APP_TOKEN" ]; then
    echo "[roost] Starting Slack adapter..."
    python -m roost.adapters.slack_bot &
    ADAPTER_PIDS="$ADAPTER_PIDS $!"
    echo "[roost] Slack adapter started (PID: $!)"
fi

SIGNAL_API_URL="${SIGNAL_API_URL:-}"
SIGNAL_PHONE_NUMBER="${SIGNAL_PHONE_NUMBER:-}"
if [ -n "$SIGNAL_API_URL" ] && [ -n "$SIGNAL_PHONE_NUMBER" ]; then
    echo "[roost] Starting Signal adapter..."
    python -m roost.adapters.signal_bot &
    ADAPTER_PIDS="$ADAPTER_PIDS $!"
    echo "[roost] Signal adapter started (PID: $!)"
fi

MATRIX_HOMESERVER="${MATRIX_HOMESERVER:-}"
MATRIX_USER_ID="${MATRIX_USER_ID:-}"
if [ -n "$MATRIX_HOMESERVER" ] && [ -n "$MATRIX_USER_ID" ]; then
    echo "[roost] Starting Matrix adapter..."
    python -m roost.adapters.matrix_bot &
    ADAPTER_PIDS="$ADAPTER_PIDS $!"
    echo "[roost] Matrix adapter started (PID: $!)"
fi

# 7. Wait for all background processes
echo "[roost] All services started. Waiting for processes..."

wait_pids="$TTYD_PID $WEB_PID"
if [ -n "$BOT_PID" ]; then
    wait_pids="$wait_pids $BOT_PID"
fi
wait_pids="$wait_pids $ADAPTER_PIDS"

wait $wait_pids
'
