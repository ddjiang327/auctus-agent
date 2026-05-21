# Auctus Agent

本地运行的个人 AI Agent。它能处理文件、生成 Markdown / Excel / HTML、维护长期记忆、支持 Web UI / Telegram，并带有自我进化闭环。

当前版本：`0.1.0`，定义在 `app/version.py`。本地服务启动后可访问 `GET /api/version` 查看版本和更新检查配置。

## 快速开始

### 一键启动（推荐）

在 `auctus-agent` 项目根目录：

- **Mac**：双击 `Setup.command`
- **Windows**：双击 `Setup.bat`

第一次打开会进入"首次设置向导"，可以在网页里选择模型接入方式并填写/验证 API key。

### 命令行安装

Mac:

```bash
cd auctus-agent
scripts/install_mac.sh
scripts/start_mac.sh
```

Windows PowerShell:

```powershell
cd auctus-agent
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1
```

然后打开：

```text
http://127.0.0.1:8000
```

完整安装说明见 [INSTALL.md](INSTALL.md)。发布规则见 [docs/release.md](docs/release.md)。

## 配置模型

首次安装会从 `.env.example` 生成 `.env`。你可以在 Web UI 的"首次设置向导/设置面板"里配置模型与 API key；也可以直接编辑 `.env`。例如：

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
# 诊断与检查
python agent.py doctor
python agent.py stability-check

# 使用统计与成本
python agent.py usage --db
python agent.py cost-report

# 记忆管理
python agent.py sleep --force

# 自我学习（Closed Learning Loop）
python agent.py evolve scan                    # 扫描历史生成学习候选
python agent.py evolve list --status pending   # 查看待确认的学习项
python agent.py evolve apply <id>              # 应用学习项
python agent.py evolve skill-scan              # 扫描生成 Skill
python agent.py evolve skill-list              # 查看 Skills

# 评估与优化（GEPA）
python agent.py evolve eval-build              # 生成评估集
python agent.py evolve eval-run                # 运行评估

# 定时任务（Cron Jobs）
python agent.py cron list                      # 查看定时任务
python agent.py cron add-run --name "weekly-report" --cron "0 9 * * 1" --file "data.md" --task "生成周报"
python agent.py cron export                    # 导出 crontab 片段（Mac/Linux）
python agent.py cron export-windows            # 导出 Windows 任务计划脚本
```

## Telegram 远程控制

在 Web UI 设置面板中配置 Telegram，或编辑 `.env`：

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
auctus-agent/
├── app/                 # FastAPI、Agent、工具、记忆、Relay
│   ├── evolve/          # 自我学习闭环（skills, eval, gepa）
│   ├── tools.py         # 工具实现
│   └── ui.html          # Web UI
├── prompts/             # system prompt
├── scripts/             # Mac / Windows 安装与启动脚本
├── inputs/              # 输入文件
├── outputs/             # 生成文件
├── data/                # SQLite + Chroma + 学习数据
├── logs/                # 工具调用日志
├── tests/               # 测试套件
├── INSTALL.md
├── requirements.txt
└── agent.py             # CLI 入口
```

## 核心特性

### 1. 本地优先
- 数据默认保存在本地 `data/`、`outputs/`、`logs/`
- 无需联网即可运行（使用本地 Ollama 模型）

### 2. 多模型支持
- 通过 LiteLLM 支持 DeepSeek、OpenAI、Anthropic、Ollama 等
- 支持 BYO Key 和 Auctus 托管 API（开发中）

### 3. 安全记忆系统
- **候选学习**：从对话中自动提取学习项，需人工确认后才生效
- **渐进披露**：Skill 采用 L0/L1 两级结构，Agent 按需使用
- **可回滚**：已应用的学习项可随时停用或回滚

### 4. 自我进化闭环（Closed Learning Loop）
```
真实任务痕迹 → 提取候选 → 人工确认 → 应用/回滚 → 运行时生效
                    ↓
              评估集生成 → GEPA 优化 → 选优变体
```

### 5. 权限与安全
- 文件访问权限：仅 workspace / 整台电脑
- 终端命令权限：默认关闭，需手动开启
- 危险命令拦截：自动阻止 rm -rf 等危险操作

## 设计取舍

- **本地优先**：数据默认保存在 `data/`、`outputs/`、`logs/`。
- **多模型**：通过 LiteLLM 支持 DeepSeek、OpenAI、Anthropic、Ollama 等。
- **安全记忆**：候选学习需要确认后才会进入运行时；已应用学习项可停用或回滚。
- **渐进披露**：Skill 文档采用 L0（核心）+ L1（详细）结构，避免提示词膨胀。

## 文档

- [Plan](docs/plan.md) - 当前桌面端产品边界
- [Roadmap](docs/roadmap.md) - 当前桌面端路线
- [Release](docs/release.md) - 发布流程
- [Standalone build](docs/standalone-build.md) - 打包说明
