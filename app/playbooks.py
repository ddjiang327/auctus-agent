"""Domain playbooks injected into the agent prompt for common task types.

Two kinds:
  - Built-in: hardcoded Python functions below (shopping, laptop, insurance).
  - User-defined: stored in data/playbooks.json, editable via the UI.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from .config import settings

_USER_PLAYBOOKS_PATH: Path = settings.data_dir / "playbooks.json"


def _read_user_playbooks() -> list[dict]:
    if not _USER_PLAYBOOKS_PATH.exists():
        return []
    try:
        data = json.loads(_USER_PLAYBOOKS_PATH.read_text(encoding="utf-8"))
        return data.get("playbooks", [])
    except Exception:
        return []


def _write_user_playbooks(playbooks: list[dict]) -> None:
    _USER_PLAYBOOKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    _USER_PLAYBOOKS_PATH.write_text(
        json.dumps({"playbooks": playbooks}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_playbooks() -> list[dict]:
    return _read_user_playbooks()


def add_playbook(name: str, trigger: str, content: str) -> dict:
    pb = {
        "id": uuid.uuid4().hex[:10],
        "name": name.strip(),
        "trigger": trigger.strip(),
        "content": content.strip(),
        "enabled": True,
        "created_at": time.time(),
    }
    pbs = _read_user_playbooks()
    pbs.append(pb)
    _write_user_playbooks(pbs)
    return pb


def update_playbook(pb_id: str, **fields) -> dict:
    pbs = _read_user_playbooks()
    allowed = {"name", "trigger", "content", "enabled"}
    for pb in pbs:
        if pb["id"] == pb_id:
            pb.update({k: v for k, v in fields.items() if k in allowed})
    _write_user_playbooks(pbs)
    return next((p for p in pbs if p["id"] == pb_id), {})


def remove_playbook(pb_id: str) -> bool:
    pbs = _read_user_playbooks()
    new_pbs = [p for p in pbs if p["id"] != pb_id]
    _write_user_playbooks(new_pbs)
    return len(new_pbs) < len(pbs)


_SHOPPING_PATTERNS = (
    r"\b(buy|purchase|shop|shopping|deal|cheap|best value|laptop|notebook|insurance|quote|provider)\b",
    r"(买|购买|划算|性价比|推荐|报价|保险|笔记本|电脑|游戏本|商家|店|库存|自提)",
)

_LAPTOP_PATTERNS = (
    r"\b(laptop|notebook|gaming laptop|macbook|rtx|gpu|3a game|aaa game)\b",
    r"(笔记本|电脑|游戏本|显卡|3a|游戏|墨尔本)",
)

_INSURANCE_PATTERNS = (
    r"\b(insurance|insurer|premium|quote|policy|excess|claim)\b",
    r"(保险|保费|报价|保单|理赔|垫底费|自付额)",
)


def context_for(user_text: str) -> str:
    """Return a compact system-context playbook for the current user request."""
    text = (user_text or "").strip().lower()
    if not text:
        return ""

    blocks: list[str] = []
    if _matches(text, _SHOPPING_PATTERNS):
        blocks.append(_shopping_context())
    if _matches(text, _LAPTOP_PATTERNS):
        blocks.append(_laptop_context())
    if _matches(text, _INSURANCE_PATTERNS):
        blocks.append(_insurance_context())
    for pb in _read_user_playbooks():
        if not pb.get("enabled"):
            continue
        trigger = (pb.get("trigger") or "").strip()
        content = (pb.get("content") or "").strip()
        if trigger and content and re.search(trigger, text, re.IGNORECASE):
            blocks.append(content)
    return "\n\n".join(blocks)


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _shopping_context() -> str:
    return (
        "[购买/报价任务策略]\n"
        "- 先建立判断框架：预算、用途、地点/渠道、时间要求、必须条件、偏好条件。\n"
        "- 如果关键条件不足，先给一个默认建议框架，再问最多 3 个最关键问题；Task Mode 下按步骤推进。\n"
        "- 查实时价格时，先 search_web，再 fetch_webpage；单个网站失败 1-2 次就换来源，不要在一个页面反复消耗工具预算。\n"
        "- 不要只打开零售商集合页；优先用搜索 query 找具体产品页、促销页或品牌页。\n"
        "- 网页抓不到商品列表时，改用 site: 搜索、零售商搜索结果、品牌官网、价格聚合站或其他本地商家；至少覆盖 3 个不同来源再下结论。\n"
        "- 工具结果不完整时，不能只回复“无法获取”；必须给可行动的替代方案、候选方向、判断标准和下一步。\n"
        "- 最终比较必须用标准 Markdown 表格：候选、价格/报价、关键配置/条款、来源、优点、风险、下一步核验项；不完整字段写“待核验”。\n"
    )


def _laptop_context() -> str:
    return (
        "[笔记本购买 playbook]\n"
        "- 3A 游戏本的默认门槛：RTX 4060 或更高，16GB RAM 起，512GB SSD 可接受但 1TB 更好，144Hz+ 屏幕，注意显卡功耗和散热。\n"
        "- 性价比判断优先级：GPU 档位和功耗 > CPU 是否拖后腿 > RAM/SSD > 屏幕 > 散热口碑 > 保修/退换 > 本地库存。\n"
        "- 墨尔本/澳洲常见来源：JB Hi-Fi、Officeworks、Harvey Norman、The Good Guys、Scorptec、Centre Com、Mwave、Umart、Lenovo/HP/Dell/ASUS/MSI/Acer 官方店。\n"
        "- 搜索 query 示例：`RTX 4060 gaming laptop Australia deal`、`site:scorptec.com.au RTX 4060 laptop`、`site:centrecom.com.au gaming laptop RTX 4060`、`Lenovo LOQ RTX 4060 Australia`。\n"
        "- 如果用户没给预算，先按档位说明：入门 3A 通常看 RTX 4060，中高预算看 RTX 4070/5070，极高预算再看 4080/5080 级别。\n"
        "- 不要推荐只靠集显或低端独显的机器给 3A 游戏需求；看到 RTX 3050/4050 要明确说明适合降画质或轻度游戏，不是高性价比 3A 首选。\n"
    )


def _insurance_context() -> str:
    return (
        "[保险比较 playbook]\n"
        "- 先确认地区、标的价值、贷款/合规要求、保额、excess、claim history、no claim bonus、附加险需求。\n"
        "- 比较时不能只看保费；要看保障范围、excess、等待期/除外责任、理赔口碑、折扣条件、取消/续保规则。\n"
        "- 工具拿不到实时报价时，输出需要用户补充的信息和可逐一询价的 insurer 清单；不要把单个 review 当结论。\n"
        "- 涉及金融/保险条款时提醒用户最终以官方报价和 PDS/条款为准。\n"
    )
