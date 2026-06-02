#!/usr/bin/env bash
# Roost update wrapper: snapshot host state, then pull + recreate.
#
# Snapshots .env, the per-vendor CLI auth dirs, ./data (roost.db + RPA state),
# and pg_dumps the Chatwoot DB if that overlay is running. Everything tars into
# backups/pre-update-<ISO>.tar.gz at mode 0600 (the .env ledger lives here, so
# the archive must stay private). Then runs `docker compose pull` and
# `docker compose up -d` with whatever flags the caller passed.
#
# Override retention via env: ROOST_BACKUP_KEEP=5 (default).
# Compose project name via env: COMPOSE_PROJECT_NAME=roost (default).
#
# Usage:
#   scripts/roost-update.sh                          # base compose
#   scripts/roost-update.sh -f docker-compose.yml -f docker-compose.public.yml

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

BACKUP_DIR="$REPO_ROOT/backups"
KEEP="${ROOST_BACKUP_KEEP:-5}"
STAMP="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
ARCHIVE="$BACKUP_DIR/pre-update-$STAMP.tar.gz"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

# Build the list of host paths that actually exist. tar errors out hard on
# a missing path, so we filter first.
paths=()
for p in .env claude-auth gemini-auth codex-auth data roost-config; do
  [ -e "$REPO_ROOT/$p" ] && paths+=("$p")
done
# Include any prior .env.bak.* the user keeps (credential ledger backups).
shopt -s nullglob
for bak in "$REPO_ROOT"/.env.bak.*; do
  paths+=("$(basename "$bak")")
done
shopt -u nullglob

if [ ${#paths[@]} -eq 0 ]; then
  echo "roost-update: nothing to back up under $REPO_ROOT, refusing to continue" >&2
  exit 1
fi

echo "roost-update: snapshotting ${#paths[@]} path(s) -> $ARCHIVE"
umask 077
tar -czf "$ARCHIVE" -C "$REPO_ROOT" "${paths[@]}"
chmod 600 "$ARCHIVE"

# Chatwoot pg_dump, only if the overlay is up. Empty/non-zero exit shouldn't
# block the update — we just warn.
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$REPO_ROOT")}"
PG_CONTAINER="${PROJECT}-chatwoot-postgres-1"
if docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER"; then
  PG_DUMP="$BACKUP_DIR/chatwoot-$STAMP.sql.gz"
  echo "roost-update: pg_dump chatwoot -> $PG_DUMP"
  if docker exec "$PG_CONTAINER" pg_dump -U chatwoot -d chatwoot 2>/dev/null | gzip > "$PG_DUMP"; then
    chmod 600 "$PG_DUMP"
  else
    echo "roost-update: pg_dump failed (continuing)" >&2
    rm -f "$PG_DUMP"
  fi
fi

# Prune to last KEEP archives of each kind (pre-update-*, chatwoot-*).
prune() {
  local pattern="$1"
  # shellcheck disable=SC2012
  ls -1t "$BACKUP_DIR"/$pattern 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
}
prune 'pre-update-*.tar.gz'
prune 'chatwoot-*.sql.gz'

echo "roost-update: pulling images"
docker compose "$@" pull

echo "roost-update: recreating containers"
docker compose "$@" up -d

echo "roost-update: done. Backup: $ARCHIVE"
