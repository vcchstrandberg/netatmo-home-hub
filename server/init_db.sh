#!/usr/bin/env bash
# Initialize the SQLite database — creates server/metrics.db with the
# tables and indexes defined in netatmo_proxy.py's _db_init() function.
#
# Normally _db_init() runs automatically on first server start, so you only
# need this script when:
#   - Scripting a fresh install before starting the service
#   - Resetting after `rm metrics.db` (history wipe)
#   - Verifying the schema is current after pulling a new version
#
# Safe to run repeatedly; uses CREATE TABLE IF NOT EXISTS, no destructive
# operations. Existing data is preserved.
#
# Usage:
#   server/init_db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -x "venv/bin/python3" ]; then
  echo "error: venv/bin/python3 not found. Run server/setup.sh first." >&2
  exit 1
fi

# netatmo_proxy.py reads the Netatmo creds at import time. _db_init() itself
# doesn't touch them, but the import fails if they're unset — pass dummy
# values so the script runs without a populated .env. No API calls happen.
NETATMO_CLIENT_ID="${NETATMO_CLIENT_ID:-x}" \
NETATMO_CLIENT_SECRET="${NETATMO_CLIENT_SECRET:-x}" \
NETATMO_REFRESH_TOKEN="${NETATMO_REFRESH_TOKEN:-x}" \
venv/bin/python3 - <<'PY'
import netatmo_proxy as np
np._db_init()
import sqlite3
con = sqlite3.connect(np.DB_FILE)
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
).fetchall()]
print(f"DB ready at {np.DB_FILE}")
print(f"Tables: {', '.join(tables) if tables else '(none)'}")
for t in tables:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n} row{'' if n == 1 else 's'}")
PY
