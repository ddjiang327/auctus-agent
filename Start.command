#!/usr/bin/env bash
set -euo pipefail

# Double-click: start Auctus Agent on macOS after setup.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

(
  command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000" || true
) &

scripts/start_mac.sh

