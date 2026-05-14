# Auctus Agent 安装教程

Auctus Agent 是本地运行的个人 AI Agent。它会在本机保存对话、记忆、输出文件和工具日志，浏览器访问地址默认是 `http://127.0.0.1:8000`。

## Mac 安装

```bash
cd secretary
scripts/install_mac.sh
```

安装完成后，打开 `.env`，至少填写一个模型 API key。例如：

```bash
MODEL=deepseek/deepseek-chat
DEEPSEEK_API_KEY=sk-...
```

启动：

```bash
scripts/start_mac.sh
```

然后打开：

```text
http://127.0.0.1:8000
```

## Windows 安装

在 PowerShell 里运行：

```powershell
cd secretary
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

安装完成后，打开 `.env`，至少填写一个模型 API key。

启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1
```

然后打开：

```text
http://127.0.0.1:8000
```

## 常用命令

```bash
python agent.py doctor
python agent.py stability-check
python agent.py usage --db
python agent.py cost-report
python agent.py sleep --force
python agent.py evolve list --status pending
```

## 目录说明

- `inputs/`：上传或放入待处理文件。
- `outputs/`：生成的 Markdown、Excel、HTML 文件。
- `data/`：SQLite、Chroma、记忆和用量数据。
- `logs/`：工具调用日志。

## Telegram 可选配置

1. 在 Telegram 找 `@BotFather` 创建 bot。
2. 把 token 写入 `.env`：

```bash
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=你的 Telegram user id
```

3. 另开一个终端运行：

```bash
python -m app.telegram_bot
```

## 故障排查

先运行：

```bash
python agent.py doctor
```

常见问题：

- `missing API key`：检查 `.env` 是否填写了当前 `MODEL` 对应的 key。
- `port already in use`：用 `PORT=8001 scripts/start_mac.sh` 或 Windows 启动脚本传 `-Port 8001`。
- 依赖安装失败：确认 Python 版本是 3.10+，并重新运行安装脚本。
