#!/usr/bin/env bash
# systemd entry point — picks up DATA_DIR / PORT / LLM_BASE_URL from
# /etc/donationtracker.env (loaded via EnvironmentFile in the unit) and
# also honors the DB-persisted https_enabled setting from the Settings page.
set -euo pipefail
cd /opt/donationtracker

PORT="${PORT:-8000}"
DATA_DIR="${DATA_DIR:-/opt/donationtracker/data}"
ARGS=(-m uvicorn app.main:app --host 0.0.0.0 --port "$PORT")

# Read persisted toggle from the SQLite settings table.
PERSISTED=""
if [ -f "$DATA_DIR/donations.db" ]; then
    PERSISTED=$(/opt/donationtracker/.venv/bin/python -c "
import sqlite3, os, sys
try:
    c = sqlite3.connect(os.environ['DATA_DIR'] + '/donations.db')
    r = c.execute(\"SELECT value FROM settings WHERE key='https_enabled'\").fetchone()
    print(r[0] if r else '')
except Exception: print('')" 2>/dev/null) || true
fi

# Env var wins; otherwise the persisted setting takes effect.
if [ -z "${USE_HTTPS:-}" ] && [ "$PERSISTED" = "1" ]; then
    export USE_HTTPS=1
fi

if [ "${USE_HTTPS:-}" = "1" ] || [ "${USE_HTTPS:-}" = "true" ]; then
    KEY="${SSL_KEY_FILE:-}"
    CRT="${SSL_CERT_FILE:-}"
    if [ -z "$KEY" ] || [ -z "$CRT" ]; then
        eval "$(/opt/donationtracker/.venv/bin/python -m app.tls --ensure)"
        KEY="$key"; CRT="$cert"
    fi
    ARGS+=(--ssl-keyfile "$KEY" --ssl-certfile "$CRT")
fi

exec /opt/donationtracker/.venv/bin/python "${ARGS[@]}"
