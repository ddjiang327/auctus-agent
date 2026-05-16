#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

if [ -d "auctus-agent" ] && [ -d "auctus-agent/app" ]; then
  cd auctus-agent
elif [ -d "app" ] && [ -d "scripts" ]; then
  :
else
  echo "未找到应用文件。请确认你在项目根目录运行。"
  read -p "按 Enter 退出..."
  exit 1
fi

echo "== Auctus Agent Setup =="
echo "1) 安装依赖"
scripts/install_mac.sh

echo
echo "2) 启动 Web UI"
echo "如未自动打开浏览器，请访问：http://127.0.0.1:8000"
echo

( command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000" || true ) &

scripts/start_mac.sh
