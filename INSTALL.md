# Auctus Agent 安装教程

Auctus Agent 是本地运行的个人 AI Agent。它会在本机保存对话、记忆、输出文件和工具日志，浏览器访问地址默认是 `http://127.0.0.1:8000`。

## 一键 Setup（推荐，适合普通用户）

在项目根目录（包含 `auctus-agent/` 的那一层）：

- Mac：双击 `Setup.command`
- Windows：双击 `Setup.bat`

第一次打开会进入"首次设置向导"，可以在网页里选择模型接入方式并填写/验证 API key（无需手动编辑 `.env`）。

> 如果 macOS 提示"无法打开"或没有执行权限：在终端进入项目根目录后运行 `chmod +x Setup.command Start.command` 再双击。

## Mac 安装

```bash
cd auctus-agent
scripts/install_mac.sh
```

安装完成后，你可以：

- 直接启动 Web UI，在“首次设置向导/设置面板”里配置模型与 API key（推荐）
- 或者手动编辑 `.env`，至少填写一个模型 API key。例如：

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
cd auctus-agent
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

安装完成后，你可以直接启动 Web UI 在网页里完成设置，或手动编辑 `.env` 填写 API key。

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
python agent.py cron list
```

## 定时任务（cron）

如果你希望“每天自动备份记账数据 / 每周自动生成报表”，可以用 `agent.py cron` 生成可执行脚本并导出 crontab。

使用说明见：`scripts/cron/README.md`。

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
