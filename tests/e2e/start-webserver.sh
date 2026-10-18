#!/usr/bin/env bash
set -euo pipefail

# Git Bash / WSL Playwright webServer often lacks the login-shell PATH.
export PATH="${HOME}/.local/bin:/c/Users/${USER}/.local/bin:${PATH}"

stamp() { date -Iseconds; }

echo "[e2e] $(stamp) Starting webServer command..."
if [ ! -d frontend/dist ]; then
  echo "[e2e] $(stamp) frontend/dist missing — running npm ci + build"
  (cd frontend && npm ci && npm run build)
  echo "[e2e] $(stamp) Frontend build complete"
else
  echo "[e2e] $(stamp) frontend/dist exists — skipping build"
fi
echo "[e2e] $(stamp) Seeding radio_transport=serial into the temp DB..."
PYTHONPATH=. uv run python tests/e2e/seed_radio_transport.py
unset MESHCORE_SERIAL_PORT MESHCORE_SERIAL_BAUDRATE MESHCORE_TCP_HOST MESHCORE_TCP_PORT MESHCORE_BLE_ADDRESS MESHCORE_BLE_PIN
echo "[e2e] $(stamp) Launching uvicorn..."
PYTHONPATH=. uv run uvicorn app.main:app --host 127.0.0.1 --port 8001 --no-proxy-headers
