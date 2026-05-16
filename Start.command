#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -d "auctus-agent" ] && [ -d "auctus-agent/app" ]; then
  cd auctus-agent
elif [ -d "app" ] && [ -d "scripts" ]; then
  :
else
  echo "未找到应用文件。请先运行 Setup.command。"
  read -p "按 Enter 退出..."
  exit 1
fi

( command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000" || true ) &

scripts/start_mac.sh
