# Cron Job（定时任务）

本目录用于"定时自动执行"的能力（cron）。核心思路：

- 先用 `python agent.py cron add-*` **生成可执行脚本**（会写到 `data/cron/scripts/`，你可以打开检查）
- 再用 `python agent.py cron export` 生成 crontab 片段（你可以手动粘贴到 crontab）
- 或用 `python agent.py cron apply` 让 Auctus 自动写入系统 crontab（会覆盖 AuctusAgent 管理块）

## Windows 用户

Windows 没有 cron，但可以用 **任务计划程序（Task Scheduler）**：

```powershell
# 生成 Windows 任务计划脚本
python agent.py cron export-windows --output scripts\setup_tasks.ps1

# 然后以管理员身份运行生成的脚本
powershell -ExecutionPolicy Bypass -File scripts\setup_tasks.ps1
```

或者直接尝试写入任务计划：

```powershell
python agent.py cron apply-windows
```

查看已有的 Windows 任务：

```powershell
python agent.py cron list-windows
```

## macOS / Linux 用户

```bash
python agent.py cron export
```

然后把输出添加到 crontab，或直接：

```bash
python agent.py cron apply
```

## 示例 1：每天备份记账数据（文件夹）

把 `inputs/test/`（你也可以换成 `inputs/test/记账/`）每天打包备份到 `data/backups/`：

```bash
python agent.py cron add-backup \
  --name "daily-bookkeeping-backup" \
  --cron "0 8 * * *" \
  --source "inputs/test" \
  --backups "data/backups"
```

然后导出 crontab 片段：

```bash
python agent.py cron export
```

写入系统 crontab：

```bash
python agent.py cron apply
```

## 示例 2：每周生成一次报表（用 agent.py run）

假设你把"记账数据"整理在 `inputs/ledger.md`（或 csv/html 等可读文件）：

```bash
python agent.py cron add-run \
  --name "weekly-ledger-report" \
  --cron "0 9 * * 1" \
  --file "ledger.md" \
  --task "基于 ledger.md 生成本周记账报表：1) 汇总收入/支出 2) 分类统计 3) 输出 Markdown 报告 + Excel"
```

同样用 `export/apply` 生效。

## 管理

列出任务：

```bash
python agent.py cron list
```

禁用/启用（只影响导出与 apply）：

```bash
python agent.py cron disable --id <job_id>
python agent.py cron enable --id <job_id>
```

删除：

```bash
python agent.py cron remove --id <job_id>
```

## cron 表达式参考

```
┌───────────── 分钟 (0-59)
│ ┌─────────── 小时 (0-23)
│ │ ┌───────── 日 (1-31)
│ │ │ ┌─────── 月 (1-12)
│ │ │ │ ┌───── 星期 (0-6，0=周日)
│ │ │ │ │
* * * * *
```

常用表达式：
- `0 9 * * *` - 每天早上 9:00
- `0 9 * * 1` - 每周一早上 9:00
- `0 9 1 * *` - 每月 1 号早上 9:00
- `0 */2 * * *` - 每 2 小时
- `30 18 * * 1-5` - 工作日晚上 6:30
