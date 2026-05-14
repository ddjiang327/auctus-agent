# Auctus Local Agent 两阶段建设方案

## 0. 总体结论

项目按两阶段做：

```text
第一阶段：本地自用版
目标：先跑通你自己的高频工作流，证明 Agent 真有用。

第二阶段：商用增强版
目标：吸收 Claude 方案里的 Relay、用户系统、配额、BYO Key、云端转发能力，为未来商业化预留。
```

核心原则：**第一阶段不背商业化包袱，第二阶段再上 Claude 的商用架构。**

你现在最容易犯的错是：一开始就做 Relay、用户、配额、计费、跨平台安装器，结果 6 周后基础设施有了，但 Agent 还不能真正帮你干活。这个风险必须压住。

---

# 第一阶段：Auctus Local Agent 本地自用版

## 1. 阶段目标

做一个运行在你电脑上的个人工作 Agent，先服务你自己。

它第一版只解决 5 件事：

1. 读取文件：PDF、Markdown、TXT、CSV、Excel、Word。
2. 生成报告：Markdown / HTML。
3. 生成表格：Excel / CSV。
4. 生成网页原型：React + Tailwind 单页。
5. 生成候选记忆：项目背景、用户偏好、固定规则，但需要你确认后才保存。

这符合 `chatgpt-plan.md` 里的核心判断：第一版应该是“本地工作台 + 工具型 Agent + 手机遥控器”，不是万能 Agent。

---

## 2. 第一阶段产品定位

产品名：

```text
Auctus Local Agent
```

一句话定位：

```text
一个本地运行的 AI 工作助理，能把输入资料转成报告、表格、网页原型和可复用记忆。
```

不是做什么：

```text
不做全自动电脑控制
不做自动发邮件
不做自动提交代码
不做自动修改服务器
不做自动交易
不做插件市场
不做多用户 SaaS
```

这些限制不是保守，而是安全边界。原方案也明确指出，如果让模型直接操作文件、发送消息、改代码、调用敏感 API，迟早会出错，所以必须让“大模型负责思考，工具层负责执行，关键操作人工确认”。

---

## 3. 第一阶段核心 Demo

第一阶段最重要的不是 UI，而是先跑通这个闭环：

```bash
python agent.py run "./inputs/prd.md" --task "生成项目总结、功能清单、测试用例和网页原型"
```

输出目录：

```text
outputs/
├── summary.md
├── features.xlsx
├── test_cases.xlsx
├── prototype/
│   ├── package.json
│   ├── src/App.jsx
│   └── index.html
└── memory_candidates.json
```

这个闭环来自 `chatgpt-plan.md` 的最小可行产物设计：项目总结、功能清单 Excel、测试用例 Excel、React + Tailwind 原型页面、候选记忆。它也明确指出，如果这个闭环跑不通，手机联动、MCP、自动操作电脑都没有意义。

---

## 4. 第一阶段系统架构

```text
CLI / 本地 Web UI / Telegram Bot
        ↓
FastAPI 本地服务
        ↓
Agent Orchestrator
        ↓
Model Adapter
Claude / OpenAI / DeepSeek / Gemini / 本地模型
        ↓
Tool Registry
        ↓
Local Workspace
inputs / outputs / temp / logs
        ↓
SQLite + Chroma
```

### 技术选型

| 模块    | 选择                                  | 理由                            |
| ----- | ----------------------------------- | ----------------------------- |
| 后端    | Python + FastAPI                    | 文件处理、AI 工具生态成熟                |
| 模型适配  | LiteLLM / OpenAI-compatible adapter | 方便切换 Claude、GPT、DeepSeek、本地模型 |
| 数据库   | SQLite                              | 本地单人使用足够                      |
| 向量库   | Chroma                              | 本地文件存储，部署成本低                  |
| 表格    | openpyxl + pandas                   | Excel / CSV 处理稳定              |
| 网页生成  | Jinja2 + React/Tailwind 模板          | 模型只填内容，不让它乱生成整个工程             |
| 本地 UI | React / Electron 后置                 | 不要第一天就做桌面壳                    |
| 手机入口  | Telegram Bot 优先                     | 不需要先做 App                     |

`ARCHITECTURE.md` 已经给出一套轻量工具清单，包括 `make_spreadsheet`、`make_webpage`、`summarize_text`、`remember`、`recall`、`read_file` 等，第一阶段可以直接沿用。

---

## 5. 第一阶段工具清单

### P0 工具：必须先做

| 工具                          | 作用                       | 风险级别 |
| --------------------------- | ------------------------ | ---- |
| `read_file`                 | 读取 workspace 内文件         | 中    |
| `summarize_text`            | 总结文本 / 文件内容              | 低    |
| `make_markdown_report`      | 生成 Markdown 报告           | 低    |
| `make_spreadsheet`          | 生成 Excel / CSV           | 低    |
| `make_webpage`              | 生成 HTML 报告               | 中    |
| `make_react_prototype`      | 生成 React + Tailwind 单页原型 | 中    |
| `extract_memory_candidates` | 提取候选记忆                   | 中    |
| `search_memory`             | 检索历史记忆                   | 低    |
| `write_log`                 | 记录工具调用日志                 | 低    |

### P1 工具：第一阶段后半段做

| 工具                       | 作用                 |
| ------------------------ | ------------------ |
| `update_file`            | 修改 workspace 内已有文件 |
| `run_preview`            | 启动网页预览             |
| `telegram_send_file`     | 把结果发到手机            |
| `create_task`            | 建立任务记录             |
| `recall_project_context` | 找回项目背景             |

### 暂时禁止

```text
delete_file
run_shell_command
git_commit
git_push
send_email
browser_login
database_write
server_modify
trade_order
```

---

## 6. 第一阶段开发路线

### Phase 0：CLI Demo

目标：不做 UI，先证明 Agent 能干活。

任务：

```text
1. 建立 Python 项目
2. 接入一个模型 API
3. 实现 read_file
4. 实现 summarize_text
5. 实现 make_spreadsheet
6. 实现 make_react_prototype
7. 输出完整 outputs 目录
```

验收：

```text
输入一个 PRD / 项目文档
输出 summary.md、features.xlsx、test_cases.xlsx、prototype/
```

周期：3–7 天。

---

### Phase 1：工具调用系统

目标：模型不能直接执行操作，必须通过工具层。

流程：

```text
用户输入任务
↓
模型判断需要哪些工具
↓
生成 tool_call JSON
↓
系统校验权限
↓
工具执行
↓
结果返回模型
↓
模型生成最终答复
↓
写入日志
```

验收：

```text
每次工具调用都有日志：
- 时间
- user_input
- tool_name
- tool_input
- tool_output
- risk_level
- model
- token_usage
```

---

### Phase 2：本地 Web UI

目标：做一个可用的工作台。

界面结构：

```text
左侧：任务历史
中间：聊天窗口
右侧：输出文件
底部：工具调用日志
```

功能：

```text
文件上传
任务输入
结果下载
日志查看
记忆候选确认
模型切换
```

---

### Phase 3：记忆系统

目标：Agent 能记住项目背景和用户偏好，但不能乱记。

记忆分 4 类：

| 类型   | 示例                                    | 是否自动保存  |
| ---- | ------------------------------------- | ------- |
| 用户偏好 | 喜欢中文、喜欢严苛分析、常用 React                  | 候选后确认   |
| 项目背景 | Auctus 使用 Supabase / Netlify / Vercel | 候选后确认   |
| 工作规则 | 新闻分析六字段、PRD 格式                        | 候选后确认   |
| 临时信息 | 某次 bug、某天对话                           | 默认不长期保存 |

数据结构：

```json
{
  "type": "project_context",
  "title": "Auctus 项目部署规则",
  "content": "Auctus 使用 Supabase、Netlify、Vercel，不能暴露 service role key。",
  "source": "chat",
  "importance": 4,
  "created_at": "2026-05-13",
  "expires_at": null,
  "tags": ["auctus", "deployment"]
}
```

`chatgpt-plan.md` 也强调记忆系统不能乱做，应采用“记忆候选 + 用户确认”，否则记忆库会越来越脏。

---

### Phase 4：手机联动

第一阶段手机只做遥控器，不做完整 App。

优先方案：

```text
Telegram Bot
```

原因：

```text
不需要先做 App
文件回传简单
通知天然可用
适合你在澳洲使用
```

`ARCHITECTURE.md` 也建议先用 Telegram Bot，本机 FastAPI + Telegram polling，不需要内网穿透，还能直接把 Excel / HTML 作为附件发回手机。

验收：

```text
手机发：
“帮我总结这个文件，并生成表格”

电脑执行：
1. 读取文件
2. 总结
3. 生成 Excel
4. Telegram 回传结果文件
```

---

## 7. 第一阶段完成标准

第一阶段完成，不看代码写了多少，只看这 5 个真实场景能不能跑通：

| 场景           | 输入           | 输出                               |
| ------------ | ------------ | -------------------------------- |
| PRD 分析       | 上传 PRD       | 项目总结 + 功能清单 + 测试用例 + 原型          |
| 交易新闻分析       | 粘贴新闻         | 新闻等级 + 影响路径 + 是否 price in + 操作建议 |
| 项目 checklist | 上传 checklist | 风险点 + 缺失项 + 下一步                  |
| TokenHub 文案  | 输入主题         | 宣传文案 + HTML 页面                   |
| 项目记忆召回       | 问历史规则        | 找回相关项目背景                         |

---

# 第二阶段：商用增强版

第二阶段吸收 Claude 写的商用方案，但不是照搬到第一阶段。

## 1. 阶段目标

把第一阶段的本地 Agent 升级成可商业化的产品架构。

新增能力：

```text
用户系统
云端 Relay
LLM Proxy
配额计量
BYO Key
Telegram Webhook
桌面 Agent 隧道
跨设备任务同步
基础计费预留
```

Claude 方案的核心是：桌面 Agent 跑在用户本机，云端 Relay 只负责账号、配额、Telegram 入口和桌面之间的隧道。这个分层思路适合第二阶段采用。

---

## 2. 第二阶段架构

```text
手机 Telegram / Web / 未来 App
        ↓
Cloud Relay
- Auth Service
- Quota Service
- LLM Proxy
- Telegram Webhook
- Tunnel Hub
        ↓
WebSocket Tunnel
        ↓
Desktop Agent
- 本地工具
- 本地记忆
- 本地文件
- 本地 workspace
        ↓
LLM Route
proxy / byo / local
```

关键原则：

```text
Relay 不存对话原文
Relay 只存账号、配额、路由和 metadata
用户文件、记忆、任务内容尽量留在本地
```

Claude 方案也明确提出不要在 Relay 上存对话内容，只存账号、配额和 tunnel 路由表，对话存在桌面端。

---

## 3. 第二阶段核心抽象

Claude 方案里最有价值的是这 5 个抽象，放到第二阶段实现：

| 抽象         | 作用                         |
| ---------- | -------------------------- |
| `User`     | 区分不同用户                     |
| `ApiKey`   | 桌面 Agent 与 Relay 通信用 token |
| `Quota`    | 记录 input/output token 和成本  |
| `LlmRoute` | 决定走代理、BYO Key 还是本地模型       |
| `Tunnel`   | 桌面 Agent 与 Relay 的双工通道     |

Claude 方案中这 5 个被列为商用化最小核心抽象：`User`、`ApiKey`、`Quota`、`LlmRoute`、`Tunnel`。

我的调整是：**第一阶段只预留字段，不实现完整系统；第二阶段正式做。**

---

## 4. 第二阶段功能模块

### 4.1 用户系统

功能：

```text
注册
登录
JWT
设备绑定
用户计划 plan
余额 balance
```

数据表：

```text
users
- id
- email
- password_hash
- plan
- balance_cents
- free_quota_tokens
- created_at

devices
- id
- user_id
- device_name
- device_type
- status
- last_seen_at
```

---

### 4.2 LLM Proxy

目标：用户默认可以用你的模型额度。

用户首次进入产品时必须先选择模型接入方式：

```text
使用 Auctus 托管 API
→ 登录账号
→ 查看免费额度或充值余额
→ Relay 用你的模型 Key 转发并按用户扣费

使用自己的 API Key
→ 选择 OpenAI / Anthropic / DeepSeek 等服务商
→ 粘贴并验证 API key
→ 本地加密保存，调用不消耗你的余额

使用本地模型
→ 配置 Ollama / LM Studio
→ 不走云端计费
```

流程：

```text
用户请求
↓
Relay 检查 quota
↓
Relay 用你的 Claude/OpenAI Key 转发
↓
记录 token 和成本
↓
返回结果给桌面 Agent
```

风险：这个模块最危险，因为它直接烧你的钱。

必须有：

```text
月度 token 上限
每日 cost 上限
每分钟速率限制
异常 spike 报警
新用户低额度
余额不足自动停止
充值入口
账单明细
```

Claude 方案也把“代理 API 被滥用，一个用户烧光你的钱”列为极高风险，要求通过 quota、速率限制和异常监控控制。

---

### 4.3 BYO Key

目标：高级用户可以填自己的 API Key。

路由逻辑：

```text
route = proxy
→ 走你的 Relay LLM Proxy

route = byo
→ 桌面端直连用户自己的 Claude/OpenAI/DeepSeek Key

route = local
→ 调本地 Ollama / LM Studio
```

Claude 方案也明确提到用户默认走你的 LLM Proxy，超额或主动切换时可以 BYO Key。

---

### 4.4 Tunnel Hub

目标：解决用户电脑在 NAT 后面，手机消息如何发到电脑。

流程：

```text
桌面 Agent 启动
↓
主动连接 Relay WebSocket
↓
Relay 维护 session
↓
Telegram 消息进入 Relay
↓
Relay 通过 tunnel 推给对应桌面 Agent
↓
桌面 Agent 执行任务
↓
结果通过 Relay 回 Telegram
```

Claude 方案中 M4 的云端 Relay 就包含 WebSocket Tunnel Hub、Telegram Webhook、LLM Proxy 和 Auth / Quota 接口。

---

### 4.5 商用记账

每次 LLM 调用记录：

```text
user_id
task_id
model
route
input_tokens
output_tokens
estimated_cost
created_at
```

用途：

```text
成本控制
套餐限制
用户账单
异常监控
模型效果分析
```

---

### 4.6 API 充值计费网站

目标：把你的模型额度产品化成一个可登录、可充值、可查账单的 API 计费网站。

核心页面：

```text
登录 / 注册
充值套餐
余额和免费额度
用量明细
账单流水
API / Agent 设备管理
管理后台
```

核心规则：

```text
托管 API 调用必须绑定 user_id
每次调用先检查余额、免费额度、速率和成本上限
调用成功后记录 token、模型成本、平台扣费、余额变化
余额不足时停止托管 API 调用并提示充值
BYO Key 调用只记录本地用量，不扣 Auctus 余额
```

这个网站不是普通 landing page，而是商业闭环：登录、充值、扣费、风控、账单和管理后台都要能闭起来。

---

## 5. 第二阶段开发路线

### Phase 2.1：用户系统和 usage 记录

任务：

```text
users 表
api_keys 表
usage 表
登录接口
本地设置页
余额字段
免费额度字段
```

验收：

```text
每次模型调用都能按 user_id 记录 token 和成本。
```

---

### Phase 2.2：BYO Key

任务：

```text
设置页填写 Anthropic / OpenAI / DeepSeek Key
本地加密保存
route 切换为 byo
调用走用户自己的 key
```

验收：

```text
用户切换 BYO 后，不再消耗你的 Relay 配额。
```

Claude 方案中 M3 就是用户系统与 BYO Key 切换，包括 users、api_keys、usage 表，以及 proxy / byo 路由。

---

### Phase 2.3：云端 Relay

任务：

```text
部署 FastAPI Relay
Auth 接口
Quota 接口
LLM Proxy
Telegram Webhook
Tunnel Hub
```

验收：

```text
手机 Telegram 发消息
↓
Relay 收到
↓
推给桌面 Agent
↓
桌面执行
↓
结果回到 Telegram
```

---

### Phase 2.4：配额和风控

任务：

```text
每用户月度额度
每日全局成本上限
速率限制
异常报警
额度耗尽提示
BYO Key 引导
充值入口
余额不足阻断
```

验收：

```text
免费用户不能无限烧 API。
系统能在成本异常时自动停止代理调用。
```

---

### Phase 2.4.5：API 计费网站

任务：

```text
登录 / 注册页面
用户余额页
充值套餐和订单
支付状态回调
用量明细
账单流水
管理后台
Relay 扣费接口
```

验收：

```text
用户可以登录后充值。
选择 Auctus 托管 API 的用户可以消耗余额完成 Agent 调用。
每次扣费都能追溯到 user_id、模型、token、成本和订单余额。
选择 BYO Key 的用户不会被平台重复扣费。
```

---

### Phase 2.5：上线准备

任务：

```text
隐私政策
用户协议
安装教程
Mac / Windows 启动脚本
错误日志
成本日报
简单 landing page
```

Claude 方案中也把上线就绪列为 M6，包括隐私政策、注册落地页、额度耗尽提示、日志日报和安装教程。

---

# 两阶段总路线图

## 阶段一：本地自用版

周期：约 3–6 周。

| 周期     | 目标          | 交付物                                                |
| ------ | ----------- | -------------------------------------------------- |
| Week 1 | CLI Demo    | summary.md、features.xlsx、test_cases.xlsx、prototype |
| Week 2 | 工具调用系统      | tool_call、权限校验、日志                                  |
| Week 3 | 本地 Web UI   | 聊天、文件上传、输出管理                                       |
| Week 4 | 记忆系统        | 候选记忆、确认保存、搜索                                       |
| Week 5 | Telegram 联动 | 手机发任务、电脑执行、文件回传                                    |
| Week 6 | 稳定性         | 错误处理、日志、模型切换、workspace 隔离                          |

## 阶段二：商用增强版

周期：约 4–8 周。

| 周期       | 目标               | 交付物                         |
| -------- | ---------------- | --------------------------- |
| Week 1   | 用户系统             | users、api_keys、usage        |
| Week 2   | BYO Key          | proxy / byo / local 路由      |
| Week 3–4 | Relay            | Auth、Quota、LLM Proxy、Tunnel |
| Week 5   | Telegram Webhook | Relay → Desktop Agent 全链路   |
| Week 6   | 配额风控             | 限速、额度、成本上限                  |
| Week 7–8 | 上线准备             | 安装包、文档、隐私政策、成本日报            |

---

# 最终建议版本

你应该这样定稿：

```text
第一阶段：
Auctus Local Agent
本地自用，先打通文件 → 报告 → 表格 → 原型 → 记忆 → 手机回传。

第二阶段：
Auctus Agent Cloud / Relay
吸收 Claude 的商用方案，上用户、配额、BYO Key、LLM Proxy、Tunnel、Telegram Webhook。
```

最关键的分界线：

```text
第一阶段验证“这个 Agent 对你有没有用”。
第二阶段验证“这个 Agent 能不能给别人用并且不亏钱”。
```

现在开工顺序不要变：

```text
1. 先做 CLI Demo
2. 再做工具调用
3. 再做本地 UI
4. 再做记忆
5. 再做 Telegram
6. 最后再做 Claude 那套商用 Relay
```

---

# 2026-05-14 补充：已完成更新（实现回顾）

> 这部分是对 roadmap 的“落地实现”记录，便于你回看今天具体做了什么。

## A. 安装与首次设置（Phase 12）

- 根目录新增一键入口：`Setup.command / Start.command`（Mac）与 `Setup.bat / Start.bat`（Windows）
- 安装脚本强化：Python 3.10+ 检查、自动建 venv、自动安装依赖、自动生成 `.env`
- 安装流程不再因为缺少 API key 而中断：允许先启动 Web UI，在设置/向导里完成配置

## B. Web UI 体验升级（The Digital Companion + Minimalist Capsule）

- 新增全新 UI：动态 Core、毛玻璃胶囊输入条、圆角卡片、任务完成提示音、执行中呼吸态
- 交互细节：Enter 发送 / Shift+Enter 换行 + “换行”按钮；“伙伴/胶囊”模式挪到设置里
- 透明度与掌控：状态流（轮询工具日志）、紧急停止按钮（取消本次请求，立即交回控制权）
- 追问不中断：对“选择哪个文件夹/是否删除”等追问场景，提供快捷按钮；对“好/好的”做澄清

## C. 权限体系（能做但缺权限 → 先问）

- 权限弹窗统一支持：终端 / 日历 / 文件访问（once / always / no）
- 文件访问：workspace vs full computer，可一次性/永久授权（一次性只对本次请求生效）
- 删除默认“可恢复”：默认移到废纸篓/回收站，除非用户明确要求“永久删除”
- 创建文件/文件夹：在已授权 workspace 内直接执行（不再要求“确认执行/终端权限”）

## D. 对话上下文压缩（省 token）

- 压缩触发：消息条数阈值 + token 预算（默认 8000）双条件，先到先触发
- 摘要分段累计：按 `covers_until_msg_id` 增量合并摘要，避免重复压同一段

## E. 本地自动化（Cron Jobs）

- 新增 `python agent.py cron ...`：生成可执行脚本、保存 jobs 元数据、导出/写入系统 crontab
- 内置模板：每日备份文件夹、每周定时运行 `agent.py run` 生成报表

---

# Closed Learning Loop（闭环自进化系统）计划（不写代码版）

目标：让 Agent **越用越强**，不是简单记忆，而是形成“可复用技能 + 可评估优化 + 可审查上线”的闭环。

## 1) 日常轻量进化（最核心：Skill 自动生成与复用）

### 1.1 Trace 标准化（输入）
- 采集来源：messages + tool logs（每次任务的工具序列、错误与修复）
- 形成 `trace_session`：任务意图、工具轨迹、产物、失败点、恢复策略、风险事件、用户确认点

### 1.2 SkillCandidate 自动生成（输出）
- 触发条件：任务复杂度高 / 出现错误并修复 / 产出文件 / 用户反馈“可复用”
- 产物格式：Markdown + metadata（when_to_use、steps、pitfalls、permissions、examples）
- 存放与状态：默认 `pending`，必须人工确认后 `applied`，并可禁用/回滚

### 1.3 运行时检索与渐进式披露（控制上下文开销）
- Skills 建索引（FTS5 或 embedding），每次任务检索 top-k
- 注入策略：
  - L0（短版）：标题 + 触发条件 + 3–8 行“关键步骤”
  - L1（完整版）：仅当任务强命中才加载（或模型明确选择某 skill）

## 2) 深度优化管道（GEPA / DSPy 自进化引擎，离线跑）

### 2.1 自动生成评估集（Eval Set）
- 从真实 trace 抽样：任务描述 + 输入样本 + 期望产物/约束
- 指标：成功率、风险事件次数、工具重试次数、token 成本、用户确认率

### 2.2 变体生成 → 评估 → 选优 → PR
- 可优化对象：Skills 文件、系统提示（prompts）、工具描述/策略、Agent 配置
- 输出：生成变体、在 eval set 上跑评估、选优、产出 PR（人工审查合并）

## 3) 安全与可控（闭环的“刹车系统”）

- 所有进化产物默认 `pending`（不自动生效）
- 高风险变更（prompts/tools 策略）必须：
  - 给出评估指标对比
  - 通过人工审查
  - 可一键回滚到上一个稳定版本

## 4) 与现有系统的最小融合点（你现在最划算的落地顺序）

1. 先做：SkillCandidate 生成 + skills 索引 + 运行时 L0/L1 注入  
2. 再做：评估集生成 + 自动对比指标（不引入 GEPA 也能跑）  
3. 最后做：GEPA/DSPy 变体优化 + 自动 PR 管道（用 cron 定期运行）
