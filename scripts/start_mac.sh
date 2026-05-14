#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -x ".venv/bin/uvicorn" ]; then
  echo "缺少 .venv/bin/uvicorn。请先完成安装："
  echo "- 推荐：在项目根目录双击 Setup.command"
  echo "- 或手动运行：scripts/install_mac.sh"
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

echo "Starting Auctus Agent on http://${HOST}:${PORT}"
exec .venv/bin/uvicorn app.server:app --host "$HOST" --port "$PORT"
