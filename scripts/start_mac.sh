#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "Missing .venv/bin/uvicorn. Please complete installation first:"
  echo "- Recommended: double-click Setup.command in the project root"
  echo "- Or run manually: scripts/install_mac.sh"
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "Starting Auctus Agent on http://${HOST}:${PORT}"
exec .venv/bin/uvicorn app.server:app --host "$HOST" --port "$PORT"
