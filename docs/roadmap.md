# Auctus Agent Roadmap

This roadmap is only for the desktop/local agent.

## Now

- Stabilize the current desktop UI and runtime.
- Keep Mac/Windows packaging scripts working.
- Improve first-run setup and model configuration.
- Add an explicit Task Mode for multi-step work: users can keep normal chat fast, or opt into a slower planned workflow for research, comparisons, purchasing decisions, and other complex tasks.
- Tighten permission prompts for files, terminal, browser, and external integrations.
- Keep mobile pairing compatible with `auctus-agent-mobile`.

### Outputs File Manager ✅ Done

- `GET /api/outputs` lists all files in `outputs/` (name, size, modified time, type).
- "📁 文件" topbar panel: reverse-chronological file list, click to open/download, HTML preview in new tab.

### Conversation History Search ✅ Done

- `GET /api/history/sessions` lists all sessions with first-message preview and timestamps.
- `GET /api/history/search?q=&session_id=` searches messages by keyword.
- "📜 历史" topbar panel: session list, keyword search, click to load messages.

### Task Mode MVP ✅ Done

Goal: make Auctus Agent behave more like a practical assistant for complex tasks without slowing down simple questions.

- Default chat stays fast and direct.
- Users can explicitly enter Task Mode from the UI.
- Task Mode starts by turning the user's goal into a visible checklist.
- The agent works through steps one by one instead of jumping from a single search result to a final answer.
- The UI shows a subdued side/bottom progress panel with the current step, completed steps, pending steps, and short activity updates.
- The progress panel shows observable work notes, not hidden chain-of-thought.
- The first version should support one active task at a time and does not need long-term task history.

## Done

- CLI and FastAPI/Web UI foundation.
- Local tool execution and output generation.
- Long-term memory and candidate confirmation flow.
- Skill learning and evaluation loop scaffolding.
- Telegram/remote-control integration.
- Cron/scheduled task support.
- macOS standalone build pipeline.
- Hosted API/BYO key integration hooks.

## Next

- Reduce UI complexity in settings and onboarding.
- Add clearer runtime diagnostics for failed tool calls.
- Improve release smoke tests before packaging.
- Document what is local-only versus hosted/API-backed.
- Keep Windows packaging aligned with Mac packaging.

## Later

- More native desktop shell polish.
- Richer mobile relay integration through `auctus-api`.
- Safer browser automation presets.
- More structured exports for generated user projects.

---

## PM Backlog (未排期，待用户选优先级)

### 3. 语音输入 STT ✅ Done
输入框旁加 🎤 按钮，使用浏览器 Web Speech API（免费，Chrome/Edge/Safari 支持）。点击开始录音，实时展示中间结果，结束后填入输入框。录音中按钮有脉冲动画提示。浏览器不支持时自动禁用按钮。

### 4. 本地知识库 RAG ✅ Done
`app/rag.py` — Chroma `rag_docs` collection，支持 index_folder / search。`/api/rag/index|folders|folder(DELETE)` 端点。Settings 页"本地知识库"板块：输入路径一键索引，已索引文件夹列表可删除。Agent 每次对话自动 top-k 检索注入。

### 5. 每日摘要推送（留存）✅ Done
`cronjobs.py` — `set_daily_brief(enabled, hour)` 写 `setup_state` 并插入/删除 `__daily_brief__` cron 任务。`/api/daily-brief/config` GET/POST。Settings 页开关 + 时间选择。

### 6. 常驻上下文卡片（Pinned Context）✅ Done
`agent.py` — `_pinned_context()` 从 setup_state 读取注入 system msg。`/api/pinned-context` GET/POST。Settings 页"常驻上下文"文本框，保存即生效。

### 7. Playbooks 编辑器 UI ✅ Done
`playbooks.py` — `list/add/update/remove_playbook()` + `data/playbooks.json`。`/api/playbooks` CRUD。Settings 页 Playbooks 管理：列表显示启用状态，支持切换/删除；添加表单填写名称、正则触发词、注入内容。

### 8. 操作审计日志 UI ✅ Done
`/api/logs` 扩展支持 `q`/`risk`/`limit` 过滤参数（倒序返回）。"📋 日志"顶栏按钮打开侧面板：关键词搜索 + 风险等级筛选；每条日志显示工具名、风险色标（绿/黄/红）、耗时、时间戳、参数摘要；点击展开完整内容。

### 9. 多用户档案
支持切换"用户档案"，不同档案有独立 memory、outputs、cron 任务。家庭版/团队版商业化路径（$X/月 额外档案）。工期：1 周+。

### 10. 使用统计 Dashboard（用户成就感）✅ Done
`/api/stats` 返回本周调用/费用/token、历史会话数、输出文件数、7日柱状图、Top 5 工具。"📊 统计"顶栏按钮打开侧面板：4 格摘要卡、每日调用柱状图、工具横向进度条排行。

---

_Last updated: 2026-05-22_

## Related Docs

- `docs/release.md`
- `docs/standalone-build.md`
- `docs/mobile-cloud-relay.md`
- `docs/closed-learning-loop.md`
- `docs/agent-capability-gaps.md`
