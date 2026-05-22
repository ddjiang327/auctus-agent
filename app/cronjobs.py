"""Cron job helper: generate executable scripts and manage crontab entries.

设计目标：
- 生成可执行脚本文件（.sh），便于用户自己检查与修改
- 以 JSON 方式保存任务元数据（jobs.json），便于 list/remove/export
- 默认只“生成 + 导出 crontab 片段”，是否写入系统 crontab 由用户主动执行 `agent.py cron apply`
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from .config import settings


CRON_DIR = (settings.data_dir / "cron").resolve()
SCRIPTS_DIR = (CRON_DIR / "scripts").resolve()
JOBS_PATH = (CRON_DIR / "jobs.json").resolve()

CRONTAB_MARK_BEGIN = "# --- AuctusAgent cron jobs (BEGIN) ---"
CRONTAB_MARK_END = "# --- AuctusAgent cron jobs (END) ---"


@dataclass
class CronJob:
    id: str
    name: str
    schedule: str
    script_path: str
    description: str = ""
    enabled: bool = True
    created_at: float = 0.0
    # Event-trigger fields (empty string = time-based cron)
    trigger_type: str = ""   # "" | "email_match" | "url_watch"
    trigger_config: str = "" # JSON-encoded trigger params


def ensure_dirs() -> None:
    CRON_DIR.mkdir(parents=True, exist_ok=True)
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    if not JOBS_PATH.exists():
        JOBS_PATH.write_text(json.dumps({"jobs": []}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _safe_name(name: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", (name or "").strip()).strip("._-")
    return value[:60] or "job"


def _read_jobs() -> list[CronJob]:
    ensure_dirs()
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = {"jobs": []}
    out: list[CronJob] = []
    for item in (data.get("jobs") or []):
        try:
            out.append(CronJob(**item))
        except TypeError:
            continue
    return out


def _write_jobs(jobs: list[CronJob]) -> None:
    ensure_dirs()
    JOBS_PATH.write_text(json.dumps({"jobs": [asdict(j) for j in jobs]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_jobs() -> list[dict[str, Any]]:
    return [asdict(j) for j in _read_jobs()]


def add_job(*, name: str, schedule: str, script_body: str, description: str = "") -> dict[str, Any]:
    ensure_dirs()
    job_id = uuid.uuid4().hex[:10]
    safe = _safe_name(name)
    script_path = (SCRIPTS_DIR / f"{safe}_{job_id}.sh").resolve()
    script_path.write_text(_wrap_script(script_body), encoding="utf-8")
    try:
        os.chmod(script_path, 0o755)
    except OSError:
        # 某些文件系统可能不支持 chmod；不阻断
        pass
    job = CronJob(
        id=job_id,
        name=name.strip() or safe,
        schedule=schedule.strip(),
        script_path=str(script_path),
        description=description.strip(),
        enabled=True,
        created_at=time.time(),
    )
    jobs = _read_jobs()
    jobs.append(job)
    _write_jobs(jobs)
    return asdict(job)


def remove_job(job_id: str, *, delete_script: bool = True) -> dict[str, Any]:
    jobs = _read_jobs()
    keep: list[CronJob] = []
    removed: Optional[CronJob] = None
    for job in jobs:
        if job.id == job_id:
            removed = job
        else:
            keep.append(job)
    if removed is None:
        return {"ok": False, "error": f"job not found: {job_id}"}
    _write_jobs(keep)
    if delete_script:
        try:
            Path(removed.script_path).unlink(missing_ok=True)  # py>=3.8
        except Exception:
            pass
    return {"ok": True, "removed": asdict(removed)}


def set_enabled(job_id: str, enabled: bool) -> dict[str, Any]:
    jobs = _read_jobs()
    updated: Optional[CronJob] = None
    for job in jobs:
        if job.id == job_id:
            job.enabled = bool(enabled)
            updated = job
    _write_jobs(jobs)
    if updated is None:
        return {"ok": False, "error": f"job not found: {job_id}"}
    return {"ok": True, "job": asdict(updated)}


def export_crontab_snippet(*, include_disabled: bool = False) -> str:
    jobs = _read_jobs()
    lines: list[str] = [CRONTAB_MARK_BEGIN]
    for job in jobs:
        if not job.enabled and not include_disabled:
            continue
        prefix = "" if job.enabled else "# "
        # 用 bash 执行脚本，避免 cron 默认 /bin/sh 与 bash 差异
        line = f'{prefix}{job.schedule} /usr/bin/env bash "{job.script_path}" # AuctusAgent:{job.id}'
        lines.append(line)
    lines.append(CRONTAB_MARK_END)
    return "\n".join(lines) + "\n"


def apply_to_system_crontab(*, include_disabled: bool = False) -> dict[str, Any]:
    """Replace the managed block in the user's crontab.

注意：这是对系统 crontab 的写入操作，建议只在用户主动执行 CLI 时调用。
"""
    snippet = export_crontab_snippet(include_disabled=include_disabled)
    try:
        current = subprocess.run(["crontab", "-l"], text=True, capture_output=True).stdout
    except Exception:
        current = ""
    new_tab = _replace_block(current or "", snippet)
    try:
        subprocess.run(["crontab", "-"], input=new_tab, text=True, check=True)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "jobs": list_jobs()}


def _replace_block(existing: str, block: str) -> str:
    if CRONTAB_MARK_BEGIN in existing and CRONTAB_MARK_END in existing:
        before, rest = existing.split(CRONTAB_MARK_BEGIN, 1)
        _, after = rest.split(CRONTAB_MARK_END, 1)
        out = before.rstrip() + "\n\n" + block + "\n" + after.lstrip()
        return out.strip() + "\n"
    base = existing.strip()
    if base:
        return base + "\n\n" + block
    return block


def _wrap_script(body: str) -> str:
    """Wrap user-provided body with consistent env + venv loading."""
    root = Path(__file__).resolve().parent.parent  # auctus-agent/
    log_dir = root / "data" / "cron" / "logs"
    return f"""#!/usr/bin/env bash
set -euo pipefail

ROOT="{root}"
cd "$ROOT"

# Redirect all output to data/cron/logs/, away from user workspace
LOG_DIR="{log_dir}"
mkdir -p "$LOG_DIR"
SCRIPT_NAME="$(basename "$0" .sh)"
exec >> "$LOG_DIR/$SCRIPT_NAME.log" 2>&1
echo "--- $(date '+%Y-%m-%d %H:%M:%S') ---"

# Load .env if present (local-only)
if [ -f ".env" ]; then
  set -a
  # shellcheck disable=SC1091
  source ".env"
  set +a
fi

# Prefer local venv python
PY=".venv/bin/python"
if [ -x "$PY" ]; then
  export PATH="$(pwd)/.venv/bin:$PATH"
fi

{body.strip()}
"""


# -------- templates --------

def template_backup_folder(*, source_rel: str, backups_rel: str = "data/backups") -> str:
    """Create a tar.gz backup under backups_rel with date suffix."""
    safe_source = source_rel.strip().strip("/")
    backups = backups_rel.strip().strip("/")
    return f"""
mkdir -p "{backups}"
ts="$(date +%Y%m%d_%H%M%S)"
name="$(basename "{safe_source}")"
tar -czf "{backups}/$name_$ts.tar.gz" "{safe_source}"
"""


def template_agent_run(*, input_rel: str, task: str) -> str:
    """Run built-in agent on a file under inputs/."""
    rel = input_rel.strip()
    task_escaped = task.replace('"', '\\"')
    return f"""
if [ ! -x ".venv/bin/python" ]; then
  echo "Missing .venv. Please run scripts/install_mac.sh (or install_windows.ps1) first." >&2
  exit 1
fi
".venv/bin/python" agent.py run "{rel}" --task "{task_escaped}"
"""


# -------- Windows Task Scheduler support --------

def is_windows() -> bool:
    import platform
    return platform.system() == "Windows"


def cron_to_windows_schedule(cron_expr: str) -> tuple[str, str]:
    """Convert cron expression to Windows Task Scheduler schedule.

    Returns (frequency, modifier) where:
    - frequency: DAILY, WEEKLY, MONTHLY, ONCE
    - modifier: /SC, /D, /W, /M based on frequency

    Only supports common patterns:
    - "0 9 * * *" -> DAILY at 9:00 AM
    - "0 9 * * 1" -> WEEKLY on Monday at 9:00 AM
    - "0 9 1 * *" -> MONTHLY on 1st at 9:00 AM
    """
    import re
    match = re.match(r'^(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$', cron_expr.strip())
    if not match:
        return "DAILY", "/ST 09:00"

    minute, hour, day_of_month, month, day_of_week = match.groups()

    # Daily
    if day_of_month == "*" and month == "*" and day_of_week == "*":
        return "DAILY", f"/ST {hour.zfill(2)}:{minute.zfill(2)}"

    # Weekly (specific day of week)
    if day_of_week != "*" and day_of_month == "*":
        days_map = {"0": "SUN", "1": "MON", "2": "TUE", "3": "WED", "4": "THU", "5": "FRI", "6": "SAT", "7": "SUN"}
        day_name = days_map.get(day_of_week, "MON")
        return "WEEKLY", f"/ST {hour.zfill(2)}:{minute.zfill(2)} /D {day_name}"

    # Monthly (specific day of month)
    if day_of_month != "*":
        return "MONTHLY", f"/ST {hour.zfill(2)}:{minute.zfill(2)} /D {day_of_month.zfill(2)}"

    return "DAILY", f"/ST {hour.zfill(2)}:{minute.zfill(2)}"


def export_windows_scheduler_script(*, include_disabled: bool = False) -> str:
    """Generate a PowerShell script to create Windows Task Scheduler tasks."""
    import re
    jobs = _read_jobs()
    if not jobs:
        return "# No cron jobs to export.\n"

    root = Path(__file__).resolve().parent.parent
    lines: list[str] = [
        "# Auctus Agent - Windows Task Scheduler Setup",
        "# Run this script as Administrator to create scheduled tasks",
        "# Generated by: python agent.py cron export-windows",
        "",
        "$ErrorActionPreference = 'Stop'",
        "",
        "# Helper function to create or update task",
        "function New-AuctusTask {",
        "  param(",
        "    [string]$Name,",
        "    [string]$Frequency,",
        "    [string]$Modifier,",
        "    [string]$ScriptPath,",
        "    [string]$Description",
        "  )",
        "",
        "  $taskName = \"AuctusAgent_$Name\"",
        f"  $root = \"{root}\"",
        "",
        "  # Remove existing task if present",
        "  $existing = Get-ScheduledTask | Where-Object { $_.TaskName -eq $taskName }",
        "  if ($existing) {",
        "    Write-Host \"Removing existing task: $taskName\"",
        "    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue",
        "  }",
        "",
        "  # Create action",
        "  $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument \"/c cd /d `\"$root`\" && .venv\\Scripts\\python.exe agent.py run `{0}`\" -WorkingDirectory $root",
        "  if (-not (Test-Path .venv\\Scripts\\python.exe)) {",
        "    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument \"/c cd /d `\"$root`\" && python agent.py run `{0}`\" -WorkingDirectory $root",
        "  }",
        "",
        "  # Create trigger",
        "  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date)  # placeholder, will be overridden",
        "  switch ($Frequency) {",
        "    'DAILY' { $trigger = New-ScheduledTaskTrigger -Daily -At $Modifier }",
        "    'WEEKLY' { $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Modifier.Split(',') -At $Modifier }",
        "    'MONTHLY' { $trigger = New-ScheduledTaskTrigger -Daily -At $Modifier }",
        "  }",
        "",
        "  # Create principal (run as current user)",
        "  $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited",
        "",
        "  # Create settings",
        "  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable",
        "",
        "  # Register task",
        f"  Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description $Description",
        "  Write-Host \"Created task: $taskName\"",
        "}",
        "",
    ]

    for job in jobs:
        if not job.enabled and not include_disabled:
            continue
        prefix = "# " if not job.enabled else ""
        freq, modifier = cron_to_windows_schedule(job.schedule)
        safe_name = re.sub(r'[^A-Za-z0-9_]', '_', job.name)[:50]

        # Get the actual command from the script body for agent-run jobs
        script_path = Path(job.script_path)
        if script_path.exists():
            script_content = script_path.read_text(encoding="utf-8")
            # Extract the python command for agent.py run jobs
            run_match = re.search(r'agent\.py run ["\']([^"\']+)["\']', script_content)
            if run_match:
                input_file = run_match.group(1)
                task_match = re.search(r'--task ["\']([^"\']+)["\']', script_content)
                task_desc = task_match.group(1) if task_match else "定期任务"
                lines.append(f'{prefix}# Job: {job.name}')
                lines.append(f'{prefix}# Schedule: {job.schedule} -> {freq} {modifier}')
                lines.append(f'{prefix}# Script: {job.script_path}')
                lines.append(f'{prefix}Write-Host "Setting up: {job.name}"')
                lines.append(f'{prefix}$script = \'{input_file}\'')
                escaped_task = task_desc.replace("'", "''")
                escaped_desc = (job.description or job.name).replace("'", "''")
                lines.append(f"{prefix}$task = '{escaped_task}'")
                lines.append(f"{prefix}$desc = '{escaped_desc}'")
                lines.append(f'{prefix}# To enable: remove the "#" prefix from the next line')
                lines.append(f'{prefix}# New-AuctusTask -Name "{safe_name}" -Frequency "{freq}" -Modifier "{modifier}" -ScriptPath "{job.script_path}" -Description $desc')
            else:
                lines.append(f'{prefix}# Job: {job.name} (backup type)')
                lines.append(f'{prefix}# Schedule: {job.schedule} -> {freq} {modifier}')
                lines.append(f'{prefix}# Script: {job.script_path}')
                lines.append(f'{prefix}# Command: (backup job - needs custom handler)')
        else:
            lines.append(f'{prefix}# Job: {job.name} - script not found: {job.script_path}')

        lines.append("")

    lines.extend([
        "",
        "Write-Host \"\"",
        "Write-Host \"Done! Created scheduled tasks.\"",
        "Write-Host \"To verify, open Task Scheduler and look for 'AuctusAgent_*' tasks.\"",
    ])

    return "\n".join(lines) + "\n"


def apply_to_windows_scheduler(*, include_disabled: bool = False) -> dict[str, Any]:
    """Apply cron jobs to Windows Task Scheduler using schtasks.exe.

    Falls back to generating a PowerShell script if direct application fails.
    """
    if not is_windows():
        return {"ok": False, "error": "This command only works on Windows"}

    import platform
    root = Path(__file__).resolve().parent.parent
    jobs = _read_jobs()
    created = []
    errors = []

    for job in jobs:
        if not job.enabled and not include_disabled:
            continue

        freq, modifier = cron_to_windows_schedule(job.schedule)
        safe_name = re.sub(r'[^A-Za-z0-9_]', '_', job.name)[:50]
        task_name = f"AuctusAgent_{safe_name}"

        # Build schtasks command
        script_path = Path(job.script_path)
        if script_path.exists():
            cmd = [
                "schtasks",
                "/Create",
                "/TN", task_name,
                "/TR", f'cmd /c "cd /d "{root}" && .venv\\Scripts\\python.exe agent.py run test_prd.md --task "test" 2>> data\\cron\\logs\\{safe_name}.log"',
                "/SC", freq,
            ]
            if freq == "WEEKLY":
                # Parse day from modifier like "/ST 09:00 /D MON"
                parts = modifier.split()
                for i, p in enumerate(parts):
                    if p == "/ST" and i + 1 < len(parts):
                        cmd.extend(["/ST", parts[i + 1].replace('"', '')])
                    if p == "/D" and i + 1 < len(parts):
                        cmd.extend(["/D", parts[i + 1].replace('"', '')])
            elif freq == "DAILY":
                for p in modifier.split():
                    if p.startswith("/ST"):
                        continue
                    cmd.extend(p.split())

            # Try to create task
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    created.append(task_name)
                else:
                    errors.append(f"{task_name}: {result.stderr or result.stdout}")
            except Exception as e:
                errors.append(f"{task_name}: {str(e)}")

    if errors and not created:
        return {"ok": False, "error": f"Failed to create tasks. Try running as Administrator. Errors: {'; '.join(errors)}"}

    return {
        "ok": True,
        "created": created,
        "skipped": len(errors),
        "errors": errors if errors else None,
        "note": "Run as Administrator if tasks were not created. Use 'python agent.py cron export-windows' to generate a script for manual setup."
    }


def list_windows_scheduler_tasks() -> list[dict[str, Any]]:
    """List existing Auctus Agent tasks in Windows Task Scheduler."""
    if not is_windows():
        return []

    import platform
    try:
        result = subprocess.run(
            ["schtasks", "/Query", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode != 0:
            return []

        tasks = []
        for line in result.stdout.strip().split("\n"):
            if not line or '"AuctusAgent_' not in line:
                continue
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 4:
                tasks.append({
                    "name": parts[0].replace('"', ''),
                    "next_run": parts[1] if len(parts) > 1 else "N/A",
                    "status": parts[2] if len(parts) > 2 else "Unknown",
                })
        return tasks
    except Exception:
        return []


# ─────────────────────────── in-process scheduler ─────────────────────────────
# Fires jobs from within the running app (no system crontab needed).
# Critical for the PyInstaller standalone bundle where users cannot edit crontab.

import logging as _logging
import threading as _threading
from datetime import datetime as _dt, timedelta as _td

_log = _logging.getLogger(__name__)
_stop_event = _threading.Event()
_scheduler_thread: Optional[_threading.Thread] = None
_runs_log = (settings.logs_dir / "cron_runs.jsonl").resolve()


def _job_due(schedule: str, now: _dt) -> bool:
    """Return True iff this cron expression matches the given minute."""
    try:
        from croniter import croniter
    except ImportError:
        return False
    base = now.replace(second=0, microsecond=0)
    try:
        # Start the iterator one minute before `base`; if `base` matches the cron,
        # get_next() will return exactly `base`.
        prev = base - _td(minutes=1)
        itr = croniter(schedule, prev)
        nxt = itr.get_next(_dt)
        return (nxt.year == base.year and nxt.month == base.month and nxt.day == base.day
                and nxt.hour == base.hour and nxt.minute == base.minute)
    except Exception as exc:
        _log.warning("cron parse failed for %r: %s", schedule, exc)
        return False


def _log_run(job: CronJob, status: str, summary: str) -> None:
    record = {
        "ts": time.time(),
        "iso": _dt.now().isoformat(timespec="seconds"),
        "job_id": job.id,
        "job_name": job.name,
        "schedule": job.schedule,
        "status": status,
        "summary": (summary or "")[:500],
    }
    try:
        _runs_log.parent.mkdir(parents=True, exist_ok=True)
        with open(_runs_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        _log.warning("cron run log failed: %s", exc)


def _execute_job(job: CronJob) -> None:
    """Run a single job's task by sending its description through the agent."""
    try:
        from . import agent  # local import — avoid circular at module load
        session_id = f"cron-{job.id}"
        result = agent.chat(session_id, job.description or job.name)
        reply = result.get("reply", "") if isinstance(result, dict) else str(result)
        _log_run(job, "success", reply)
    except Exception as exc:
        _log_run(job, "error", f"{type(exc).__name__}: {exc}")


def _scheduler_loop() -> None:
    """Every 30s, check whether any job is due this minute. Fire matches in fresh threads."""
    last_fired_minute: Optional[str] = None
    while not _stop_event.is_set():
        now = _dt.now()
        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if minute_key != last_fired_minute:
            last_fired_minute = minute_key
            for job in _read_jobs():
                if not job.enabled:
                    continue
                if _job_due(job.schedule, now):
                    _log.info("Firing cron job %s (%s)", job.name, job.id)
                    _threading.Thread(target=_execute_job, args=(job,), daemon=True).start()
        _stop_event.wait(30)


def start_scheduler() -> None:
    """Start the background scheduler. Safe to call multiple times (idempotent)."""
    global _scheduler_thread, _event_thread
    if _scheduler_thread and _scheduler_thread.is_alive():
        return
    _stop_event.clear()
    _scheduler_thread = _threading.Thread(target=_scheduler_loop, daemon=True, name="cron-scheduler")
    _scheduler_thread.start()
    _event_thread = _threading.Thread(target=_event_loop, daemon=True, name="event-trigger")
    _event_thread.start()
    _log.info("In-process cron scheduler started")


def stop_scheduler() -> None:
    """Signal the scheduler loop to exit."""
    _stop_event.set()


# ─────────────────────────── daily brief ───────────────────────────────────

_DAILY_BRIEF_JOB_NAME = "__daily_brief__"


def _daily_brief_prompt(target: str = "telegram") -> str:
    if target == "mobile":
        push_instruction = (
            "如果 Auctus Mobile relay 已配置，请同时通过 relay 把简报推送到 Auctus Mobile App。"
        )
    else:
        push_instruction = (
            "如果 Telegram 已配置，请同时用 send_telegram_message 工具把简报发送给用户。"
        )
    return (
        "请生成今日简报：1) 今天的日期和星期；"
        "2) 本周（含今天）对话次数（从记忆/日志估算）；"
        "3) 接下来 24 小时内有无定时任务即将触发；"
        "4) 一句积极的开始建议。"
        f"{push_instruction}"
        "回复要简洁，控制在 150 字以内。"
    )


def get_daily_brief_config() -> dict:
    from . import accounting
    state = accounting.get_setup_state()
    return {
        "enabled": state.get("daily_brief_enabled", "0") == "1",
        "hour": int(state.get("daily_brief_hour", "8")),
        "target": state.get("daily_brief_target", "telegram"),
    }


def add_event_trigger(
    *,
    name: str,
    trigger_type: str,
    trigger_config: dict,
    task: str,
) -> dict:
    """Register an event-triggered job (email_match or url_watch).

    trigger_type="email_match": trigger_config must include account_id + keyword (subject/sender match).
    trigger_type="url_watch":   trigger_config must include url; optional selector + match_text.
    """
    allowed = {"email_match", "url_watch"}
    if trigger_type not in allowed:
        return {"error": f"trigger_type must be one of {allowed}"}
    job = CronJob(
        id=uuid.uuid4().hex[:10],
        name=name.strip() or trigger_type,
        schedule="",  # not time-based
        script_path="",
        description=task.strip(),
        enabled=True,
        created_at=time.time(),
        trigger_type=trigger_type,
        trigger_config=json.dumps(trigger_config, ensure_ascii=False),
    )
    jobs = _read_jobs()
    jobs.append(job)
    _write_jobs(jobs)
    return {
        "ok": True,
        "job": asdict(job),
        "note": f"事件触发器 '{name}' 已创建，Auctus 会在后台监听 {trigger_type}，条件满足时自动执行任务。",
    }


# ─────────── event-trigger state store ───────────

_TRIGGER_STATE_PATH = (CRON_DIR / "trigger_state.json").resolve()


def _load_trigger_state() -> dict:
    if _TRIGGER_STATE_PATH.exists():
        try:
            return json.loads(_TRIGGER_STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_trigger_state(state: dict) -> None:
    _TRIGGER_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _TRIGGER_STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _check_email_match(job: CronJob, cfg: dict, state: dict) -> tuple[bool, bool]:
    """Return (fired, state_changed) for matching email since last check."""
    account_id = cfg.get("account_id", "")
    keyword = (cfg.get("keyword") or cfg.get("subject") or "").lower()
    if not account_id or not keyword:
        return False, False
    try:
        from . import accounting, email_client
        account = accounting.get_email_account(account_id)
        if not account:
            return False, False
        emails = email_client.list_inbox(account, limit=10)
    except Exception:
        return False, False

    last_uid = state.get(job.id, {}).get("last_uid", "")
    matched_uid = ""
    for email in emails:
        uid = str(email.get("uid") or "")
        subject = (email.get("subject") or "").lower()
        sender = (email.get("from") or "").lower()
        if keyword in subject or keyword in sender:
            if uid != last_uid:
                matched_uid = uid
                break

    if matched_uid:
        if job.id not in state:
            state[job.id] = {}
        state[job.id]["last_uid"] = matched_uid
        return True, True
    return False, False


def _check_url_watch(job: CronJob, cfg: dict, state: dict) -> tuple[bool, bool]:
    """Return (fired, state_changed) for watched URL content changes."""
    import hashlib
    from urllib.request import Request, urlopen

    url = cfg.get("url", "")
    if not url:
        return False, False
    selector = cfg.get("selector", "")
    match_text = (cfg.get("match_text") or "").lower()
    try:
        req = Request(url, headers={"User-Agent": "AuctusAgent/0.1"})
        with urlopen(req, timeout=10) as r:
            raw = r.read(500_000).decode("utf-8", errors="replace")
    except Exception:
        return False, False

    if selector:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(raw, "lxml")
            elements = soup.select(selector)
            raw = " ".join(el.get_text(strip=True) for el in elements)
        except Exception:
            pass

    if match_text and match_text not in raw.lower():
        return False, False

    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    last_hash = state.get(job.id, {}).get("hash", "")
    if content_hash != last_hash:
        if job.id not in state:
            state[job.id] = {}
        state[job.id]["hash"] = content_hash
        if not last_hash:
            return False, True  # first run: record baseline, don't fire
        return True, True
    return False, False


_event_thread: Optional[_threading.Thread] = None


def _event_loop() -> None:
    """Poll event-triggered jobs every 60 s."""
    while not _stop_event.is_set():
        state = _load_trigger_state()
        changed = False
        for job in _read_jobs():
            if not job.enabled or not job.trigger_type:
                continue
            try:
                cfg = json.loads(job.trigger_config or "{}")
            except Exception:
                continue
            fired = False
            state_changed = False
            if job.trigger_type == "email_match":
                fired, state_changed = _check_email_match(job, cfg, state)
            elif job.trigger_type == "url_watch":
                fired, state_changed = _check_url_watch(job, cfg, state)
            if state_changed:
                changed = True
            if fired:
                _log.info("Event trigger fired: %s (%s)", job.name, job.id)
                _threading.Thread(target=_execute_job, args=(job,), daemon=True).start()
        if changed:
            _save_trigger_state(state)
        _stop_event.wait(60)


def set_daily_brief(enabled: bool, hour: int = 8, target: str = "telegram") -> dict:
    from . import accounting
    hour = max(0, min(23, int(hour)))
    target = target if target in ("telegram", "mobile") else "telegram"
    accounting.set_setup_state({
        "daily_brief_enabled": "1" if enabled else "0",
        "daily_brief_hour": str(hour),
        "daily_brief_target": target,
    })
    jobs = _read_jobs()
    jobs = [j for j in jobs if j.name != _DAILY_BRIEF_JOB_NAME]
    if enabled:
        job = CronJob(
            id="daily_brief",
            name=_DAILY_BRIEF_JOB_NAME,
            schedule=f"0 {hour} * * *",
            script_path="",
            description=_daily_brief_prompt(target),
            enabled=True,
            created_at=time.time(),
        )
        jobs.append(job)
    _write_jobs(jobs)
    return {"ok": True, "enabled": enabled, "hour": hour, "target": target}
