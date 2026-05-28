#!/usr/bin/env bash
# Polls GitHub for new commits and redeploys if anything has changed.
# Intended to run from cron every 5 minutes.
#
# Install (no shell redirect — the script logs into update.log itself, with
# size cap; the cron line stays clean):
#   crontab -e
#   */5 * * * * /home/pi/netatmo-home-hub/server/update.sh

set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="netatmo-proxy"
LOG_FILE="$REPO_DIR/server/update.log"
LOG_MAX_BYTES=$((100 * 1024))   # rotate when log exceeds 100 KiB…
LOG_KEEP_BYTES=$((50 * 1024))   # …keeping the most recent 50 KiB.

# Rotate own log BEFORE redirecting stdout, so the new FD points at the
# truncated file (no sparse writes from a stale offset).
if [ -f "$LOG_FILE" ]; then
    size=$(wc -c < "$LOG_FILE")
    if [ "$size" -gt "$LOG_MAX_BYTES" ]; then
        tail -c "$LOG_KEEP_BYTES" "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
    fi
fi
# All output from here on lands in update.log; cron no longer needs a >>.
exec >> "$LOG_FILE" 2>&1

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

cd "$REPO_DIR"

LOCAL=$(git rev-parse HEAD)
REMOTE=$(git ls-remote origin HEAD 2>/dev/null | cut -f1)

if [ -z "$REMOTE" ]; then
    log "Could not reach GitHub — skipping" >&2
    exit 0
fi

if [ "$LOCAL" = "$REMOTE" ]; then
    exit 0
fi

log "New commit detected: $LOCAL -> $REMOTE"
git pull --ff-only

# Install Python deps when requirements.txt changed in this pull — otherwise a
# newly added dependency would be missing until someone installs it by hand.
# On failure we still restart (guarded imports keep the server booting) and log
# loudly, rather than leaving new code on disk but never run.
if ! git diff --quiet "$LOCAL" HEAD -- server/requirements.txt; then
    log "requirements.txt changed — installing deps into venv..."
    if "$REPO_DIR/server/venv/bin/pip" install -q -r "$REPO_DIR/server/requirements.txt"; then
        log "Deps installed."
    else
        log "WARNING: pip install failed — restarting anyway; install deps manually" >&2
    fi
fi

log "Pull done. Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"
log "Done."
