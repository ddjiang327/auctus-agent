# Auctus Agent — Roadmap

## 第一阶段：本地自用版

### Phase 0：CLI Demo ✅ 已完成

- [x] 建立 Python 项目结构（`secretary/`）
- [x] 接入模型 API（LiteLLM，支持 Claude / OpenAI / DeepSeek / 本地模型）
- [x] 实现 `read_file`（读 txt / md / pdf / csv / xlsx / docx，限制在 inputs/ 目录）
- [x] 实现 `summarize_text`（bullet / executive / qa 三种风格）
- [x] 实现 `make_markdown_report`（生成 .md 报告文件）
- [x] 实现 `make_spreadsheet`（生成 .xlsx 表格）
- [x] 实现 `make_webpage`（生成 HTML 报告页）
- [x] 实现 `make_react_prototype`（生成 React + Tailwind 单页原型，CDN 版，无需构建）
- [x] 实现 `extract_memory_candidates`（提取候选记忆，不自动保存）
- [x] 实现 `remember` / `recall`（SQLite + Chroma 三层记忆）
- [x] 工具调用日志自动写入 `logs/tool_calls.jsonl`
- [x] CLI 入口 `agent.py`（支持 `run <file> --task "..."` 和 `chat` 两个子命令）
- [x] 建立 `inputs/`（文件 workspace）和 `logs/` 目录
- [x] 更新系统提示词，新增所有工具说明

**验收标准：**
- [x] 输入一个 PRD 文件，能输出 summary.md、features.xlsx、test_cases.xlsx、prototype/
- [x] 输出文件路径清晰，用户能直接打开
- [x] 所有工具调用有基础日志

**测试中发现并修复的 Bug：**
- Python 3.9 兼容性：`str | None` → `Optional[str]`（6 个文件）
- SQLAlchemy DetachedInstanceError：`memory.py load_history()` session 生命周期修复
- LiteLLM 模型名：补 `deepseek/` provider 前缀

---

### Phase 1：工具安全、日志与错误恢复 ✅ 已完成

- [x] 每次工具调用记录完整字段：`task_id` / `time` / `user_input` / `tool_name` / `tool_input` / `tool_output` / `risk_level` / `model` / `token_usage` / `status` / `error`
- [x] 建立 risk_level：low / medium / high
- [x] 高风险工具默认禁用或要求二次确认（`remember` 需传 `confirmed: true`）
- [x] Tool-use 循环错误恢复（参数错误返回 LLM，LLM 可自动重试）
- [x] 工具参数校验（按 schema `required` 前置校验）
- [x] 单任务 `task_id` 串联所有 tool_call
- [x] 增加日志查看命令：`agent.py logs`

**验收标准：**
- [x] 任意任务都能追踪完整工具链
- [x] 工具失败时有明确错误原因
- [x] 高风险操作不会被模型直接执行

---

### Phase 1.5：邮件文本处理 ✅ 已完成

- [x] `summarize_email_text`：总结粘贴的邮件内容
- [x] `extract_email_tasks`：提取邮件中的待办事项
- [x] `draft_email_reply`：生成邮件回复草稿（tone 可选 professional / friendly / concise / apologetic）
- [x] 明确禁止自动发送、删除、归档邮件（schema 描述 + system prompt 双重限制，无发送类工具）

**验收标准：**
- [x] 粘贴一封邮件，能生成摘要、待办、回复草稿
- [x] 任何邮件发送动作都只生成草稿，不执行发送

---

### Phase 2：记忆系统补强 ✅ 已完成

- [x] 候选记忆确认机制（`extract_memory_candidates` 现在自动存为 confirmed=0，需 confirm/reject）
- [x] 记忆分类：preference / project / rule / temporary
- [x] 记忆字段标准化：`type` / `title` / `content` / `tags` / `source` / `importance` / `created_at` / `expires_at`
- [x] 记忆搜索（`recall` 语义搜索 + LIKE 降级）
- [x] 记忆删除（`forget_memory` 工具 + `agent.py memory forget <id>`）
- [x] Sleep-time 任务：`compress_history_to_facts` + `agent.py compress --session <id>`
- [x] 新增 LLM 工具：`list_memories` / `confirm_memory` / `forget_memory`
- [x] CLI 新增子命令：`agent.py memory list/confirm/reject/forget` + `agent.py compress`
- [x] `remember` 工具支持 `type` / `importance` 参数
- [x] 对话滚动摘要：支持“消息条数 or token 预算（默认 8000）”任一触发即压缩
- [x] 对话摘要分段累计：按 `covers_until_msg_id` 增量合并摘要，避免重复压缩同一段

**验收标准：**
- [x] 用户明确要求"记住"时能保存（`remember` 工具直接确认）
- [x] 普通对话只生成候选记忆（`extract_memory_candidates` 存为 confirmed=0）
- [x] 新会话能召回项目背景（`recall` 语义搜索）
- [x] 错误记忆可删除（`forget_memory` 工具 + CLI）

---

### Phase 3：本地 Web UI ✅ 基础版已完成

- [x] FastAPI 本地服务（`app/server.py`）
- [x] 聊天窗口
- [x] 文件上传 → inputs/
- [x] 输出文件下载
- [x] 工具调用日志查看
- [x] 候选记忆确认界面
- [x] 模型切换
- [x] UI 视觉升级：The Digital Companion + Minimalist Capsule（动态 Core + 玻璃胶囊输入条 + 圆角卡片）
- [x] 微交互：执行中“呼吸态”指示、完成提示音、可中断的停止按钮
- [x] 交互兜底：追问场景的快捷选项按钮 + 模糊确认词澄清（防止对话断档）
- [x] 文本输入体验：Enter 发送 / Shift+Enter 换行 + “换行”按钮
- [x] 权限弹窗统一：支持终端/日历/文件访问的 once/always/no 授权选择

**验收标准：**
- [x] Web UI 能完整跑通 CLI Demo 同样的任务
- [x] 用户能下载所有输出文件
- [x] 用户能查看工具调用链
- [x] 用户能确认或拒绝候选记忆

---

### Phase 4：手机联动（Telegram）✅ 基础版已完成

- [x] Telegram Bot 收发消息（`app/telegram_bot.py`）
- [x] Telegram 文件上传 → inputs/
- [x] 文件回传：Excel / HTML / Markdown
- [x] 手机触发任务 → 电脑执行 → Telegram 回传结果
- [x] 执行失败时返回明确错误

**验收标准：**
- [x] 手机发任务，电脑执行
- [x] 手机发文件，Agent 能处理
- [x] 结果文件能回传手机
- [x] 失败时不静默

备注：本地逻辑与导入测试已覆盖；真实 Telegram 端到端仍需配置 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USER_IDS` 后用实机验收。

---

### Phase 5：稳定性与本地可用性 ✅ 基础版已完成

- [x] 工作区隔离：每个 task 独立目录
- [x] 模型 token 用量统计
- [x] 错误处理和友好提示
- [x] 配置检查命令：`agent.py doctor`
- [x] 日志清理策略
- [x] Mac 本地启动脚本
- [x] Windows 路径兼容性检查

**验收标准：**
- [ ] 连续使用 7 天不丢数据
- [x] 每个任务产物互不污染
- [x] API key、workspace、模型配置错误能被 doctor 检测出来

备注：7 天连续使用需要真实使用周期验证；基础机制已补齐。
补充：已提供 `agent.py stability-check` 用于每日生成本地数据完整性快照，辅助 7 天验收。

---

## 第二阶段：商用增强版

### Phase 6：用户与 Usage 基础 ✅ 基础版已完成

- [x] `users` 表
- [x] `api_keys` 表
- [x] `usage` 表
- [x] 本地 user_id
- [x] 每次模型调用记录 token / cost

备注：已建立本地 SQLite accounting 基础；`usage` 会记录 chat completion 与 embedding 的 token，cost 优先使用 LiteLLM 返回的 `response_cost`，没有返回时记为 0。BYO key 和 route 切换留到 Phase 7。

---

### Phase 7：BYO Key ✅ 基础版已完成

- [x] proxy / byo / local 路由
- [x] 用户填写自己的 Anthropic / OpenAI / DeepSeek Key
- [x] BYO Key 本地加密保存
- [x] 切换 route 后调用走用户自己的 Key

备注：已支持 CLI 和 Web API 管理 route / BYO key。`proxy` 路由需要配置 `PROXY_BASE_URL`，云端 Relay 服务本体留到 Phase 8。

---

### Phase 8：Cloud Relay ✅ 基础版已完成

- [x] Auth Service
- [x] Quota Service
- [x] LLM Proxy
- [x] Telegram Webhook
- [x] Tunnel Hub
- [x] Desktop Agent 主动连接 Relay WebSocket

备注：已在本地 FastAPI 内实现 Relay API 骨架和 WebSocket tunnel 协议；真实云部署、持久队列和生产鉴权仍留给上线阶段继续增强。

---

### Phase 8.5：Mobile App + Desktop Agent Cloud Relay

目标：让用户下载手机 App 后，通过 Auctus API 账号直接联系自己家里/办公室电脑上的桌面 Agent，不需要公网 IP、端口转发、Telegram、飞书或 Lark。

用户路径：

1. 下载手机 App
2. 注册 / 登录 Auctus API
3. 下载电脑端 Agent
4. 电脑端显示二维码，手机扫码绑定账号
5. 手机 App 发送任务
6. 云端 Relay 转发给对应电脑 Agent
7. Agent 本地执行后把结果回传 App

核心实现步骤：

- [ ] Auctus API 增加生产级 Device Registry：`user_id -> device_id -> device_token`
- [ ] 桌面 Agent 增加二维码绑定流程：生成短期 `bind_code`，展示给手机扫码
- [ ] 手机 App 增加登录、扫码绑定、设备列表
- [ ] 桌面 Agent 使用 device token 主动连接云端 `wss://.../relay/tunnel/{device_id}`
- [ ] Relay Hub 维护在线设备映射：`user_id -> device_id -> websocket`
- [ ] 新增移动端任务 API：`POST /api/mobile/tasks`
- [ ] Relay 将移动端任务封装为 `agent_task` 推送给对应桌面 Agent
- [ ] 桌面 Agent 执行任务后回传 `agent_result`
- [ ] 手机 App 展示任务状态、结果和生成文件
- [ ] 增加任务状态机：queued / sent_to_device / running / needs_permission / completed / failed / timed_out
- [ ] 增加离线处理：电脑不在线时提示设备离线，任务可排队或拒绝
- [ ] 增加权限流：文件、终端、日历等敏感操作需要桌面策略或可信手机确认
- [ ] 增加文件回传：桌面上传输出文件到云端临时 URL，手机可预览/下载
- [ ] 增加推送通知：任务完成、需要权限、设备离线/重连
- [ ] 增加安全控制：短期绑定码、单设备 token、token 撤销、消息签名、任务限流
- [ ] 增加观测：任务日志、Tunnel 在线率、超时率、Relay 队列长度

验收标准：

- [ ] 新用户只需手机 App 登录 + 桌面扫码，即可完成一次远程任务
- [ ] 用户没有公网 IP 时仍可使用
- [ ] 手机只能给自己绑定的设备发任务
- [ ] 桌面 Agent 离线时，App 有明确状态提示
- [ ] 任务结果和生成文件能回到手机 App
- [ ] 敏感操作不会被云端默认放行

详细设计见：`docs/mobile-cloud-relay.md`

---

### Phase 9：配额与风控 ✅ 基础版已完成

- [x] 月度额度
- [x] 每分钟速率限制
- [x] Daily cost cap
- [x] 异常 spike 报警
- [x] 额度耗尽后引导 BYO Key

备注：Relay 已基于 usage 表提供月度 token、分钟 token、每日 cost 与 spike 检测；超限时 LLM Proxy 返回 429 并给出 BYO Key 引导。

---

### Phase 10：上线准备

- [x] 隐私政策
- [x] 用户协议
- [x] 安装教程
- [x] Landing page
- [x] 成本日报
- [x] Mac / Windows 安装脚本

---

### Phase 11：Self-Evolution Loop 自我进化

- [x] `learning_candidates` 表
- [x] `agent.py evolve scan/list/apply/reject`
- [x] 从 messages / tool logs 中提炼候选学习
- [x] 用户确认后写入 memory
- [x] workflow 自动匹配任务
- [x] prompt_rule 运行时注入
- [x] error pattern 学习并影响工具重试
- [x] sleep-time 自动进化
- [x] 学习项可禁用、回滚

备注：第一版采用“候选学习 → 用户确认 → 运行时生效 → 可停用/回滚”的安全闭环；不会让 Agent 自动改代码或自动改 system prompt。

**Closed Learning Loop 升级（已完成）：**
- [x] Skill 自动生成闭环：基于 traces 自动提炼可复用 Skill（Markdown），支持条件激活与渐进式披露（L0/L1）
- [x] Skill 索引与检索：token 匹配检索 top-k，按任务类型自动注入最小必要上下文，强匹配升级 L1
- [x] 安全闸与回滚：所有 Skill 默认 pending，需 skill-apply 确认后生效，可 skill-disable / skill-rollback
- [ ] GEPA / DSPy 优化管道：基于真实任务评估集生成变体、自动评测、选优并产出 PR（人工审查）

---

### Phase 12：傻瓜式安装与首次设置向导

目标用户：完全不懂命令行、Python、API key、环境变量的普通用户。下载后应只看到清晰入口，能双击安装、按提示配置，并完成第一次可用启动。

- [x] 根目录提供可双击的 `Setup` 入口（`Setup.command` / `Setup.bat`）
- [x] Windows 提供 `.bat` 安装入口（并提供 `Start.bat`）
- [x] Mac 提供 `.command` 安装入口（并提供 `Start.command`）
- [x] 安装向导自动检查 Python 版本（3.10+）/ 运行环境基础可用性
- [x] 安装向导自动创建虚拟环境并安装依赖
- [x] 安装向导自动创建 `.env`（从 `.env.example` 生成）
- [x] 首次点击进入后显示首次启动向导，而不是直接进入空白聊天页
- [x] 首次启动向导：选择模型接入方式（使用自己的 API key / 使用 Auctus 托管 API 额度 / 本地模型）
- [x] 首次启动向导：用户选择自己的 API key 时，选择模型服务商（DeepSeek / OpenAI / Anthropic / 本地模型）
- [x] 首次启动向导：用户选择自己的 API key 时，粘贴并验证 API key
- [x] 首次启动向导：用户选择 Auctus 托管 API 时，引导登录账号并展示当前额度、充值入口和计费说明
- [x] 首次启动向导：选择本地网页、Telegram、局域网访问等使用方式
- [x] 首次设置向导：用户可以给 Agent 起一个名字
- [x] 首次设置向导：用户可以选择是否做一个简短自我介绍
- [x] 首次设置向导：用户同意后，把 Agent 名字、用户自我介绍、偏好信息写入第一批候选记忆
- [x] 首次设置向导：用户可以选择 AI 性格（专业简洁 / 高冷御姐 / 知心大叔 / 可靠小哥 / 元气萌妹）
- [x] 首次设置向导与 Settings 支持系统语言选择（中文 / English）
- [x] 英文模式下 Web UI、设置面板、首次设置向导、状态提示和常见系统错误显示英文
- [x] Settings 抽屉收纳模型、路由、API key、文件权限、文件上传、候选记忆和工具日志，聊天界面保持干净
- [x] 文件权限支持可视化文件夹选择器，默认从桌面开始选择
- [x] 文件权限支持整机模式：用户明确启用后，Agent 可读取/写入电脑任意路径内支持的文本文件
- [x] 终端命令权限：支持 once/always/no 授权弹窗 + 设置开关（当前默认开启，可按产品策略再调整）
- [x] Agent 支持读取公开网页 URL 并提取正文文本，用于总结网页或基于网页内容分析
- [x] 聊天发送后显示“已收到，正在处理”的加载动效，并自动滚动到最新消息
- [x] 权限体系补强：日历访问权限（once/always/no）+ 文件访问权限（workspace vs full computer 的一次性/永久授权）
- [x] Email 管理配置向导：接入 Gmail / Outlook / IMAP 的应用专用密码（IMAP 方式）
  - [x] 账号连接状态页：已连接账号列表、测试连接按钮、删除账户
  - [x] Gmail / Outlook / QQ / 163 / iCloud / Yahoo：IMAP 预设服务器配置，一键填充
  - [x] IMAP：应用专用密码保存（本地加密），支持手动指定服务器
- [x] Email 管理工具：读取收件箱、搜索邮件、读取邮件正文
  - [x] 只读能力：`list_inbox`（列出最新邮件）、`search_emails`（按关键词搜索）、`get_email_thread`（读取完整正文）
  - [x] 与现有文本工具联动：读取正文后可调用 summarize_email_text / extract_email_tasks / draft_email_reply
- [x] Email 写入风控：只读，不提供发送/删除/归档工具
  - [x] 默认只生成草稿（draft_email_reply），不执行发送
  - [x] schema + system prompt 双重禁止自动发邮件（Phase 1.5 已实现）
- [x] Telegram 配置向导：说明如何通过 BotFather 创建 bot
- [x] Telegram 配置向导：粘贴 `TELEGRAM_BOT_TOKEN` 后自动验证
- [x] Telegram 配置向导：自动识别或引导填写 `TELEGRAM_ALLOWED_USER_IDS`（getUpdates 自动获取）
- [x] Telegram 配置完成后发送测试消息，确认手机端可用
- [x] 安装完成后自动创建桌面启动入口（Mac: 启动 Auctus Agent.command / Win: .bat）
- [x] 安装完成后自动打开本地 Web UI（Setup.command / Setup.bat 已包含 open 命令）
- [x] 安装失败时显示普通用户能理解的错误说明和下一步操作（install scripts 错误分类提示）
- [x] 支持重新运行 Setup 修改配置（Web UI 已有"重新设置"按钮重入向导）

**验收标准：**
- [ ] 用户不打开终端也能完成安装
- [ ] 用户不手动编辑 `.env` 也能完成配置
- [ ] 用户能在 10 分钟内完成首次启动并发出第一条任务
- [ ] 用户第一次进入时能明确选择“自己的 API”或“Auctus 托管 API”
- [ ] 选择 Auctus 托管 API 的用户必须先登录，且能看到额度、充值和消耗记录入口
- [ ] 用户可选择是否命名 Agent、是否做自我介绍；同意保存的信息必须进入候选记忆确认流
- [ ] Telegram 从创建 bot 到手机测试消息全流程有引导
- [ ] 安装失败能明确告诉用户卡在哪一步

备注：当前已有 Mac / Windows 脚本和 `.env.example`，但仍偏技术用户；Phase 12 要把它升级成普通用户可理解的安装产品体验。

---

### Phase 12.5：本地自动化（Cron Jobs）

- [x] 新增 cron job 管理：生成可执行脚本、保存 job 元数据、导出/写入系统 crontab
- [x] CLI：`python agent.py cron list/add-backup/add-run/export/apply/enable/disable/remove`
- [x] 内置模板：每日备份文件夹、每周定时运行 `agent.py run` 生成报表
- [x] Windows 任务计划程序（Task Scheduler）适配与引导（替代 cron）
  - 新增 `export-windows`：生成 PowerShell 脚本，可手动导入任务计划
  - 新增 `apply-windows`：直接写入 Windows 任务计划
  - 新增 `list-windows`：查看已创建的 Windows 任务
  - cron 表达式自动转换为 Windows Schedule 格式（DAILY/WEEKLY/MONTHLY）
  - 文档更新：scripts/cron/README.md 包含 Windows 使用说明

### Phase 13：Auctus 托管 API 与充值计费网站

目标：做一个独立的 API 计费网站。普通用户不用理解大模型 API key，也能登录、充值、使用 Auctus 托管 API 额度；高级用户仍可选择 BYO Key。

> ⚠️ **注意**：Phase 13 已拆到独立 GitHub 项目 `auctus-api/` 中开发，不再放在 `auctus-agent` 项目内。

**核心功能已完成（auctus-api/）：**
- [x] 项目结构搭建（FastAPI + SQLAlchemy + SQLite）
- [x] 用户认证：注册、登录、JWT 令牌
- [x] API Key 管理：创建、撤销
- [x] 计费系统：余额管理、交易记录
- [x] Relay 代理：OpenAI 兼容接口，自动计费
- [x] 管理后台 API：用户列表、统计
- [x] 前端 UI：用户后台管理界面
- [x] 混合计费模式：订阅优先 + 余额兜底
- [x] 套餐订阅系统：8 个套餐（Global + CN），购买/续费/过期
- [x] 额度保护：每日/每月成本上限、异常 spike 检测
- [x] BYO Key 分流：自带密钥不扣余额，仅记录用量
- [x] 找回密码：忘记密码、验证 token、重置密码、修改密码
- [x] Auctus Agent 集成 API：设备绑定、状态查询、用量上报
- [x] 全流程联调测试通过（16/16 项）

**待完成：**
- [ ] 真实支付集成（Stripe/Alipay/WeChat Pay）
- [x] 设备绑定（前端 UI 完善）
- [ ] 双区域部署（Vercel / 阿里云）

- [x] Web 端账号系统：注册 / 登录 / 找回密码 / 设备绑定（后端 API 已完成）
- [x] 用户后台：显示余额、额度、调用次数、token 消耗、预计成本
- [x] 充值系统：套餐、订单、支付状态、充值流水（模拟支付已完成）
- [x] 计费规则：按模型、token、请求类型计算扣费，保留平台毛利和赠送额度配置
- [x] 托管 API Key：服务端安全保存平台模型 key，不下发给客户端
- [x] Relay 鉴权：桌面 Agent 使用登录 token 调用 Auctus 托管 API
- [x] 额度保护：余额不足、免费额度耗尽、异常 spike、每日成本上限时自动阻断
- [x] BYO Key 分流：用户填自己的 API key 时不扣 Auctus 余额，只记录本地用量
- [x] 账单明细：用户能看到每次调用的时间、模型、token、扣费、任务来源
- [x] 管理后台：用户列表、充值记录、用量统计、异常账号冻结、成本日报
- [ ] 托管 API 双区域部署：海外用户走 Vercel，国内用户走阿里云
- [x] 区域自动检测入口：安装/首次打开时根据浏览器时区和语言自动推荐国内阿里云或海外 Vercel
- [ ] 区域路由策略：根据用户选择、网络可达性或账号地区选择 `global/vercel` 或 `cn/aliyun` Relay
- [ ] 国内阿里云节点：部署 Auth、Quota、LLM Proxy、账单查询和充值回调服务，优先接入国内可用模型与支付方式
- [ ] 海外 Vercel 节点：部署 Auth、Quota、LLM Proxy、账单查询和充值回调服务，优先接入海外模型与支付方式
- [ ] 双区域统一鉴权：桌面 Agent 登录后获得区域化 token，token 能明确绑定用户、设备、区域和额度账户
- [ ] 双区域计费一致性：Vercel 与阿里云都按同一套模型价格表、赠送额度、余额扣减和风控规则执行
- [ ] 双区域数据同步：用户账号、余额、充值流水、用量明细和风控状态需要有主数据源与同步策略
- [ ] 区域故障切换：当前区域不可用时，提示用户切换区域；涉及数据与合规限制时不自动跨区发送内容
- [x] Settings / 首次设置：选择 Auctus 托管 API 时允许用户选择服务区域（自动 / 海外 Vercel / 国内阿里云）

**验收标准：**
- [ ] 新用户登录后可充值，并用 Auctus 托管 API 完成一次 Agent 对话
- [ ] 每次托管 API 调用都能按 user_id 记录 token、成本、扣费和余额变化
- [ ] 余额不足时给出充值入口，不再继续消耗平台模型 key
- [ ] BYO Key 用户不会被重复扣费
- [ ] 海外用户通过 Vercel 节点完成登录、充值、扣费和一次 Agent 对话
- [ ] 国内用户通过阿里云节点完成登录、充值、扣费和一次 Agent 对话
- [ ] 两个区域的余额、用量和账单明细在用户后台里口径一致
- [ ] 区域不可用时给出清楚的切换或稍后重试提示，不静默失败

备注：这是 Phase 6-9 的产品化版本。当前本地 accounting / route / quota / relay 已有基础骨架，Phase 13 要补齐真实登录、支付、账单、生产级风控，以及海外 Vercel / 国内阿里云双区域托管 API 架构。

---

### Phase 13.5：GitHub 拆仓、版本与 0.1.0 内测发布 ✅ 已完成

目标：把本地 Agent 和云端 API 拆成两个可独立发布、独立部署、独立打 tag 的 GitHub 仓库，并建立最小 release 流程。

- [x] 本地 Agent 仓库拆分为 `auctus-agent`
- [x] 托管 API / 计费网站仓库拆分为 `auctus-api`
- [x] `auctus-agent` 增加统一版本号：`app/version.py`
- [x] `auctus-agent` 增加版本接口：`GET /api/version`
- [x] `auctus-api` 版本号调整为 `0.1.0`
- [x] `auctus-api` 增加版本与 Agent 更新信息接口：`GET /version`
- [x] 两个项目都补 `.gitignore`，排除 `.env`、本地数据库、日志、输出、虚拟环境和用户文件
- [x] `auctus-api` 补 `.env.example`
- [x] 两个项目都补发布规则文档：`docs/release.md`
- [x] `auctus-agent` 推送到 GitHub：`ddjiang327/auctus-agent`
- [x] `auctus-api` 推送到 GitHub：`ddjiang327/auctus-api`
- [x] `auctus-agent` 打 tag：`agent-v0.1.0`
- [x] `auctus-api` 打 tag：`api-v0.1.0`
- [x] `auctus-agent` 生成 macOS / Windows zip 安装包
- [x] `auctus-agent` GitHub Release 发布：`agent-v0.1.0`

**当前发布地址：**

```text
Agent repo: https://github.com/ddjiang327/auctus-agent
Agent release: https://github.com/ddjiang327/auctus-agent/releases/tag/agent-v0.1.0
API repo: https://github.com/ddjiang327/auctus-api
```

**下一步：**

- [x] 给 `auctus-api` 补基础自动化测试
- [ ] 部署 `auctus-api` staging 环境
- [ ] 让 `auctus-agent` 首次设置支持本地 / staging / production 托管 API 地址
- [x] 在 `auctus-agent` 设置页显示当前版本、最新版本和下载入口

---

### Phase 13.6：朋友试用版分发、域名与 staging 闭环

目标：拿到一个能发给朋友安装试用的 Auctus Agent 测试版，并把下载页、API staging、国内访问、收款测试和反馈入口串成一个可执行闭环。

**推荐线上结构：**

```text
www.leyoustudio.com                 Netlify：品牌页 / 下载页 / 安装说明
download.leyoustudio.com        Netlify 或 GitHub Releases：Agent zip 下载
api.leyoustudio.com             Vercel：海外 API / 账号 / 计费 / Relay
cn-api.leyoustudio.com          阿里云 ECS：中国用户低延迟 API / Relay
account.leyoustudio.com         API dashboard 或 Netlify 静态入口 + API
www.leyoustudio.com/pay             测试期收款说明 / 支付链接 / 付款后填写邮箱或订单号
```

**短期产品路径：**

```text
Netlify 下载页
  -> 下载 Auctus Agent 测试包
  -> 双击 Setup.command / Setup.bat
  -> 首次启动向导
  -> 选择：
     1. 自己填 API Key（最快可用）
     2. 使用 Auctus 托管额度（中国用户优先走 cn-api.leyoustudio.com）
     3. 本地模型
  -> 完成一个真实任务
  -> 需要额度时进入 /pay 或 dashboard 充值入口
  -> 用户反馈安装、配置、任务失败或扣费问题
```

**部署策略：**

- [ ] 下载页继续放 Netlify：适合静态页、下载说明、安装教程、反馈入口和快速改文案
- [ ] Agent 安装包先放 GitHub Releases：`Auctus-Agent-mac-v0.1.1.zip` / `Auctus-Agent-windows-v0.1.1.zip`
- [ ] Netlify 下载页链接 GitHub Release assets；后续可同步到 Netlify 或阿里云 OSS/CDN
- [ ] 海外 API staging 放 Vercel：`api.leyoustudio.com`
- [ ] 国内 API staging 放阿里云 ECS：`cn-api.leyoustudio.com`
- [ ] 若自定义域名正式绑定中国大陆 ECS，需要先确认 ICP 备案；备案前可先用 Netlify/Vercel 或阿里云香港/新加坡节点
- [ ] API dashboard 短期可继续使用 `auctus-api/static/dashboard.html`，后续再拆成独立前端

**收款测试策略：**

- [ ] 测试期先接“跳转收款链接 / 二维码页”，不先做完整支付 webhook
- [ ] `/pay` 页面展示体验包、月度测试包、支付方式和付款后填写邮箱/订单号的入口
- [ ] 付款后由管理员手动给账号充值，验证余额、交易记录、扣费和充值入口体验
- [ ] 等 3-5 个朋友真实跑完安装和任务后，再接 Stripe / Alipay / WeChat Pay webhook

**7 天执行计划：**

1. 打 Agent 测试包：生成 Mac / Windows zip，版本 `agent-v0.1.1`，确认双击安装可用。
2. 做下载页：`www.leyoustudio.com/download` 放下载按钮、安装步骤、更新日志、反馈入口。
3. 部署 API staging：海外 Vercel `api.leyoustudio.com`，中国阿里云 `cn-api.leyoustudio.com`。
4. 配置 Agent 默认托管 API：首次向导支持中国 / 海外 / 自定义 API 地址。
5. 接入收款链接：`www.leyoustudio.com/pay` 和 dashboard 充值入口先跳转到测试收款页。
6. 发给 3-5 个朋友试用：只要求安装、配置模型、完成一个真实任务。
7. 只修阻塞问题：安装失败、启动失败、API 连接失败、充值显示错误、任务执行失败。

**验收标准：**

- [ ] 朋友能从 `www.leyoustudio.com/download` 下载 Mac 或 Windows 测试包
- [ ] 非技术用户能双击安装并在 10 分钟内进入首次设置向导
- [ ] 用户能选择 BYO Key、Auctus 托管 API 或本地模型
- [ ] 中国用户能通过 `cn-api.leyoustudio.com` 完成登录、额度查看和一次托管 API 对话
- [ ] 海外用户能通过 `api.leyoustudio.com` 完成登录、额度查看和一次托管 API 对话
- [ ] 充值入口能跳转到测试收款页，并支持人工充值后余额变化可见
- [ ] 下载页、安装包、版本接口和更新入口指向一致，不出现旧版本链接
- [ ] 收到至少 3 个真实用户的安装和任务反馈

备注：这一阶段不追求完整商业支付和生产级双区域数据同步，核心是拿到“可安装、可启动、可完成任务、可测试充值”的朋友试用版。

---

_最后更新：2026-05-16（Phase 13.7 macOS .pkg 构建脚本 + Phase 13.8 Windows .exe 安装程序脚本已完成）_
