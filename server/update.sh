#!/usr/bin/env bash
# Polls GitHub for new commits and redeploys if anything has changed.
# Intended to run from cron every 5 minutes.
# Install: crontab -e  →  */5 * * * * /home/pi/netatmo-home-hub/server/update.sh
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE="netatmo-proxy"
LOG_TAG="netatmo-update"

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
log "Pull done. Restarting $SERVICE..."
sudo systemctl restart "$SERVICE"
log "Done."
