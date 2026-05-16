"""GEPA (Generate-Evaluate-Prune-Apply) optimization pipeline.

This module implements the deep optimization loop for the closed learning system:
1. Generate variants of skills/prompts/configs
2. Evaluate variants against the eval set
3. Prune/select the best variant
4. Apply the winner (with human approval)
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional

from ..config import settings
from .. import llm
from . import eval as eval_mod


@dataclass
class Variant:
    """A variant of a skill, prompt, or config."""
    variant_id: str
    target_type: str  # "skill", "prompt", "tool_policy", "config"
    target_id: str  # base skill/prompt ID
    patch: str  # diff or new content
    description: str
    created_at: float


@dataclass
class Experiment:
    """An experiment comparing multiple variants."""
    exp_id: str
    name: str
    target_type: str
    base_id: str
    variants: list[Variant]
    results: dict[str, Any]  # variant_id -> eval results
    winner_id: Optional[str]
    status: str  # "running", "completed", "applied", "rejected"
    created_at: float
    completed_at: Optional[float]


def _variant_system_context(variant: Variant) -> str:
    """Build a compact system-prompt injection for a variant under test.

    For skill variants the patch is {"level0": ..., "level1": ...}.
    For prompt variants the patch is the raw replacement prompt text.
    """
    if variant.target_type == "skill":
        try:
            patch = json.loads(variant.patch) if isinstance(variant.patch, str) else variant.patch
        except (json.JSONDecodeError, TypeError):
            return ""
        level0 = (patch or {}).get("level0", "")
        level1 = (patch or {}).get("level1", "")
        return (
            "[GEPA 实验 — 临时技能变体]\n"
            f"请优先使用以下技能版本来完成任务（实验 ID: {variant.variant_id}）：\n"
            f"步骤：\n{level0}\n"
            + (f"\n详细说明：\n{level1}" if level1 else "")
            + "\n如果该变体不适用，则回退到默认行为。"
        )
    if variant.target_type == "prompt":
        return (
            "[GEPA 实验 — 临时系统提示变体]\n"
            f"以下为本次评估使用的替代系统提示（实验 ID: {variant.variant_id}）：\n"
            f"{variant.patch[:3000]}"
        )
    if variant.target_type in ("tool_policy", "config"):
        return (
            "[GEPA 实验 — 临时配置变体]\n"
            f"实验 ID: {variant.variant_id}\n"
            f"描述: {variant.description}\n"
            f"策略调整: {variant.patch[:2000]}"
        )
    return ""


class VariantGenerator:
    """Generate variants of skills, prompts, or configs using LLM."""

    def __init__(self):
        self.variants_dir = settings.data_dir / "evolve" / "experiments"
        self.variants_dir.mkdir(parents=True, exist_ok=True)

    def generate_skill_variants(self, base_skill_id: str, count: int = 3) -> list[Variant]:
        """Generate variants of a skill by rewriting/refining it."""
        from . import skills as skills_mod
        skill = skills_mod.get_skill(base_skill_id)
        if not skill:
            return []

        level0 = skill.get("level0", "")
        level1 = skill.get("level1", "")
        name = skill.get("name", "")

        prompt = f"""你是一个技能优化专家。请基于以下技能文档，生成 {count} 个改进版本。

原始技能：{name}

L0（核心步骤）：
{level0}

L1（详细说明）：
{level1 or "（无）"}

请输出 JSON 数组，每个元素包含：
{{
  "description": "改进说明",
  "level0": "改进后的 L0",
  "level1": "改进后的 L1（可选）"
}}

改进方向：
1. 让步骤更清晰、更具体
2. 添加常见陷阱和注意事项
3. 优化关键词匹配
4. 保持简洁但信息完整
"""

        try:
            resp = llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )
            raw = resp["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1]

            items = json.loads(raw)
            if not isinstance(items, list):
                items = [items]

            variants = []
            for i, item in enumerate(items[:count]):
                v = Variant(
                    variant_id=f"v_{base_skill_id}_{i+1}_{int(time.time())}",
                    target_type="skill",
                    target_id=base_skill_id,
                    patch=json.dumps({
                        "level0": item.get("level0", level0),
                        "level1": item.get("level1", level1),
                    }, ensure_ascii=False),
                    description=item.get("description", f"Variant {i+1}"),
                    created_at=time.time(),
                )
                variants.append(v)

            return variants
        except Exception:
            return []

    def generate_prompt_variants(self, base_prompt: str, count: int = 3) -> list[Variant]:
        """Generate variants of a system prompt."""
        prompt = f"""你是一个提示词工程专家。请基于以下系统提示，生成 {count} 个改进版本。

原始提示：
{base_prompt[:2000]}  # Truncate for token limit

请输出 JSON 数组，每个元素包含：
{{
  "description": "改进说明",
  "prompt": "改进后的完整提示"
}}

改进方向：
1. 让指令更清晰、更结构化
2. 添加更多示例（few-shot）
3. 优化输出格式说明
4. 减少歧义和模糊表述
"""

        try:
            resp = llm.chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
            )
            raw = resp["choices"][0]["message"]["content"].strip()
            if raw.startswith("```"):
                parts = raw.split("```")
                raw = parts[1][4:] if len(parts) > 1 and parts[1].startswith("json") else parts[1]

            items = json.loads(raw)
            if not isinstance(items, list):
                items = [items]

            variants = []
            for i, item in enumerate(items[:count]):
                v = Variant(
                    variant_id=f"v_prompt_{i+1}_{int(time.time())}",
                    target_type="prompt",
                    target_id="system_prompt",
                    patch=item.get("prompt", base_prompt),
                    description=item.get("description", f"Prompt variant {i+1}"),
                    created_at=time.time(),
                )
                variants.append(v)

            return variants
        except Exception:
            return []


class ExperimentRunner:
    """Run experiments to compare variants."""

    def __init__(self):
        self.experiments_dir = settings.data_dir / "evolve" / "experiments"
        self.experiments_dir.mkdir(parents=True, exist_ok=True)

    def create_experiment(
        self,
        name: str,
        target_type: str,
        base_id: str,
        variants: list[Variant],
    ) -> Experiment:
        """Create a new experiment."""
        exp = Experiment(
            exp_id=f"exp_{int(time.time())}_{uuid.uuid4().hex[:6]}",
            name=name,
            target_type=target_type,
            base_id=base_id,
            variants=variants,
            results={},
            winner_id=None,
            status="running",
            created_at=time.time(),
            completed_at=None,
        )
        self._save_experiment(exp)
        return exp

    def _save_experiment(self, exp: Experiment) -> None:
        """Save experiment to disk."""
        path = self.experiments_dir / f"{exp.exp_id}.json"
        path.write_text(json.dumps({
            "exp_id": exp.exp_id,
            "name": exp.name,
            "target_type": exp.target_type,
            "base_id": exp.base_id,
            "variants": [asdict(v) for v in exp.variants],
            "results": exp.results,
            "winner_id": exp.winner_id,
            "status": exp.status,
            "created_at": exp.created_at,
            "completed_at": exp.completed_at,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    def run_experiment(
        self,
        exp: Experiment,
        eval_set_name: str = "weekly_eval",
    ) -> Experiment:
        """Run the experiment: evaluate all variants with real agent execution.

        For each variant, temporarily injects the variant content into the
        agent's system prompt so the evaluation reflects the variant's effect.
        """
        from .. import evolution as _evo

        # Load or build eval set
        eval_path = settings.data_dir / "evolve" / "eval" / f"{eval_set_name}.json"
        if not eval_path.exists():
            eval_mod.cli_build_eval(eval_set_name)

        eval_set = eval_mod.EvalSet.load(eval_path)

        # Save original build_runtime_context so we can restore it
        _original_build = _evo.build_runtime_context

        # Evaluate baseline (original agent, no variant injection)
        baseline_result = eval_mod.run_eval_set(eval_set, "baseline")
        exp.results["baseline"] = baseline_result

        # Evaluate each variant with its content injected
        for variant in exp.variants:
            variant_context = _variant_system_context(variant)

            def _patched_build(user_text: str, path=None, limit: int = 8) -> str:
                base = _original_build(user_text, path=path, limit=limit)
                if variant_context:
                    return base + "\n\n" + variant_context if base else variant_context
                return base

            # Inject variant into runtime
            _evo.build_runtime_context = _patched_build  # type: ignore[assignment]

            try:
                result = eval_mod.run_eval_set(eval_set, variant.variant_id)
                exp.results[variant.variant_id] = result
            finally:
                _evo.build_runtime_context = _original_build  # type: ignore[assignment]

        # Select winner (best success_rate, tie-break on lower tokens)
        scored = sorted(
            [(vid, r) for vid, r in exp.results.items()],
            key=lambda x: (x[1]["success_rate"], -(x[1].get("total_tokens", 0))),
            reverse=True,
        )
        exp.winner_id = scored[0][0]
        exp.status = "completed"
        exp.completed_at = time.time()

        self._save_experiment(exp)
        return exp

    def load_experiment(self, exp_id: str) -> Optional[Experiment]:
        """Load experiment from disk."""
        path = self.experiments_dir / f"{exp_id}.json"
        if not path.exists():
            return None

        data = json.loads(path.read_text(encoding="utf-8"))
        exp = Experiment(
            exp_id=data["exp_id"],
            name=data["name"],
            target_type=data["target_type"],
            base_id=data["base_id"],
            variants=[Variant(**v) for v in data.get("variants", [])],
            results=data.get("results", {}),
            winner_id=data.get("winner_id"),
            status=data.get("status", "unknown"),
            created_at=data.get("created_at", 0),
            completed_at=data.get("completed_at"),
        )
        return exp

    def list_experiments(self) -> list[dict]:
        """List all experiments."""
        experiments = []
        for path in sorted(self.experiments_dir.glob("exp_*.json"), reverse=True):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                experiments.append({
                    "exp_id": data["exp_id"],
                    "name": data["name"],
                    "target_type": data["target_type"],
                    "status": data.get("status", "unknown"),
                    "winner_id": data.get("winner_id"),
                    "created_at": data.get("created_at", 0),
                })
            except Exception:
                continue
        return experiments


class PRGenerator:
    """Generate PR/patch for the winning variant."""

    def __init__(self):
        self.patches_dir = settings.data_dir / "evolve" / "patches"
        self.patches_dir.mkdir(parents=True, exist_ok=True)

    def generate_patch(
        self,
        exp: Experiment,
        output_format: str = "markdown",
    ) -> Path:
        """Generate a patch/report for the winning variant."""
        if not exp.winner_id:
            raise ValueError("Experiment has no winner yet")

        winner_result = exp.results.get(exp.winner_id, {})
        baseline_result = exp.results.get("baseline", {})

        report = f"""# GEPA 优化报告

## 实验信息

- **实验 ID**: {exp.exp_id}
- **名称**: {exp.name}
- **目标类型**: {exp.target_type}
- **基础版本**: {exp.base_id}
- **获胜变体**: {exp.winner_id}
- **完成时间**: {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp.completed_at or time.time()))}

## 结果对比

| 指标 | 基线 | 获胜变体 | 提升 |
|------|------|----------|------|
| 成功率 | {baseline_result.get('success_rate', 0)*100:.1f}% | {winner_result.get('success_rate', 0)*100:.1f}% | {(winner_result.get('success_rate', 0) - baseline_result.get('success_rate', 0))*100:+.1f}% |
| Token 用量 | {baseline_result.get('total_tokens', 0)} | {winner_result.get('total_tokens', 0)} | {winner_result.get('total_tokens', 0) - baseline_result.get('total_tokens', 0):+d} |
| 风险事件 | {baseline_result.get('total_risk_events', 0)} | {winner_result.get('total_risk_events', 0)} | {winner_result.get('total_risk_events', 0) - baseline_result.get('total_risk_events', 0):+d} |

## 所有变体结果

"""

        for variant_id, result in sorted(exp.results.items(), key=lambda x: x[1].get('success_rate', 0), reverse=True):
            marker = "🏆 " if variant_id == exp.winner_id else ""
            report += f"- {marker}**{variant_id}**: {result.get('success_rate', 0)*100:.1f}% 成功率\n"

        report += f"""

## 获胜变体详情

"""

        # Find winner variant details
        winner_variant = None
        for v in exp.variants:
            if v.variant_id == exp.winner_id:
                winner_variant = v
                break

        if winner_variant:
            report += f"""
**描述**: {winner_variant.description}

**改进内容**:
```json
{winner_variant.patch}
```
"""

        report += f"""

## 建议操作

1. 审查上述改进内容
2. 如果满意，运行以下命令应用：
   ```bash
   python agent.py evolve apply-winner {exp.exp_id}
   ```
3. 如果不满意，可以：
   - 重新运行实验（调整参数）
   - 手动编辑改进内容
   - 放弃本次实验

## 注意事项

- 所有变体在评估集上进行了测试
- 结果基于模拟执行，实际效果可能有所不同
- 建议在应用到生产环境前进行人工审查
"""

        # Save report
        report_path = self.patches_dir / f"{exp.exp_id}_report.md"
        report_path.write_text(report, encoding="utf-8")

        return report_path


# CLI helpers
def cli_gepa_run(
    target_type: str,
    target_id: str,
    eval_set: str = "weekly_eval",
    variant_count: int = 3,
) -> dict:
    """CLI entry: run GEPA optimization."""
    generator = VariantGenerator()
    runner = ExperimentRunner()
    pr_gen = PRGenerator()

    # Generate variants
    if target_type == "skill":
        variants = generator.generate_skill_variants(target_id, variant_count)
    elif target_type == "prompt":
        # Load current system prompt
        from ...prompts import system
        variants = generator.generate_prompt_variants(system.SYSTEM_PROMPT, variant_count)
    else:
        return {"ok": False, "error": f"Unsupported target type: {target_type}"}

    if not variants:
        return {"ok": False, "error": "Failed to generate variants"}

    # Create and run experiment
    exp = runner.create_experiment(
        name=f"Optimize {target_type} {target_id}",
        target_type=target_type,
        base_id=target_id,
        variants=variants,
    )

    exp = runner.run_experiment(exp, eval_set)

    # Generate report
    report_path = pr_gen.generate_patch(exp)

    return {
        "ok": True,
        "exp_id": exp.exp_id,
        "winner_id": exp.winner_id,
        "success_rate": exp.results.get(exp.winner_id, {}).get("success_rate", 0),
        "baseline_rate": exp.results.get("baseline", {}).get("success_rate", 0),
        "report_path": str(report_path),
        "variants_tested": len(variants) + 1,  # +1 for baseline
    }


def cli_list_experiments() -> list[dict]:
    """CLI entry: list experiments."""
    runner = ExperimentRunner()
    return runner.list_experiments()
