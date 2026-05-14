# Closed Learning Loop（闭环自进化）设计稿（基于现有 evolve/learning_candidates）

> 目标：在不推翻现有 `secretary/app/evolution.py + learning_candidates + agent.py evolve` 的基础上，扩展出“越用越强”的闭环自进化系统：  
> **日常轻量进化（Skill 自动生成/复用） + 深度优化（GEPA/DSPy） + 安全审查与回滚**。

本文包含：
- 目录树（模块与文件结构）
- CLI 命令规划
- 核心数据结构/schema（DB + 文件落盘）
- 流程图（Mermaid）
- 兼容层 / 新增模块 / 迁移步骤标注

---

## 0. 现状基线（你现在已经有的）

### 0.1 现有自进化入口

- 代码：`secretary/app/evolution.py`
- 表：SQLite `learning_candidates`
- CLI：`python agent.py evolve scan/list/apply/reject/disable/rollback`

### 0.2 现有候选学习的数据模型（摘自 evolution.py）

`learning_candidates` 表字段：

```text
id, type, title, content, evidence, confidence, status, created_at, applied_at, memory_id
```

apply 的行为：把候选写入 memory（confirmed=1），并标记 applied。

---

## 1. 总体设计：把“经验”资产化为 4 类产物

闭环里我们把“学到的东西”分成 4 类资产（都可候选 → 评估 → 审查 → 启用/回滚）：

1) **Skills（技能文档/小流程）**：任务套路（怎么做），支持条件激活与渐进披露  
2) **Prompt Rules（提示词规则）**：行为约束（如“删除默认进回收站”）  
3) **Tool Policies（工具策略）**：工具调用策略、风控、降级路径  
4) **Config Deltas（配置建议）**：token 预算、触发阈值、默认策略等

其中：
- `learning_candidates` 继续承载：2/3/4 + 部分 workflow  
- **新增** `skill_candidates` 用于承载 1（技能库不应该混在 memory 里）

---

## 2. 模块与文件结构改造图（目录树）

### 2.1 目标目录树（建议）

```text
secretary/app/
  evolution.py                 # 兼容层（兼容旧 API，内部转调到 app/evolve/candidates.py）

  evolve/
    __init__.py
    candidates.py              # 现 evolution.py 主体：learning_candidates（prompt_rule/tool_strategy/...）
    runtime.py                 # 运行时注入：加载 applied 的规则/策略/skills(L0)，控制上下文开销

    traces/
      schema.py                # TraceSession/TraceEvent
      collector.py             # 从 tool logs + messages 归一化 trace（含失败/修复/权限）
      store.py                 # trace 写入/查询（db + jsonl）

    skills/
      schema.py                # SkillDoc/SkillCandidate
      generator.py             # 从 trace 自动生成 SkillCandidate（L0/L1，含触发条件）
      index.py                 # 索引/检索（FTS5 或 embedding），top-k + 触发强度
      applier.py               # apply/reject/disable/rollback（写入 skills 目录 + DB 状态）

    eval/
      schema.py                # EvalSet/EvalCase/EvalRun
      dataset.py               # 从真实 trace 采样生成评估集
      runner.py                # 回放/评测（离线调用模型/工具，打分）
      metrics.py               # success/risk/token/redo 等指标

    gepa/
      variants.py              # 产出变体（skills/prompt/tool desc/config）
      optimizer.py             # GEPA/DSPy 选择最优
      pr.py                    # 生成 PR/patch（本地 git 分支 + 报告）
```

### 2.2 兼容层 / 新增模块标注

- **兼容层（保持原行为）**
  - `secretary/app/evolution.py` → 未来只做 thin wrapper，转调 `app/evolve/candidates.py`
  - `agent.py evolve ...` 旧子命令保留

- **新增模块（闭环扩展）**
  - `app/evolve/traces/*`：trace 标准化
  - `app/evolve/skills/*`：skill 生成/检索/应用
  - `app/evolve/eval/*`：评估集与评测
  - `app/evolve/gepa/*`：深度优化与 PR 输出

---

## 3. 数据落盘结构（资产化）

建议把所有“进化产物”放到 data_dir（`settings.data_dir`）下，便于备份、审查与回滚：

```text
data/
  evolve/
    traces/
      sessions/{session_id}.jsonl

    skills/
      pending/{skill_id}.md
      applied/{skill_id}.md
      disabled/{skill_id}.md
      index.sqlite                   # FTS5 索引（或 embedding 索引）

    eval/
      sets/{eval_name}/cases.jsonl
      runs/{run_id}.json

    experiments/
      {exp_id}/
        variants/
        report.md
        metrics.json
```

---

## 4. CLI 命令规划（在现有 evolve 上扩展）

### 4.1 保持兼容（原命令不变）

```bash
python agent.py evolve scan --messages 120 --logs 80
python agent.py evolve list --status pending
python agent.py evolve apply <candidate_id>
python agent.py evolve reject <candidate_id>
python agent.py evolve disable <candidate_id>
python agent.py evolve rollback <candidate_id>
```

### 4.2 Skill 闭环（新增）

```bash
python agent.py evolve skill-scan [--session <sid>] [--limit 50]
python agent.py evolve skill-list --status pending|applied|rejected|disabled|all
python agent.py evolve skill-apply <skill_id>
python agent.py evolve skill-reject <skill_id>
python agent.py evolve skill-disable <skill_id>
python agent.py evolve skill-rollback <skill_id>
python agent.py evolve skill-export <skill_id> --format md
```

### 4.3 Eval/GEPA（离线深度优化）

```bash
python agent.py evolve eval-build --from-traces --out weekly_eval
python agent.py evolve eval-run --eval weekly_eval --variant baseline
python agent.py evolve gepa-run --eval weekly_eval --target skill|prompt|tool|config
python agent.py evolve pr-create --exp <exp_id>
```

> 这些命令适合用 cron 每周跑一次（成本低、无 GPU）。

---

## 5. Schema（数据结构）

### 5.1 TraceSession（messages + tool logs 归一化）

文件存储：`data/evolve/traces/sessions/{sid}.jsonl`

```json
{
  "trace_id": "tr_20260514_001",
  "session_id": "web-xxxx",
  "task_summary": "把 2.md 移到废纸篓",
  "intent": ["file_operation", "soft_delete"],
  "started_at": 1710000000.0,
  "ended_at": 1710000020.0,
  "events": [
    {"t": 0.1, "type": "message", "role": "user", "content": "2.md 放进 trash"},
    {"t": 0.8, "type": "permission", "scope": "terminal", "choice": "once"},
    {"t": 1.2, "type": "tool_call", "tool": "run_terminal_command", "input": {"command": "..."}},
    {"t": 1.6, "type": "tool_result", "ok": true, "output_files": []}
  ],
  "outcome": {
    "success": true,
    "risk_events": 0,
    "artifacts": [],
    "notes": "软删除路径优先回收站"
  }
}
```

### 5.2 SkillDoc（Markdown + frontmatter：条件激活 + 渐进披露）

文件存储：`data/evolve/skills/applied/{skill_id}.md`

```md
---
skill_id: sk_ledger_backup_v1
name: 记账数据：每日备份与周报
version: 1
status: applied
triggers:
  keywords: ["记账", "备份", "报表", "导出", "csv"]
  file_patterns: ["**/ledger*.md", "**/ledger*.csv", "**/记账/**"]
  tool_patterns: ["make_spreadsheet", "write_file", "cron:add-backup"]
permissions:
  terminal: optional
  files: workspace_or_full
activation:
  level0_max_lines: 8
  level1_on: ["tool_error", "user_confirmed", "repeat_task"]
created_from_trace_ids: ["tr_20260514_001"]
---

## L0（短版步骤）
- ...

## L1（完整版）
1) ...
```

### 5.3 skill_candidates（建议新增表，避免和 memory 混）

新增表（建议）：

```sql
CREATE TABLE IF NOT EXISTS skill_candidates (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  level0 TEXT NOT NULL,          -- 运行时默认注入的短版
  doc_path TEXT NOT NULL,        -- 对应 md 文件（pending/applied/disabled）
  triggers_json TEXT NOT NULL,   -- keywords/file_patterns/tool_patterns
  confidence REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at REAL NOT NULL,
  applied_at REAL,
  trace_ids_json TEXT            -- 来源 trace_id 列表（可选）
);
CREATE INDEX IF NOT EXISTS idx_skill_status ON skill_candidates(status);
```

> `learning_candidates` 保持不变，继续写入 memory（confirmed=1），用于 prompt/tool/config/workflow。

### 5.4 EvalCase（评估用例）

```json
{
  "case_id": "evc_001",
  "input": {"message": "每天自动备份记账数据，并每周生成报表"},
  "setup": {"files": ["inputs/ledger.md"], "permissions": {"terminal": "enabled"}},
  "expectations": {
    "should_create_files": ["data/backups/*.tar.gz"],
    "should_not_use": ["rm "],
    "max_risk_events": 0
  }
}
```

### 5.5 Experiment / Variant（GEPA 产物）

```json
{
  "exp_id": "exp_20260514_gepa_01",
  "target": "skills",
  "base": "sk_ledger_backup_v1",
  "variants": [
    {"id": "v1", "patch_path": "variants/v1.patch"},
    {"id": "v2", "patch_path": "variants/v2.patch"}
  ],
  "results": [
    {"variant": "v1", "success_rate": 0.82, "avg_tokens": 5400, "risk": 0},
    {"variant": "v2", "success_rate": 0.86, "avg_tokens": 5100, "risk": 0}
  ],
  "winner": "v2"
}
```

---

## 6. 流程图（Mermaid）

### 6.1 日常轻量闭环（Skill 自动生成/复用）

```mermaid
flowchart TD
  U[用户任务] --> A[Agent 执行]
  A --> M[messages]
  A --> L[tool logs]
  M --> T[Trace Collector]
  L --> T
  T --> SC[生成 SkillCandidate\n(status=pending)]
  SC --> R{人工审查?}
  R -->|apply| SA[Skill Applied\n写入 skills/applied + 建索引]
  R -->|reject| SR[Rejected]
  SA --> RT[Runtime 检索注入\nL0 默认 / L1 条件触发]
  RT --> A
```

### 6.2 深度优化（Eval → Variants → GEPA → PR）

```mermaid
flowchart TD
  TR[真实 traces] --> EB[Eval Build]
  EB --> ES[Eval Set]
  ES --> V[生成变体\n(Skills/Prompts/Tool/Config)]
  V --> ER[Eval Run/Score]
  ER --> SEL[选择最优]
  SEL --> PR[生成 PR/patch + 报告]
  PR --> H{人工合并?}
  H -->|merge| DEPLOY[上线/生效]
  H -->|no| DISCARD[丢弃/继续迭代]
```

---

## 7. 迁移步骤（从现状平滑升级）

### Step 1（零风险）：抽离 candidates 模块
- 新增 `app/evolve/candidates.py`，把 `evolution.py` 主体迁移过去
- `app/evolution.py` 保留函数签名（兼容层），内部 import 并转调

### Step 2：引入 traces（不影响运行时）
- 新增 `app/evolve/traces/*`，先只落盘 trace_session（jsonl）
- 暂不影响 runtime，不改变行为

### Step 3：SkillCandidate 生成 + 手动审查
- 新增 `skill_candidates` 表
- `skill-scan` 从 traces 生成 pending skills（写到 `skills/pending`）
- `skill-apply` 仅做“移动文件 + 更新表状态 + 建索引”

### Step 4：Runtime 注入（L0/L1 渐进披露）
- 新增 `runtime.py`：每次新任务检索 top-k，注入 L0
- 增加“强命中”或“失败重试”时再注入 L1

### Step 5：Eval/GEPA 离线优化（可选）
- 从 traces 生成 eval set
- 先做简单对比（baseline vs variant）再引入 GEPA/DSPy
- 产出 PR（人工审查），严格可回滚

---

## 8. 安全边界（必须写死的原则）

- 所有进化产物默认 `pending`，必须人工确认才生效  
- 删除类操作默认“移到回收站/废纸篓（可恢复）”，除非用户明确永久删除并再次确认  
- 运行时注入要 **渐进披露**：先 L0，必要时 L1，控制 token 开销  
- 深度优化（GEPA）只能“产出 PR/patch”，不能自动修改主分支  

