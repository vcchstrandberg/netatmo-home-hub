#!/usr/bin/env bash
# Initialize the SQLite database — creates server/metrics.db with the
# tables and indexes defined in netatmo_proxy.py's _db_init() function,
# and runs VACUUM to compact the file (reclaim space from any rows that
# were deleted by the retention policy).
#
# Normally _db_init() runs automatically on first server start, so you
# only need this script when:
#   - Scripting a fresh install before starting the service
#   - Resetting after `rm metrics.db` (history wipe)
#   - Reclaiming disk space after a retention change (RETAIN_DAYS in
#     netatmo_proxy.py was dropped) — the rows are deleted lazily by
#     the next insert, but the file size only shrinks after VACUUM
#
# Safe to run repeatedly; uses CREATE TABLE IF NOT EXISTS, no destructive
# operations. Existing data is preserved.
#
# VACUUM needs an exclusive lock on the DB. If the netatmo-proxy service
# is running, the script may briefly block waiting for the lock; if a
# transaction is in flight it can fail with SQLITE_BUSY. For best
# results stop the service first:
#
#   sudo systemctl stop netatmo-proxy
#   server/init_db.sh
#   sudo systemctl start netatmo-proxy
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
import os, sqlite3
import netatmo_proxy as np

np._db_init()

con = sqlite3.connect(np.DB_FILE)

# Size before vacuum
size_before = os.path.getsize(np.DB_FILE)

tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
).fetchall()]
print(f"DB ready at {np.DB_FILE}")
print(f"Retention: {np.RETAIN_DAYS} days (metrics + weather_history)")
print(f"Tables: {', '.join(tables) if tables else '(none)'}")
for t in tables:
    n = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n} row{'' if n == 1 else 's'}")

# VACUUM reclaims pages freed by the retention DELETE statements. Without
# this the file size doesn't shrink even after rows are deleted.
try:
    print("Running VACUUM…", end="", flush=True)
    con.execute("VACUUM")
    size_after = os.path.getsize(np.DB_FILE)
    saved = size_before - size_after
    if saved > 0:
        print(f" done. Reclaimed {saved/1024:.1f} KiB "
              f"({size_before/1024:.1f} → {size_after/1024:.1f} KiB).")
    else:
        print(f" done. Already compact ({size_after/1024:.1f} KiB).")
except sqlite3.OperationalError as e:
    print(f"\nVACUUM failed: {e}")
    print("Tip: stop netatmo-proxy first, then re-run this script.")
PY
