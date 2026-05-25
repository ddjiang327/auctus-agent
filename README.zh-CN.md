# Auctus Agent ◈

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL%20v3-green?style=for-the-badge" alt="License: AGPL-3.0"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"></a>
  <a href="docs/roadmap.md"><img src="https://img.shields.io/badge/Status-Pre--1.0-orange?style=for-the-badge" alt="Pre-1.0"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="English"></a>
</p>

**本地优先（local-first）的个人 AI Agent —— 跑在你自己的电脑上，用你的文件、记你需要的上下文、调你授权过的本地工具。**

Auctus Agent 是常驻在桌面的个人助手。你用自然语言下达任务，它读取你的文件、写报告、跑你授权的命令、用浏览器抓数据、收发邮件、并记住有用的东西。所有事情都发生在你的机器上 —— 你的文件、你的模型 API key、你的对话历史，没有一处需要上云。

它跟传统聊天 SaaS 是反方向：不用注册、不用云账号、不绑定厂商。配一个你自己的模型 key（LiteLLM 支持的全部）就能用。

<table>
<tr><td><b>真本地</b></td><td>一个 FastAPI 进程跑在 <code>127.0.0.1</code>。记忆走 SQLite，其他都是文件。删文件夹就能彻底清零 —— 没有你管不到的云端状态。</td></tr>
<tr><td><b>工具使用循环</b></td><td>50+ 工具：文件读写、终端 &amp; 长生命周期终端会话、浏览器自动化（Playwright）、网页搜索、图片生成、TTS、视觉、报告 / Excel / 网页生成、Email、定时任务。</td></tr>
<tr><td><b>逐任务权限关</b></td><td>文件访问默认限定 workspace 目录。终端 &amp; 日历需要明确授权。<code>rm</code> 默认被拦截改成"移到废纸篓"，除非你说"永久删除"。</td></tr>
<tr><td><b>闭环自学</b></td><td>自动从你的对话里提炼可复用 Skill（<code>evolve/</code> 子系统）。内置评估集生成 + GEPA 风格的优化循环。</td></tr>
<tr><td><b>选你想用的模型</b></td><td>LiteLLM 路由 —— DeepSeek、OpenAI、Anthropic、DashScope、Ollama，或你自己的 OpenAI 兼容网关。改一个环境变量就切，不动代码。</td></tr>
<tr><td><b>手机上也能用</b></td><td>Telegram、Discord、飞书、Lark、Email（IMAP+SMTP）。也可以通过你自己部署的 relay 服务器配对手机端。</td></tr>
<tr><td><b>自然语言定时任务</b></td><td>"每周一早上 9 点发我昨天邮件的摘要。" 进程内调度，不需要系统 crontab，在 standalone bundle 里也能用。</td></tr>
<tr><td><b>可打包成独立可执行</b></td><td>已带 PyInstaller spec —— 全套打成一个 Mac <code>.command</code> / Windows <code>.bat</code> 双击启动包，终端用户不需要装 Python。</td></tr>
</table>

---

## 快速安装

### Mac

```bash
git clone https://github.com/ddjiang327/auctus-agent.git
cd auctus-agent
bash scripts/install_mac.sh        # 创建 .venv 并装依赖
cp .env.example .env               # 编辑加入你的模型 key
bash scripts/start_mac.sh
```

### Windows

```powershell
git clone https://github.com/ddjiang327/auctus-agent.git
cd auctus-agent
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
copy .env.example .env
powershell -ExecutionPolicy Bypass -File .\scripts\start_windows.ps1
```

或者直接双击 `Setup.command`（Mac）/ `Setup.bat`（Windows）。第一次会进入浏览器内首次设置向导帮你选模型和填 key。看到 "Loading… ready!" 后访问 <http://127.0.0.1:8000>。

完整安装说明见 [INSTALL.md](INSTALL.md)。

## 配置

全部走 `.env`。完整字段含注释见 [`.env.example`](.env.example)。最少需要：

```bash
MODEL=deepseek/deepseek-chat
DEEPSEEK_API_KEY=your_key_here
```

或者用 UI 里的"设置"面板 —— 效果一样，写回 `.env`。

## 模型 Provider

只要 LiteLLM 支持的都能用。常见：

| Provider       | 示例 `MODEL`                 | 环境变量              |
|----------------|------------------------------|-----------------------|
| DeepSeek       | `deepseek/deepseek-chat`     | `DEEPSEEK_API_KEY`    |
| OpenAI         | `gpt-4o-mini`                | `OPENAI_API_KEY`      |
| Anthropic      | `claude-sonnet-4-5`          | `ANTHROPIC_API_KEY`   |
| DashScope（通义） | `dashscope/qwen-plus`     | `DASHSCOPE_API_KEY`   |
| Ollama（本地） | `ollama/llama3:8b`           | （无需，需先跑 `ollama serve`）|

也可以走你自己的 OpenAI 兼容网关：设 `LLM_ROUTE=byo` + `PROXY_BASE_URL` + `PROXY_API_KEY`。

## 文档

| 章节 | 内容 |
|------|------|
| [INSTALL.md](INSTALL.md) | 完整安装 + 首次设置向导 |
| [docs/roadmap.md](docs/roadmap.md) | 已发布功能、规划中、明确不做 |
| [docs/agent-capability-gaps.md](docs/agent-capability-gaps.md) | 跟 OpenClaw / Hermes / Manus 的诚实对比 |
| [docs/closed-learning-loop.md](docs/closed-learning-loop.md) | 自动提取 Skill / GEPA 循环怎么工作 |
| [docs/standalone-build.md](docs/standalone-build.md) | 打 PyInstaller 独立分发包 |
| [docs/release.md](docs/release.md) | 版本规范、tag、发布流程 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 开发环境、PR 规范、贡献入门 |
| [SECURITY.md](SECURITY.md) | 漏洞上报、威胁模型 |

## 文件和终端权限

Auctus Agent 不给"全开"。默认：

- **文件访问** 限定在 `WORKSPACE_DIR`（默认 `./inputs/`）。访问其他路径会触发对话内权限请求 *"允许一次 / 始终允许 / 拒绝"*。
- **终端命令** 必须显式授权（按单条 / 始终）。
- **日历 / 提醒事项写入** 也走同样权限流，或者降级生成 `.ics` 文件让你导入。

授权（选"始终允许"时）会持久化到 `data/` 里的 SQLite。设置面板可随时撤销。

危险命令会被拦截：`rm` 默认改成"移到废纸篓"，只有你明确说"永久删除"才真删。

## 本地数据与隐私

全部在硬盘上，全是 SQLite / JSONL / 普通文件：

| 路径        | 内容 |
|-------------|------|
| `data/`     | SQLite（对话历史、学习候选、设置状态）、Chroma 向量、定时任务 |
| `inputs/`   | 你给 agent 的文件（UI 上传或 copy 进来） |
| `outputs/`  | agent 生成的文件（报告、截图、图片、音频） |
| `logs/`     | 工具调用审计（`tool_calls.jsonl`）、cron 执行日志 |
| `.env`      | 你的 API key 和配置 |

删上面这些文件夹就能彻底清零。除了你配置的模型 provider，没有任何数据离开过你的机器。

## 这个 repo 不包含什么

只是**本地 agent 核心**。以下不在这个 repo 里：

- **手机端 App** —— relay pairing API 在，但 mobile client 源码不在。可以按文档自己实现一个，或者不用手机端。
- **托管云服务** —— 没有公开的 Auctus 云后端。agent 直接调你选的模型 API。想做托管商业版的话欢迎 fork。
- **账户 / 计费 / 订阅系统** —— 没有。完全靠你选的模型 provider 自己的额度。
- **预编译二进制** —— [`docs/standalone-build.md`](docs/standalone-build.md) 说明怎么自己打。

## 贡献

欢迎贡献。环境搭建、PR 规范、从哪入手见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 社区

- 🐛 [Issues](https://github.com/ddjiang327/auctus-agent/issues) —— bug、feature request
- 💡 [Discussions](https://github.com/ddjiang327/auctus-agent/discussions) —— 设计讨论、配方、show-and-tell
- 🔒 [SECURITY.md](SECURITY.md) —— 安全问题请走私有渠道

## License

AGPL-3.0 —— 详见 [LICENSE](LICENSE)。如果你以网络服务方式跑 Auctus Agent 的修改版本，须向用户提供源码。

AGPL 不适合你的场景？开 issue 讨论 —— 我们对特定贡献可能愿意双许可。
