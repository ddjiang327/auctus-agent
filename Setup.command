#!/usr/bin/env bash
set -euo pipefail

# Double-click: install dependencies and start Auctus Agent on macOS.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "== Auctus Agent Setup =="
echo "1) Install dependencies"
scripts/install_mac.sh

echo
echo "2) Start local Web UI"
echo "If the browser does not open automatically, visit: http://127.0.0.1:8000"
echo

(
  command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000" || true
) &

scripts/start_mac.sh

