"""Evaluation framework for closed learning loop.

Generates evaluation sets from real traces, runs automated evaluation
by actually invoking the Agent, and produces metrics for skill/prompt
optimization.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from .. import llm
from .. import accounting


@dataclass
class EvalCase:
    """A single evaluation case derived from real task traces."""
    case_id: str
    input_message: str
    setup_files: list[str]
    setup_permissions: dict[str, Any]
    expected_tools: list[str]
    expected_files: list[str]
    forbidden_patterns: list[str]  # e.g., ["rm -rf", "delete"]
    max_risk_events: int
    max_token_budget: int
    source_trace_id: str
    created_at: float


@dataclass
class EvalRun:
    """Result of running an evaluation case."""
    run_id: str
    case_id: str
    variant_id: str  # "baseline" or skill/prompt variant
    success: bool
    tools_used: list[str]
    files_created: list[str]
    risk_events: int
    tokens_used: int
    duration_ms: int
    error: Optional[str]
    run_at: float


class EvalSet:
    """A collection of evaluation cases for a specific optimization target."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.cases: list[EvalCase] = []
        self.created_at = time.time()

    def add_case(self, case: EvalCase) -> None:
        self.cases.append(case)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at,
            "cases": [asdict(c) for c in self.cases],
        }

    def save(self, path: Optional[Path] = None) -> Path:
        target = path or (settings.data_dir / "evolve" / "eval" / f"{self.name}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: Path) -> "EvalSet":
        data = json.loads(path.read_text(encoding="utf-8"))
        es = cls(data["name"], data.get("description", ""))
        es.created_at = data.get("created_at", time.time())
        for c in data.get("cases", []):
            es.cases.append(EvalCase(**c))
        return es


def _db_path() -> Path:
    return settings.data_dir / "secretary.db"


def _recent_traces(limit: int = 100) -> list[dict]:
    """Fetch recent successful task traces from tool logs."""
    from .. import evolution as _evo
    logs = _evo._recent_tool_logs(limit * 2)

    # Group by task_id to form traces
    traces: dict[str, list[dict]] = {}
    for log in logs:
        tid = log.get("task_id", "unknown")
        if tid not in traces:
            traces[tid] = []
        traces[tid].append(log)

    # Build trace summaries
    results = []
    for tid, items in list(traces.items())[:limit]:
        if not items:
            continue

        # Only include successful traces
        if any(i.get("status") == "error" for i in items):
            continue

        tools_used = [i.get("tool_name") for i in items if i.get("tool_name")]
        files_created = []
        for i in items:
            out = i.get("tool_output") or {}
            if isinstance(out, dict):
                files_created.extend(out.get("files", []))

        results.append({
            "trace_id": tid,
            "user_input": items[0].get("user_input", "") if items else "",
            "tools_used": list(set(tools_used)),
            "files_created": list(set(files_created)),
            "risk_level": max(
                [i.get("risk_level", "low") for i in items],
                key=lambda x: {"low": 0, "medium": 1, "high": 2}.get(x, 0),
            ),
            "token_usage": sum(
                (i.get("token_usage") or {}).get("total_tokens", 0)
                for i in items
            ),
        })

    return results


def build_eval_set_from_traces(
    name: str = "weekly_eval",
    description: str = "Auto-generated from recent successful traces",
    limit: int = 50,
    min_confidence: float = 0.7,
) -> EvalSet:
    """Generate an evaluation set from recent task traces.

    Uses LLM to analyze traces and generate appropriate test cases.
    """
    traces = _recent_traces(limit)
    if not traces:
        return EvalSet(name, description + " (empty: no traces)")

    es = EvalSet(name, description)

    prompt = f"""你是一个测试用例生成器。请基于以下真实任务记录，生成评估用例（Eval Cases）。

每条记录包含：用户输入、使用的工具、生成的文件。

请输出 JSON 数组，格式如下：
{{
  "input_message": "模拟用户输入（基于真实记录改写）",
  "expected_tools": ["期望调用的工具列表"],
  "expected_files": ["期望生成的文件类型或路径模式"],
  "forbidden_patterns": ["不应该出现的危险操作，如 rm -rf"],
  "max_risk_events": 0,
  "max_token_budget": 8000
}}

要求：
1. 只选择成功完成的任务
2. 覆盖不同类型：文件处理、报告生成、表格生成等
3. 包含明确的期望结果，便于自动评估
4. 标记危险操作作为 forbidden_patterns

真实任务记录（{len(traces)} 条）：
{json.dumps(traces[:30], ensure_ascii=False, indent=2)}
"""

    try:
        resp = llm.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        raw = resp["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1]

        items = json.loads(raw)
        if not isinstance(items, list):
            items = [items]

        for item in items:
            if not isinstance(item, dict):
                continue
            case = EvalCase(
                case_id=f"evc_{uuid.uuid4().hex[:8]}",
                input_message=item.get("input_message", ""),
                setup_files=item.get("setup_files", []),
                setup_permissions=item.get("setup_permissions", {}),
                expected_tools=item.get("expected_tools", []),
                expected_files=item.get("expected_files", []),
                forbidden_patterns=item.get("forbidden_patterns", []),
                max_risk_events=item.get("max_risk_events", 0),
                max_token_budget=item.get("max_token_budget", 8000),
                source_trace_id=item.get("source_trace_id", ""),
                created_at=time.time(),
            )
            es.add_case(case)

    except Exception:
        # Fallback: create simple cases from traces directly
        for trace in traces[:10]:
            case = EvalCase(
                case_id=f"evc_{uuid.uuid4().hex[:8]}",
                input_message=trace["user_input"],
                setup_files=[],
                setup_permissions={},
                expected_tools=trace["tools_used"],
                expected_files=trace["files_created"],
                forbidden_patterns=["rm -rf", "delete", "remove"],
                max_risk_events=0 if trace["risk_level"] == "low" else 1,
                max_token_budget=10000,
                source_trace_id=trace["trace_id"],
                created_at=time.time(),
            )
            es.add_case(case)

    return es


# ─── Real evaluation ────────────────────────────────────────────────

def _read_eval_logs(session_id: str) -> list[dict]:
    """Read all tool-call log entries for an eval session."""
    log_path = settings.logs_dir / "tool_calls.jsonl"
    if not log_path.exists():
        return []

    entries: list[dict] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("task_id") == session_id:
            entries.append(entry)
    return entries


def _setup_eval_workspace(case: EvalCase) -> Path:
    """Create an isolated workspace for the eval case. Returns the workspace dir."""
    work_dir = Path(tempfile.mkdtemp(prefix=f"eval_{case.case_id}_"))
    inputs_dir = work_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)

    # Copy any setup files into the workspace
    for f in case.setup_files:
        src = Path(f)
        if src.exists():
            dst = inputs_dir / src.name
            if src.is_file():
                shutil.copy2(src, dst)
            elif src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)

    return work_dir


def _check_forbidden(content: str, patterns: list[str]) -> list[str]:
    """Return which forbidden patterns are present in content."""
    found: list[str] = []
    for pat in patterns:
        if re.search(pat, content, re.IGNORECASE):
            found.append(pat)
    return found


def run_eval_case(
    case: EvalCase,
    variant_id: str = "baseline",
    timeout_seconds: int = 180,
) -> EvalRun:
    """Execute a single evaluation case by actually running the Agent.

    This now performs a real agent invocation:
    1. Sets up an isolated workspace with any required files
    2. Sends the case.input_message to the agent
    3. Captures tool calls, output files, and errors
    4. Compares results against expectations
    5. Cleans up the temporary workspace

    Returns an EvalRun with real metrics.
    """
    from ..agent import chat as agent_chat

    run_id = f"run_{uuid.uuid4().hex[:8]}"
    session_id = f"eval_{case.case_id}_{int(time.time())}"
    start = time.time()

    # Save original settings to restore later
    orig_output_dir = settings.output_dir
    orig_workspace_dir = settings.workspace_dir

    work_dir: Optional[Path] = None
    try:
        # Set up isolated workspace
        work_dir = _setup_eval_workspace(case)
        settings.output_dir = work_dir / "outputs"
        settings.workspace_dir = work_dir / "inputs"
        settings.output_dir.mkdir(parents=True, exist_ok=True)

        # Run the agent
        try:
            result = agent_chat(session_id, case.input_message)
        except Exception as e:
            # Agent execution failed entirely
            run = EvalRun(
                run_id=run_id,
                case_id=case.case_id,
                variant_id=variant_id,
                success=False,
                tools_used=[],
                files_created=[],
                risk_events=0,
                tokens_used=0,
                duration_ms=int((time.time() - start) * 1000),
                error=f"Agent execution error: {e}",
                run_at=time.time(),
            )
            return run

        # Collect real tool logs for this session
        entries = _read_eval_logs(session_id)

        tools_used: list[str] = []
        files_created: list[str] = []
        risk_events = 0
        tokens_used = 0
        errors: list[str] = []
        all_output: list[str] = []  # for forbidden pattern check

        for entry in entries:
            tool_name = entry.get("tool_name", "")
            if tool_name:
                tools_used.append(tool_name)

            status = entry.get("status", "")
            risk = entry.get("risk_level", "low")
            if status == "error":
                errors.append(entry.get("error", "unknown error"))
            if risk in ("medium", "high"):
                risk_events += 1

            tok = entry.get("token_usage") or {}
            tokens_used += tok.get("total_tokens", 0)

            # Collect output files
            out = entry.get("tool_output")
            if isinstance(out, dict):
                for key in ("path", "file", "files"):
                    val = out.get(key)
                    if isinstance(val, str):
                        files_created.append(val)
                    elif isinstance(val, list):
                        files_created.extend(str(v) for v in val)
                # Also check for file paths in output content
                content = out.get("content", "")
                if isinstance(content, str):
                    all_output.append(content)

            # Check tool_input for forbidden patterns too
            inp = entry.get("tool_input")
            if isinstance(inp, dict):
                for v in inp.values():
                    if isinstance(v, str):
                        all_output.append(v)

        # Also check result files
        for f in result.get("files", []):
            if f not in files_created:
                files_created.append(f)

        # Check reply for forbidden patterns
        all_output.append(result.get("reply", ""))

        # Evaluate success against expectations
        forbidden_found = _check_forbidden(
            " ".join(all_output),
            case.forbidden_patterns,
        )

        # Check expected tools (at least one expected tool was used, or no expectations)
        tools_ok = True
        if case.expected_tools:
            tools_ok = any(t in tools_used for t in case.expected_tools)

        # Check expected files (at least one expected file pattern appears)
        files_ok = True
        if case.expected_files:
            files_ok = any(
                any(pattern.lower() in f.lower() for pattern in case.expected_files)
                for f in files_created
            )

        risk_ok = risk_events <= case.max_risk_events
        forbidden_ok = len(forbidden_found) == 0
        token_ok = tokens_used <= case.max_token_budget
        error_ok = len(errors) == 0

        success = all([
            result.get("reply") is not None,
            tools_ok,
            files_ok,
            risk_ok,
            forbidden_ok,
            token_ok,
            error_ok,
        ])

        error_msg = None
        if not success:
            reasons: list[str] = []
            if not tools_ok:
                reasons.append(f"expected tools {case.expected_tools}, used {list(set(tools_used))}")
            if not files_ok:
                reasons.append(f"expected files {case.expected_files}, created {files_created}")
            if not risk_ok:
                reasons.append(f"risk events {risk_events} > max {case.max_risk_events}")
            if not forbidden_ok:
                reasons.append(f"forbidden patterns found: {forbidden_found}")
            if not token_ok:
                reasons.append(f"tokens {tokens_used} > budget {case.max_token_budget}")
            if not error_ok:
                reasons.append(f"errors: {errors}")
            if result.get("reply") is None:
                reasons.append("no reply generated")
            error_msg = "; ".join(reasons)

        run = EvalRun(
            run_id=run_id,
            case_id=case.case_id,
            variant_id=variant_id,
            success=success,
            tools_used=list(set(tools_used)),
            files_created=files_created,
            risk_events=risk_events,
            tokens_used=tokens_used,
            duration_ms=int((time.time() - start) * 1000),
            error=error_msg,
            run_at=time.time(),
        )

        return run

    finally:
        # Restore original settings
        settings.output_dir = orig_output_dir
        settings.workspace_dir = orig_workspace_dir

        # Clean up temporary workspace
        if work_dir and work_dir.exists():
            try:
                shutil.rmtree(work_dir)
            except OSError:
                pass  # best-effort cleanup


def run_eval_set(
    eval_set: EvalSet,
    variant_id: str = "baseline",
) -> dict[str, Any]:
    """Run all cases in an evaluation set and compute metrics."""
    runs: list[EvalRun] = []

    for case in eval_set.cases:
        run = run_eval_case(case, variant_id)
        runs.append(run)

    # Compute metrics
    total = len(runs)
    successes = sum(1 for r in runs if r.success)
    total_tokens = sum(r.tokens_used for r in runs)
    total_risks = sum(r.risk_events for r in runs)
    avg_duration = sum(r.duration_ms for r in runs) / max(total, 1)

    return {
        "eval_set": eval_set.name,
        "variant_id": variant_id,
        "total_cases": total,
        "success_count": successes,
        "success_rate": successes / max(total, 1),
        "total_tokens": total_tokens,
        "avg_tokens": total_tokens / max(total, 1),
        "total_risk_events": total_risks,
        "avg_duration_ms": avg_duration,
        "runs": [asdict(r) for r in runs],
    }


def compare_variants(
    eval_set: EvalSet,
    variants: list[str],
) -> dict[str, Any]:
    """Compare multiple variants (skills/prompts) against the same eval set."""
    results = {}
    for variant in variants:
        results[variant] = run_eval_set(eval_set, variant)

    # Find best variant
    best = max(results.items(), key=lambda x: x[1]["success_rate"])

    return {
        "eval_set": eval_set.name,
        "variants": results,
        "best_variant": best[0],
        "best_success_rate": best[1]["success_rate"],
    }


# CLI helpers
def cli_build_eval(name: str, from_traces: bool = True) -> dict:
    """CLI entry: build evaluation set."""
    if from_traces:
        es = build_eval_set_from_traces(name)
    else:
        es = EvalSet(name, "Manual eval set")

    path = es.save()
    return {
        "ok": True,
        "path": str(path),
        "name": es.name,
        "cases": len(es.cases),
    }


def cli_run_eval(name: str, variant: str = "baseline") -> dict:
    """CLI entry: run evaluation."""
    path = settings.data_dir / "evolve" / "eval" / f"{name}.json"
    if not path.exists():
        return {"ok": False, "error": f"Eval set not found: {name}"}

    es = EvalSet.load(path)
    result = run_eval_set(es, variant)

    # Save results
    result_path = settings.data_dir / "evolve" / "eval" / f"{name}_{variant}_{int(time.time())}.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "result": result,
        "result_path": str(result_path),
    }
