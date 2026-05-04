#!/usr/bin/env bash
set -euo pipefail
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

PORT="${PORT:-8000}"
ARGS=(-m uvicorn app.main:app --host 0.0.0.0 --port "$PORT")

# Honor the persisted setting saved through the Settings UI, if no env var is set.
if [ -z "${USE_HTTPS:-}" ] && [ -f "${DATA_DIR:-./data}/donations.db" ]; then
    PERSISTED=$(.venv/bin/python -c "
import sqlite3, os
try:
    c = sqlite3.connect(os.environ.get('DATA_DIR','./data') + '/donations.db')
    r = c.execute(\"SELECT value FROM settings WHERE key='https_enabled'\").fetchone()
    print(r[0] if r else '')
except Exception: print('')" 2>/dev/null)
    if [ "$PERSISTED" = "1" ]; then USE_HTTPS=1; fi
fi

if [ "${USE_HTTPS:-}" = "1" ] || [ "${USE_HTTPS:-}" = "true" ]; then
    KEY="${SSL_KEY_FILE:-}"
    CRT="${SSL_CERT_FILE:-}"
    if [ -z "$KEY" ] || [ -z "$CRT" ]; then
        eval "$(.venv/bin/python -m app.tls --ensure)"
        echo "Using self-signed cert: $cert"
        echo "(Browsers will warn the first time. Accept to continue.)"
        KEY="$key"; CRT="$cert"
    fi
    ARGS+=(--ssl-keyfile "$KEY" --ssl-certfile "$CRT")
    echo "HTTPS on https://localhost:$PORT"
else
    echo "HTTP on http://localhost:$PORT  (set USE_HTTPS=1 for TLS)"
fi

exec .venv/bin/python "${ARGS[@]}"
