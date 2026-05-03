#!/usr/bin/env bash
set -euo pipefail
if [ ! -d .venv ]; then
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

PORT="${PORT:-8000}"
ARGS=(-m uvicorn app.main:app --host 0.0.0.0 --port "$PORT")

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
