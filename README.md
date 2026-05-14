# Auctus Agent

本地运行的个人 AI Agent。它能处理文件、生成 Markdown / Excel / HTML、维护长期记忆、支持 Web UI / Telegram，并带有自我进化闭环。

## 快速开始

Mac:

```bash
cd secretary
scripts/install_mac.sh
scripts/start_mac.sh
```

Windows PowerShell:

```powershell
cd secretary
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1
```

然后打开：

```text
http://127.0.0.1:8000
```

完整安装说明见 [INSTALL.md](INSTALL.md)。

## 配置模型

首次安装会从 `.env.example` 生成 `.env`。至少填写一个模型 API key：

```bash
MODEL=deepseek/deepseek-chat
DEEPSEEK_API_KEY=sk-...
```

也可以切换到 OpenAI、Anthropic、DashScope 或 Ollama：

```bash
MODEL=gpt-4o
OPENAI_API_KEY=sk-...

MODEL=claude-sonnet-4-5
ANTHROPIC_API_KEY=sk-ant-...

MODEL=ollama/llama3:8b
```

## 常用命令

```bash
python agent.py doctor
python agent.py stability-check
python agent.py usage --db
python agent.py cost-report
python agent.py sleep --force
python agent.py evolve scan
python agent.py evolve list --status pending
```

## Telegram

在 `.env` 中配置：

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=123456789
```

另开终端运行：

```bash
python -m app.telegram_bot
```

## 目录结构

```text
secretary/
├── app/                 # FastAPI、Agent、工具、记忆、Relay
├── prompts/             # system prompt
├── scripts/             # Mac / Windows 安装与启动脚本
├── inputs/              # 输入文件
├── outputs/             # 生成文件
├── data/                # SQLite + Chroma
├── logs/                # 工具调用日志
├── INSTALL.md
├── requirements.txt
└── agent.py             # CLI
```

## 设计取舍

- 本地优先：数据默认保存在 `data/`、`outputs/`、`logs/`。
- 多模型：通过 LiteLLM 支持 DeepSeek、OpenAI、Anthropic、Ollama 等。
- 安全记忆：候选学习需要确认后才会进入运行时；已应用学习项可停用或回滚。
