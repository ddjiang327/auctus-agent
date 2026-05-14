#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 was not found. Install Python 3.10+ first."
  exit 1
fi

mkdir -p inputs outputs logs data

if [ ! -d ".venv" ]; then
  "$PYTHON_BIN" -m venv .venv
fi

.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example. Add your model API key before starting Auctus Agent."
fi

.venv/bin/python agent.py doctor

echo
echo "Auctus Agent installed."
echo "Start it with: scripts/start_mac.sh"
