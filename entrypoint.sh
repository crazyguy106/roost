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
    echo "[roost] Starting roost-bot..."
    python -m roost.bot.main &
    BOT_PID=$!
    echo "[roost] roost-bot started (PID: $BOT_PID)"
else
    echo "[roost] Telegram bot disabled (TELEGRAM_ENABLED=$TELEGRAM_ENABLED)"
fi

# 6. Wait for all background processes
echo "[roost] All services started. Waiting for processes..."

wait_pids="$TTYD_PID $WEB_PID"
if [ -n "$BOT_PID" ]; then
    wait_pids="$wait_pids $BOT_PID"
fi

wait $wait_pids
'
