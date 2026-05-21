# Auctus Agent — Capability Gap Roadmap

对照三个主要竞品做的能力差距规划：

- [OpenClaw](https://openclaw.ai) — Peter Steinberger 做的本地 AI 助手（npm 安装，IM 控制）
- [Hermes Agent](https://hermes-agent.nousresearch.com) — Nous Research 做的服务器端自主 agent（MIT 开源）
- [Manus](https://manus.im) — Meta 收购的云端 SaaS agent（E2B Firecracker microVM 沙箱）

## 四方功能对照表

| 能力 | Auctus | OpenClaw | Hermes | Manus |
|------|--------|----------|--------|-------|
| **多端 IM 接入** | Telegram、Feishu、Lark、Email | WhatsApp、Telegram、Discord、Slack、Signal、iMessage | Telegram、Discord、Slack、WhatsApp、Signal、Email、CLI | Slack、Email |
| **手机 App** | ✅ 独立 RN 应用 + QR 配对 | ❌（靠 IM 客户端） | ❌（靠 IM 客户端） | ✅ 官方 App |
| **桌面 GUI** | ✅ PyWebView 本地窗口 | ❌（CLI/IM 优先） | ❌（服务器优先） | ✅ 桌面端 |
| **浏览器自动化** | ✅ Playwright（v0.1.32+） | ✅ | ✅ | ✅ 内置 Browser Operator |
| **网页搜索** | ✅ DDG 默认 + Tavily/Brave 可选 | ✅ | ✅ | ✅ Wide Research |
| **沙箱** | ❌（直接跑在 host） | ❌（local 为主） | ✅ 5 种后端（Docker/SSH/Singularity/Modal） | ✅ Firecracker microVM（每会话一个）|
| **并行 sub-agent** | ❌ | ⚠️ 有限 | ✅ 隔离 terminal + Python RPC | ✅ Wide Research（通用型 subagent） |
| **持续记忆 / 自学** | ✅ evolve(skills/eval/GEPA) | ✅ 偏向用户偏好 | ✅ 自动生成 skill | ⚠️ 默认 ephemeral，靠 file system + knowledge 显式存储 |
| **NL 定时任务** | ✅ create_cron_job + 进程内 scheduler（v0.1.32+） | ⚠️ | ✅ 「每周一发周报」式 | ⚠️ 未明确 |
| **图片识别** | ✅ analyze_image | ✅ | ✅ | ✅ |
| **图片生成 / TTS** | ❌ | ⚠️ 未明说 | ✅ TTS + 图像生成 | ✅ 图像 + 音乐生成 |
| **Skill 插件 / 市场** | ⚠️ skill docs 内部循环 | ✅ 社区插件 | ✅ MIT 开源生态 | ⚠️ knowledge feature（不是 marketplace） |
| **付费托管 LLM 服务** | ✅ Auctus API | ❌ BYO key | ❌ BYO key | ✅ Manus 自有云 |
| **生产文档 / 报表** | ✅ markdown / spreadsheet / webpage | ❌ | ❌ | ✅ slide / website / desktop app 生成 |
| **Email 收发** | ✅ IMAP + SMTP | ✅（通过插件） | ✅ | ✅ Mail Manus |
| **部署模型** | 本地 standalone（无云依赖） | 本地 npm | 服务器自部署 | **仅云端**（manus.im） |
| **非开发者可用** | ✅ 双击启动，零 Python | ⚠️ npm 安装 | ❌ 服务器部署 | ✅ 浏览器访问 |
| **开源** | 闭源（私有 repo） | ✅ 开源 | ✅ MIT | ❌ 闭源 SaaS |

**Auctus 的独有优势（保护这条护城河）**：

- **本地优先** — 数据/文件全在用户机器，Manus 反例：必须上云
- **专属手机 App** — Manus 也有 App 但它是访问云端，Auctus 是真正的"手机控制本机"
- **中文/亚洲生态** — Feishu / Lark，其它三家都没有
- **托管 LLM API（Auctus API）** — Manus 也有但走的是 SaaS，Auctus 给的是"既能本地跑也能买托管"双轨

**Manus 反过来比 Auctus 强在哪**（认清差距）：

- **生成能力** — slide / website / desktop app / 图片 / 音乐 直接产出，Auctus 只能 markdown/spreadsheet/webpage
- **沙箱执行** — Firecracker microVM 每会话隔离，安全性 Auctus 完全没法比（这是本文档 §1 要补的）
- **多 agent 并行** — Wide Research 是 Manus 的招牌功能（这是 §3 要补的）

## 优先级总览

| # | 能力 | 状态 | 备注 |
|---|------|------|------|
| 1 | 浏览器自动化 | ✅ Done (v0.1.32+) | `app/tools_browser.py`，Playwright |
| 2 | 网页搜索 | ✅ Done (v0.1.32+) | `app/search_providers.py`，DDG + Tavily/Brave |
| 4 | 自然语言定时任务 | ✅ Done (v0.1.32+) | `app/cronjobs.py` 进程内 scheduler |
| 3 | Docker 沙箱 | 📋 Planned | 本文档 §1 |
| 5 | WhatsApp / Discord 接入 | 📋 Planned | 本文档 §2 |
| 6 | 并行 sub-agent | 📋 Planned | 本文档 §3 |
| 7 | 图片生成 | 📋 Planned | 本文档 §4 |
| 8 | 文本转语音 (TTS) | 📋 Planned | 本文档 §5 |
| 9 | Skill 插件市场 | 📋 Planned | 本文档 §6 |

---

## §1. Docker 沙箱（执行隔离）

### 目标

`tools.py` 里的 `run_terminal_command` 现在直接在 host 上跑，权限只有路径白名单和命令黑名单拦截。给企业客户或新手用户的话风险高。让用户可以**在设置里二选一**：`local`（现状）或 `docker`（隔离）。

### 推荐方案

**用 `docker-py` SDK 包一层**，不引入 Hermes 那种 5 后端的复杂度。

- 镜像：自定义一个 `auctus/sandbox:latest`，基于 `python:3.11-slim` + `bash` + `git` + 常用 CLI
- 工作目录 mount：`inputs/`、`outputs/`、`data/` 三个目录绑定到容器，host 其他路径不可见
- 网络：默认 `--network=none`，需要联网的工具（浏览器、搜索）走另一个 profile
- 生命周期：每个会话一个长 live 容器，会话结束自动清理；不是「每条命令起一个容器」（启停太慢）

### 实现思路

- 新建 `app/sandbox/` 子包：
  - `app/sandbox/__init__.py` — 工厂方法 `get_sandbox(kind)` 返回 `LocalSandbox` 或 `DockerSandbox`
  - `app/sandbox/base.py` — `Sandbox` 抽象（`exec(cmd, cwd, timeout) -> Result`、`copy_in/out`、`close()`）
  - `app/sandbox/local.py` — 现有 subprocess 逻辑搬过来
  - `app/sandbox/docker.py` — 新代码
- 改 `app/tools.py`：`run_terminal_command` 不再直接 subprocess，改成 `sandbox.exec(...)`
- 改 `app/config.py`：新增 `sandbox_backend: str = "local"`（`local | docker`）
- 改 `app/ui.html`：设置页加单选框
- 新增 `docker/sandbox.Dockerfile` + 构建脚本

### 依赖

- `docker` Python SDK（`pip install docker`）
- 用户机需装 Docker Desktop / Colima — **加 doctor 检测**：用户选了 `docker` 但没装的话，给清晰报错

### 工作量估计

3–5 个工作日。难点不在代码，在 **打包 PyInstaller 还要装 docker SDK** + **Mac 用户对 Docker Desktop 4 GB RAM 占用的抗拒**。

### 风险 / 注意

- Docker Desktop 在 Mac 上吃资源，普通用户可能不愿装。**先不强制**，给个 toggle，默认 `local`。
- 文件系统差异：Docker for Mac 的 mount 慢，大文件处理可能比 local 慢 3-5 倍。
- Windows 上 Docker 依赖 WSL2，配置复杂度高。先只做 Mac，Windows v2 再说。

---

## §2. WhatsApp / Discord 接入

### 目标

现有 Telegram + Feishu + Lark + Email 已经覆盖国内场景，缺海外的 **WhatsApp** 和 **Discord**。OpenClaw 和 Hermes 两边都支持，海外用户认知里这俩等于「能用的 agent」。

### 推荐方案

**Discord 先做，WhatsApp 推迟。** 理由：

- Discord 官方 Bot API 免费、稳定、Python 生态成熟（`discord.py`），跟现有 `telegram_bot.py` 架构对得上
- WhatsApp 官方 Business API 要审核 + 收费，第三方网关（Twilio、Meta Cloud API）有合规风险，**个人开发者难拿**。先观察客户呼声

### 实现思路（Discord）

- 新建 `app/discord_bot.py`，参考现有 `app/telegram_bot.py` 结构：
  - 启动一个 `discord.Client`（intents 配 `messages` + `message_content`）
  - 监听私聊和 @ mention
  - 复用现有 `agent.run_task()` 逻辑
- 配置：`DISCORD_BOT_TOKEN` 加入 `.env.example`
- UI：设置页加 Discord 一项，跟 Telegram 一样的「输入 Token → 检测连接」流程
- 启动管理：`app/server.py` 的 `_lifespan` 里按 token 是否存在条件启动

### 依赖

- `discord.py` (`pip install discord.py`)
- PyInstaller hiddenimports 加 `discord` 系列

### 工作量估计

1.5–2 个工作日。基本是把 Telegram 那套抄一份改 SDK 调用。

### WhatsApp 未来路径（仅记录）

- **方案 A**：Meta Cloud API — 官方，需要企业认证 + Phone Number ID + Webhook 服务器。对个人不友好。
- **方案 B**：`whatsapp-web.js` (Node) 或 `pywhatkit` — 走 WhatsApp Web 协议。**违反 ToS**，可能封号。
- **方案 C**：Twilio WhatsApp — 中转，收费但合规。

结论：要做就走 A，等有付费客户主动要再做。

---

## §3. 并行 sub-agent

### 目标

复杂任务（"同时查 5 家公司财报"、"批量处理 30 份简历"）现在只能串行。Hermes 用 isolated terminal + Python RPC 跑 subagent。Auctus 应该支持。

### 推荐方案

**用 `asyncio.gather` + 进程池起 subagent**，每个 sub-agent 是一个独立的 `agent.run_task()` 调用，共享同一份 settings 但独立的对话历史和工具调用日志。

不要做成完整的 multi-agent 框架（CrewAI、AutoGen 那种），太重。**只暴露一个 `spawn_subagent(task, max_tokens)` 工具**给主 agent 调用。

### 实现思路

- 新建 `app/subagent.py`：
  - `async def run_subagent(task: str, parent_context: dict) -> dict` — 起一个轻量 agent loop
  - 限制：sub-agent **不能再 spawn**（防递归爆炸），不能调 `run_terminal_command`（防破坏），不能访问主会话历史
  - 用独立 SQLite connection、独立日志文件 `logs/subagent_<uuid>.jsonl`
- 改 `app/tools.py`：注册新工具 `spawn_subagent(task, expected_output)`
- 改 `app/agent.py`：当主 agent 调 `spawn_subagent` 时走异步分支
- UI：在对话视图里显示 sub-agent 节点（缩进 / 折叠展示）

### 依赖

无新依赖，全用 stdlib + 现有架构。

### 工作量估计

4–6 个工作日。核心难点是 **UI 显示 sub-agent 树** 和 **token 限额 / 超时回收**。

### 风险

- Token 成本爆炸：主 agent 一句话起 10 个 sub-agent，每个再用 5K token = 50K。**必须在 `accounting.py` 加 per-task 限额**。
- 用户混淆：sub-agent 输出回到主 agent 时如何展示，要设计好。

---

## §4. 图片生成

### 目标

让用户能在对话里说 "画一张猫的图"、"生成一个 OG 图片用 'Auctus Agent' 作标题"，agent 调图片生成 API 输出到 `outputs/`。

### 推荐方案

**用 OpenAI Images API (gpt-image-1) 或 Replicate 接 SDXL**。

- 国内用户用 **DeepSeek 体系没有图像生成**，只能走 OpenAI / Anthropic / Replicate / Stability
- 走 LiteLLM 已支持的渠道，跟现有 `llm.py` 路由对接

### 实现思路

- 新建 `app/tools_image.py`（或并到 `tools.py`）：
  - `generate_image(prompt: str, size: str = "1024x1024", style: str = "natural") -> dict`
  - 输出文件路径 + 元数据
  - 失败时降级到「显示 prompt + 告知用户没配 key」
- 改 `app/config.py`：新增 `image_provider: str = "openai"`、`image_api_key: Optional[str]`
- UI：设置页加图片模型配置
- 系统提示：告诉 agent 什么时候调（用户明确要画 / 生成 / 出图）

### 依赖

无新依赖，直接 `httpx` 调 API。

### 工作量估计

1–2 个工作日。

### 风险

- 成本：DALL·E 3 每张 $0.04，SDXL 通过 Replicate 每张 $0.003。**默认走便宜的**，UI 让用户选。
- 内容安全：所有上游都有内容过滤，agent 收到 429/400 时要友好降级。

---

## §5. 文本转语音 (TTS)

### 目标

移动端最有用：「今天的日程语音播报」、「把这篇文章读给我听」。

### 推荐方案

**OpenAI TTS API** (`tts-1` 模型) 起步，未来可加本地 `coqui-tts` 或 `piper`。

- 输出 mp3 到 `outputs/`，移动端用现有 RN audio 播放
- 桌面端在浏览器里用 `<audio>` 标签

### 实现思路

- 新建 `app/tools_tts.py`：
  - `text_to_speech(text: str, voice: str = "alloy") -> dict` — 返回文件路径 + duration
- 改 `app/server.py`：新增 `/api/tts/stream` 流式端点（可选，TTS API 支持流）
- 改 mobile `auctus-agent-mobile/app/src/ChatScreen.tsx`：消息卡片右侧加「🔊」按钮
- 改 `app/config.py`：`tts_provider`、`tts_voice`

### 依赖

直接 `httpx` 调 OpenAI API。

### 工作量估计

桌面端 1 天；mobile 端 1–2 天（要处理 audio session、后台播放权限）。

### 风险

- OpenAI TTS 每 1M 字 $15，长文章可能超预期。**加 per-message 字数限制**（默认 2000 字）。
- 移动端在 iOS Safari 里 `<audio>` 自动播放被禁，必须用户手动点。

---

## §6. Skill 插件市场

### 目标

现在 `evolve/skills.py` 是闭环自学：agent 自己提炼 skill doc 注入 prompt。如果开放成**社区可贡献的 skill 库**（类似 Claude Code 的 skills、OpenClaw 的 plugin），用户能装别人写的能力（"翻译为简体中文风格"、"按公司财报标准做摘要"），生态会爆发。

### 推荐方案

**两阶段做：**

**阶段 A（先做）**：本地 skill 包格式 + 导入命令
- Skill = 一个 `.zip` 或 git repo，包含：
  - `skill.yaml`（元数据：name, description, version, author）
  - `prompt.md`（注入到 system prompt 的内容）
  - 可选 `tools/*.py`（额外 Python 工具，沙箱执行）
- CLI / UI 装包：`agent skill install <url-or-path>` / 设置页"导入 skill"
- 装在 `data/skills/<name>/`，runtime 加载

**阶段 B（远）**：中央 registry
- `skills.auctus.ai` 网站，浏览 / 评分 / 下载
- 一键安装 from registry

### 实现思路（阶段 A）

- 新建 `app/skills_manager.py`（注意：跟 `evolve/skills.py` 区分清楚，后者是**自动学习的 skill**，这个是**用户/社区显式安装的 skill**）
  - `install_skill(source)` — 支持 zip 路径、git url、registry slug
  - `list_skills()` / `enable_skill(name)` / `disable_skill(name)` / `uninstall_skill(name)`
  - 启动时把所有 enabled skill 的 `prompt.md` 注入 system prompt
- 改 `app/agent.py`：构造 system prompt 时合并 enabled skills
- UI：新增 Skills 标签页（列出已装、可启用/禁用、卸载）
- 安全：执行 skill 自带的 Python 代码必须**走沙箱**（依赖 §1 Docker 沙箱）

### 依赖

`pyyaml` (装 yaml 解析)、`gitpython`（如果支持 git url）

### 工作量估计

阶段 A：5–7 个工作日。阶段 B（registry 网站）：另起一个项目。

### 风险

- **跟 `evolve/skills.py` 命名冲突** — 必须想清楚两者关系。建议把 evolve 那个改名 `auto_skills` 或 `learned_skills`，社区那个叫 `skills`。
- 安全模型：第三方 skill 的代码执行权限怎么定？建议**默认只允许 prompt 注入，要装代码 skill 必须用户手动确认**。
- 命名空间冲突：两个 skill 都叫 "translate-zh" 怎么办？要有 `author/name` 形式。

---

## 实施建议

按 ROI 排序：

1. **§2 Discord**（1.5–2 天）— 投入小，海外用户立刻有感
2. **§4 图片生成**（1–2 天）— 投入小，是「显得 agent 能力强」的标配
3. **§5 TTS**（2–3 天）— 移动端差异化点
4. **§3 并行 sub-agent**（4–6 天）— 复杂任务能力跃迁，但用户感知没前 3 项直接
5. **§1 Docker 沙箱**（3–5 天）— 企业客户场景必要，但当前用户大多在意"能用"而不是"安全"
6. **§6 Skill 市场**（5–7 天 + 长期）— 战略性，做好了是护城河，但要先有用户基础

**不建议齐头并进**，按上面顺序串行做，每个落地一版就发布，让用户给反馈再做下一个。

---

_Last updated: 2026-05-18_
