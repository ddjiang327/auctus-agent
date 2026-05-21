"""Lightweight in-memory Task Mode state for the local web UI."""
from __future__ import annotations

from datetime import datetime
from typing import Any
import json
import sqlite3
import time
import uuid
import re

from .config import settings


_TASKS: dict[str, dict[str, Any]] = {}
_UPDATE_MARKER_RE = re.compile(r"<!--\s*TASK_UPDATE\s+(\{.*?\})\s*-->", re.DOTALL)
_VALID_TASK_STATUSES = {"in_progress", "waiting", "completed", "ended"}
_VALID_STEP_STATUSES = {"pending", "in_progress", "completed"}
_VALID_ACTIVITY_KEYS = {"waiting", "advanced", "working", "artifact"}
_PLAN_FIELDS = {"missing_requirements", "success_criteria", "research_plan"}
_STEP_COUNT = 5


DEFAULT_STEPS = [
    "确认目标和必要条件",
    "建立比较标准",
    "收集候选方案和来源",
    "整理结构化对比",
    "给出建议和下一步",
]


def create_or_resume(session_id: str, goal: str) -> dict[str, Any]:
    task = _TASKS.get(session_id) or _load(session_id)
    if task and task.get("status") != "ended":
        cleaned_goal = goal.strip()
        if task.get("status") == "completed" and cleaned_goal and cleaned_goal != task.get("goal"):
            task = None
        elif cleaned_goal and cleaned_goal != task.get("goal"):
            task["activity"].append(_activity(f"收到新的补充目标：{goal[:120]}"))
            task["updated_at"] = _now()
            _save(task)
            return task
        else:
            return task

    task = {
        "id": uuid.uuid4().hex[:12],
        "session_id": session_id,
        "goal": goal.strip(),
        "status": "in_progress",
        "current_step": 0,
        "steps": [
            {"id": f"step_{idx + 1}", "title": title, "status": "in_progress" if idx == 0 else "pending"}
            for idx, title in enumerate(DEFAULT_STEPS)
        ],
        "plan": build_initial_plan(goal),
        "activity": [
            _activity("已进入任务模式", "entered"),
            _activity("正在把目标整理成可执行清单", "preparing"),
        ],
        "artifacts": {},
        "created_at": _now(),
        "updated_at": _now(),
    }
    _TASKS[session_id] = task
    _save(task)
    return task


def get(session_id: str) -> dict[str, Any] | None:
    return _TASKS.get(session_id) or _load(session_id)


def list_tasks(limit: int = 20) -> list[dict[str, Any]]:
    _ensure_store()
    limit = max(1, min(int(limit or 20), 100))
    with sqlite3.connect(_db_path()) as conn:
        rows = conn.execute(
            "SELECT session_id, payload, updated_at FROM tasks ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for session_id, payload, updated_at in rows:
        try:
            task = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if not isinstance(task, dict):
            continue
        artifacts = (task.get("artifacts") or {}).get("items") or []
        out.append({
            "session_id": session_id,
            "id": task.get("id"),
            "goal": task.get("goal") or "",
            "status": task.get("status") or "in_progress",
            "current_step": int(task.get("current_step") or 0),
            "artifact_count": len(artifacts) if isinstance(artifacts, list) else 0,
            "created_at": task.get("created_at") or "",
            "updated_at": task.get("updated_at") or "",
            "updated_ts": float(updated_at or 0),
        })
    return out


def mark_working(session_id: str, note: str) -> dict[str, Any] | None:
    task = _TASKS.get(session_id) or _load(session_id)
    if not task:
        return None
    task["status"] = "in_progress"
    task["activity"].append(_activity(note, "working"))
    task["activity"] = task["activity"][-12:]
    task["updated_at"] = _now()
    _save(task)
    return task


def add_activity(session_id: str, text: str, key: str | None = None) -> dict[str, Any] | None:
    task = _TASKS.get(session_id) or _load(session_id)
    if not task:
        return None
    task["activity"].append(_activity(text, key))
    task["activity"] = task["activity"][-16:]
    task["updated_at"] = _now()
    _save(task)
    return task


def mark_after_reply(session_id: str, reply: str) -> dict[str, Any] | None:
    task = _TASKS.get(session_id) or _load(session_id)
    if not task:
        return None
    text = (reply or "").strip()
    update = extract_update(text)
    if update:
        _apply_update(task, update)
        task["activity"].append(_activity("已根据显式任务更新同步阶段", update.get("activity_key") or "advanced"))
    else:
        _apply_inferred_update(task, text)
    artifacts = _extract_artifacts(text)
    if artifacts:
        _append_artifacts(task, artifacts)
        task["activity"].append(_activity("已更新任务产物", "artifact"))
    task["activity"] = task["activity"][-12:]
    task["updated_at"] = _now()
    _save(task)
    return task


def extract_update(reply: str) -> dict[str, Any] | None:
    match = _UPDATE_MARKER_RE.search(reply or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return _normalize_update(payload)


def strip_update_markers(reply: str) -> str:
    return _UPDATE_MARKER_RE.sub("", reply or "").strip()


def _apply_update(task: dict[str, Any], update: dict[str, Any]) -> None:
    idx = update.get("current_step")
    if isinstance(idx, int):
        # Marker uses human step numbers 1-5.
        _set_current_step(task, idx - 1)
    status = update.get("status")
    if status in _VALID_TASK_STATUSES:
        task["status"] = "in_progress" if status == "waiting" else status
    steps = task.get("steps", [])
    step_statuses = update.get("steps")
    if isinstance(step_statuses, list):
        for pos, step_status in enumerate(step_statuses[: len(steps)]):
            if step_status in _VALID_STEP_STATUSES:
                steps[pos]["status"] = step_status
                if step_status == "in_progress":
                    task["current_step"] = pos
    artifacts = update.get("artifacts")
    if isinstance(artifacts, list):
        cleaned = [
            item for item in artifacts
            if isinstance(item, dict) and item.get("type") and (item.get("title") or item.get("content"))
        ]
        if cleaned:
            _append_artifacts(task, cleaned)
    plan_update = update.get("plan")
    if isinstance(plan_update, dict):
        _merge_plan(task, plan_update)


def _normalize_update(payload: dict[str, Any]) -> dict[str, Any] | None:
    update: dict[str, Any] = {}
    current_step = payload.get("current_step")
    if isinstance(current_step, int) and 1 <= current_step <= _STEP_COUNT:
        update["current_step"] = current_step

    status = payload.get("status")
    if status in _VALID_TASK_STATUSES:
        update["status"] = status

    activity_key = payload.get("activity_key")
    if activity_key in _VALID_ACTIVITY_KEYS:
        update["activity_key"] = activity_key

    steps = payload.get("steps")
    if isinstance(steps, list):
        cleaned_steps = [step for step in steps[:_STEP_COUNT] if step in _VALID_STEP_STATUSES]
        if cleaned_steps:
            update["steps"] = cleaned_steps

    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        cleaned_artifacts = [
            {
                "id": item.get("id") or uuid.uuid4().hex[:10],
                "type": str(item.get("type") or "").strip(),
                "title": str(item.get("title") or "").strip(),
                "content": str(item.get("content") or "").strip(),
                "created_at": item.get("created_at") or _now(),
            }
            for item in artifacts
            if isinstance(item, dict) and item.get("type") and (item.get("title") or item.get("content"))
        ]
        if cleaned_artifacts:
            update["artifacts"] = cleaned_artifacts

    plan = payload.get("plan")
    if isinstance(plan, dict):
        cleaned_plan: dict[str, Any] = {}
        for field in _PLAN_FIELDS:
            items = plan.get(field)
            if isinstance(items, list):
                cleaned = [str(item).strip()[:220] for item in items if str(item).strip()]
                if cleaned:
                    cleaned_plan[field] = cleaned[:8]
        task_type = str(plan.get("task_type") or "").strip()[:80]
        if task_type:
            cleaned_plan["task_type"] = task_type
        if cleaned_plan:
            update["plan"] = cleaned_plan

    return update or None


def build_initial_plan(goal: str) -> dict[str, Any]:
    """Create a structured first-pass plan that the model can refine later."""
    text = (goal or "").strip()
    lowered = text.lower()
    task_type = "general"
    if any(word in lowered for word in ("买", "购买", "报价", "保险", "laptop", "insurance", "quote", "travel", "旅行")):
        task_type = "research_comparison"
    elif any(word in lowered for word in ("写", "生成", "制作", "draft", "create", "make", "设计")):
        task_type = "creation"
    elif any(word in lowered for word in ("修", "bug", "报错", "代码", "debug", "fix", "test")):
        task_type = "technical"

    missing = ["目标范围", "约束条件", "完成标准"]
    criteria = ["明确用户真正要达成的结果", "列出关键假设和不确定项", "给出可执行下一步"]
    research = ["先确认必要条件", "再收集来源或候选", "最后整理对比和建议"]

    if task_type == "research_comparison":
        missing = ["预算或价格范围", "地点/渠道/时间要求", "必须条件和偏好条件"]
        criteria = ["候选方案不少于 3 个或明确说明不足原因", "输出结构化对比表", "标注来源、风险和待核验项"]
        research = ["列出比较维度", "搜索不同来源", "抓取关键页面", "保存证据并输出表格"]
    elif task_type == "creation":
        missing = ["目标受众", "格式/风格要求", "交付文件类型"]
        criteria = ["交付物能直接使用", "内容结构清楚", "如需文件则保存到 outputs"]
        research = ["确认规格", "生成草稿", "检查缺口", "输出最终文件或文本"]
    elif task_type == "technical":
        missing = ["复现步骤或错误信息", "目标环境", "允许执行的检查/测试范围"]
        criteria = ["定位根因或给出可验证假设", "修改范围尽量小", "跑相关测试或说明未跑原因"]
        research = ["读相关文件", "定位调用路径", "做最小修复", "运行测试验证"]

    return {
        "task_type": task_type,
        "missing_requirements": missing,
        "success_criteria": criteria,
        "research_plan": research,
        "created_at": _now(),
        "updated_at": _now(),
    }


def _merge_plan(task: dict[str, Any], plan_update: dict[str, Any]) -> None:
    plan = task.setdefault("plan", build_initial_plan(task.get("goal") or ""))
    for field in _PLAN_FIELDS:
        items = plan_update.get(field)
        if isinstance(items, list):
            cleaned = [str(item).strip()[:220] for item in items if str(item).strip()]
            if cleaned:
                plan[field] = cleaned[:8]
    task_type = str(plan_update.get("task_type") or "").strip()[:80]
    if task_type:
        plan["task_type"] = task_type
    plan["updated_at"] = _now()


def _apply_inferred_update(task: dict[str, Any], text: str) -> None:
    inferred_idx = _infer_step_index(text)
    if inferred_idx is not None:
        if inferred_idx == _STEP_COUNT - 1 and _looks_like_final_recommendation(text):
            _complete_task(task)
            task["activity"].append(_activity("已完成任务建议", "advanced"))
        else:
            _set_current_step(task, inferred_idx)
            task["activity"].append(_activity("已根据回复更新当前任务阶段", "advanced"))
    elif _is_waiting_for_user(text, int(task.get("current_step") or 0)):
        task["activity"].append(_activity("等待用户补充信息或确认下一步", "waiting"))
    elif _looks_like_final_recommendation(text):
        _complete_task(task)
        task["activity"].append(_activity("已完成任务建议", "advanced"))
    else:
        _advance(task)
        task["activity"].append(_activity("已完成一轮任务推进", "advanced"))


def _looks_like_final_recommendation(text: str) -> bool:
    lowered = (text or "").lower()
    signals = ("最终建议", "我的建议", "推荐你", "首选", "结论", "final recommendation", "i recommend")
    return any(signal in lowered for signal in signals)


def _complete_task(task: dict[str, Any]) -> None:
    _set_current_step(task, _STEP_COUNT - 1)
    task["status"] = "completed"
    for step in task.get("steps", []):
        step["status"] = "completed"


def _extract_artifacts(text: str) -> list[dict[str, Any]]:
    table = _first_markdown_table(strip_update_markers(text))
    if not table:
        return []
    return [{
        "id": uuid.uuid4().hex[:10],
        "type": "comparison_table",
        "title": "结构化对比表",
        "content": table,
        "created_at": _now(),
    }]


def _first_markdown_table(text: str) -> str:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    for idx in range(len(lines) - 1):
        header = lines[idx].strip()
        separator = lines[idx + 1].strip()
        if "|" not in header or "|" not in separator:
            continue
        if not re.match(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$", separator):
            continue
        table_lines = [header, separator]
        for line in lines[idx + 2:]:
            if "|" not in line.strip():
                break
            table_lines.append(line.strip())
        return "\n".join(table_lines)
    return ""


def _append_artifacts(task: dict[str, Any], artifacts: list[dict[str, Any]]) -> None:
    store = task.setdefault("artifacts", {})
    items = store.setdefault("items", [])
    seen = {item.get("content") for item in items if isinstance(item, dict)}
    for artifact in artifacts:
        if not artifact.get("id"):
            artifact["id"] = uuid.uuid4().hex[:10]
        if not artifact.get("created_at"):
            artifact["created_at"] = _now()
        if artifact.get("content") in seen:
            continue
        items.append(artifact)
        seen.add(artifact.get("content"))
    store["items"] = items[-8:]
    store["updated_at"] = _now()


def _load(session_id: str) -> dict[str, Any] | None:
    _ensure_store()
    db = _db_path()
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT payload FROM tasks WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    try:
        task = json.loads(row[0])
    except json.JSONDecodeError:
        return None
    if isinstance(task, dict):
        _TASKS[session_id] = task
        return task
    return None


def _save(task: dict[str, Any]) -> None:
    session_id = task.get("session_id")
    if not session_id:
        return
    _ensure_store()
    db = _db_path()
    payload = json.dumps(task, ensure_ascii=False)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO tasks (session_id, payload, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at
            """,
            (session_id, payload, time.time()),
        )


def _ensure_store() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(_db_path()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                session_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                updated_at REAL
            )
            """
        )


def _db_path():
    return settings.data_dir / "secretary.db"


def end(session_id: str) -> dict[str, Any] | None:
    task = _TASKS.get(session_id) or _load(session_id)
    if not task:
        return None
    task["status"] = "ended"
    for step in task.get("steps", []):
        if step.get("status") == "in_progress":
            step["status"] = "pending"
    task["activity"].append(_activity("任务模式已结束", "ended"))
    task["updated_at"] = _now()
    _save(task)
    return task


def prompt_context(task: dict[str, Any]) -> str:
    steps = "\n".join(
        f"- [{'x' if step['status'] == 'completed' else ' '}] {step['title']}"
        for step in task.get("steps", [])
    )
    current_step = int(task.get("current_step") or 0) + 1
    plan = task.get("plan") or build_initial_plan(task.get("goal") or "")
    plan_text = _plan_prompt_text(plan)
    return (
        "[任务模式]\n"
        "用户已选择 Task Mode。你必须使用多步任务工作流，而不是普通快速回答。\n"
        "硬性规则：\n"
        "1. 第一轮必须先给任务清单/checklist，并标明“当前步骤”。不要直接开始网页搜索或调用浏览器工具，除非用户已经给足关键条件并明确要求立刻查。\n"
        "2. 如果缺少预算、用途、地点、时间、偏好、约束等关键条件，先问最少必要问题并暂停；不要假装知道。\n"
        "3. 每次回复都要包含：当前步骤、正在做什么、已知/缺口、下一步。\n"
        "4. 进入研究或比较阶段后，再用 search_web/fetch_webpage 在后台查来源；只有动态页面、登录、点击/输入时才用 browser_open。\n"
        "5. 做研究或比较时必须输出结构化表格、来源、优缺点和不确定项，不要只粘贴一页 review 或单一搜索结果就下结论。\n"
        "6. 可以说明可观察的做法和依据，但不要展示隐藏推理链。\n"
        "7. 如果任务是购买决策（如电脑、保险、服务报价），第一步通常应先确认预算、用途、偏好和是否需要本地库存/自提。\n\n"
        "工具失败处理：\n"
        "- 单个网站抓不到内容时，换搜索关键词、换来源或用 site: 搜索，不要反复卡在同一个网页。\n"
        "- 工具结果不足时，不要把“无法获取”当最终答案；要给出已知、缺口、替代策略、可行动建议和下一步。\n"
        "- 如果已经到工具预算上限，仍要基于已知信息和领域判断给用户一个能继续推进的结果。\n\n"
        "研究预算：\n"
        "- 进入收集候选/对比阶段后，最多使用 3-4 轮工具调用；不要无限搜索。\n"
        "- 工具预算接近上限时，必须停止搜索并输出标准 Markdown 对比表。\n"
        "- 即使价格、库存不完整，也要列出候选方向、来源、风险和下一步核验项，未确认字段写“待核验”。\n\n"
        "任务状态更新协议：\n"
        "- 每次任务模式回复末尾尽量追加一段隐藏标记，格式严格为：<!--TASK_UPDATE {\"current_step\":1,\"status\":\"in_progress\",\"activity_key\":\"waiting\"}-->\n"
        "- current_step 必须是 1-5 的整数；status 只能是 in_progress/waiting/completed；activity_key 只能是 waiting/advanced/working/artifact。\n"
        "- 可选 steps 数组只能包含 pending/in_progress/completed，最多 5 项；可选 artifacts 数组必须包含 type 和 title 或 content。\n"
        "- 可选 plan 对象可包含 task_type、missing_requirements、success_criteria、research_plan；如果你发现初始计划不准，必须用 plan 修正。\n"
        "- 这段标记只用于系统更新 UI，用户界面会隐藏，不要在正文里解释它。写不准时宁可省略，由后端兜底推断。\n\n"
        "任务产物：\n"
        "- 到“整理结构化对比”阶段时，如果有候选方案，请输出标准 Markdown 表格；系统会自动把它保存为任务产物。\n"
        "- 表格不要只有链接，要包含价格/配置/条款/来源/优点/风险/下一步核验项中与任务相关的列。\n\n"
        f"任务目标：{task.get('goal')}\n"
        f"当前步骤：{current_step}. {task.get('steps', [{}])[current_step - 1].get('title', '')}\n"
        f"当前清单：\n{steps}\n"
        f"结构化计划：\n{plan_text}\n"
    )


def _plan_prompt_text(plan: dict[str, Any]) -> str:
    def lines(title: str, values: Any) -> str:
        items = values if isinstance(values, list) else []
        body = "\n".join(f"  - {item}" for item in items[:8]) or "  - 待确认"
        return f"- {title}:\n{body}"

    return "\n".join([
        f"- task_type: {plan.get('task_type') or 'general'}",
        lines("missing_requirements", plan.get("missing_requirements")),
        lines("success_criteria", plan.get("success_criteria")),
        lines("research_plan", plan.get("research_plan")),
    ])


def _advance(task: dict[str, Any]) -> None:
    steps = task.get("steps", [])
    idx = int(task.get("current_step") or 0)
    if 0 <= idx < len(steps):
        steps[idx]["status"] = "completed"
    next_idx = min(idx + 1, len(steps) - 1)
    task["current_step"] = next_idx
    if idx + 1 < len(steps):
        steps[next_idx]["status"] = "in_progress"
    else:
        task["status"] = "completed"


def _set_current_step(task: dict[str, Any], idx: int) -> None:
    steps = task.get("steps", [])
    if not steps:
        return
    idx = max(0, min(idx, len(steps) - 1))
    for pos, step in enumerate(steps):
        if pos < idx:
            step["status"] = "completed"
        elif pos == idx:
            step["status"] = "in_progress"
        else:
            step["status"] = "pending"
    task["current_step"] = idx
    task["status"] = "in_progress"


def _infer_step_index(text: str) -> int | None:
    lowered = text.lower()
    step_match = re.search(r"\b(?:step|步骤)\s*([1-5])\b|第\s*([1-5])\s*步", lowered)
    if step_match:
        raw = next((group for group in step_match.groups() if group), None)
        if raw:
            return int(raw) - 1

    lines = [line.strip().strip("-• ") for line in text.splitlines() if line.strip()]
    for line in lines[:8]:
        line = line.strip("*_ ")
        numbered = re.match(r"^(?:step\s*)?([1-5])[\.\-:：、\s]+(.+)$", line, re.IGNORECASE)
        if numbered:
            return int(numbered.group(1)) - 1

    signals = [
        ("确认目标", "必要条件", "missing information", "requirements", "need to confirm"),
        ("比较标准", "评价标准", "criteria", "comparison standard"),
        ("候选方案", "候选保险", "来源", "providers", "candidates", "sources"),
        ("结构化对比", "对比表", "comparison table", "compare table"),
        ("建议", "推荐", "下一步", "recommendation", "next step"),
    ]
    current_lines = [
        line.lower()
        for line in lines[:10]
        if not any(skip in line.lower() for skip in ("下一步", "next step", "后续", "later"))
    ]
    hits = [
        idx
        for idx, words in enumerate(signals)
        if any(any(word in line for word in words) for line in current_lines)
    ]
    return max(hits) if hits else None


def _is_waiting_for_user(text: str, current_step: int) -> bool:
    if current_step > 0:
        return False
    head = text[:320]
    return "?" in head or "？" in head or "请" in head or "tell me" in head.lower()


def _activity(text: str, key: str | None = None) -> dict[str, str]:
    item = {"time": _now(), "text": text}
    if key:
        item["key"] = key
    return item


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
