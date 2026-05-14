#!/usr/bin/env python3
"""CLI 入口。

用法：
    python agent.py run <file> --task "任务描述"
    python agent.py chat          # 交互式对话
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

# 切换到 secretary/ 目录，确保相对路径（data/、outputs/、inputs/）正确
os.chdir(Path(__file__).parent)

from app.agent import chat  # noqa: E402（chdir 必须在 import 之前）
from app.config import settings
from app import memory as mem_module
from app import accounting
from app import evolution
from app import maintenance
from app import relay


def _run(args: argparse.Namespace) -> None:
    file_arg = args.file
    task = args.task
    session_id = f"cli_{int(time.time())}"

    # 如果用户给的路径含 inputs/，剥掉前缀，因为 read_file 已经以 inputs/ 为根
    file_for_tool = file_arg
    for prefix in ("./inputs/", "inputs/"):
        if file_for_tool.startswith(prefix):
            file_for_tool = file_for_tool[len(prefix):]
            break

    prompt = (
        f"请读取文件 `{file_for_tool}`（在 inputs/ 目录下），然后完成以下任务：\n\n{task}"
    )

    print(f"\n文件：{file_arg}")
    print(f"任务：{task}")
    print("─" * 60)
    print("Agent 处理中，请稍候…\n")

    result = chat(session_id, prompt)

    print(result["reply"])

    if result["files"]:
        print("\n生成的文件：")
        for f in result["files"]:
            print(f"  {f}")

    print()


def _chat(_args: argparse.Namespace) -> None:
    session_id = f"cli_{int(time.time())}"
    print("\n交互模式（输入 exit 退出）\n")
    while True:
        try:
            user_input = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if user_input.lower() in {"exit", "quit", "q"}:
            break
        if not user_input:
            continue

        result = chat(session_id, user_input)
        print(f"\nAgent：{result['reply']}")
        if result["files"]:
            print("生成的文件：")
            for f in result["files"]:
                print(f"  {f}")
        print()


def _logs(args: argparse.Namespace) -> None:
    log_path = Path("logs") / "tool_calls.jsonl"
    if args.clean:
        result = maintenance.clean_logs(keep=args.keep)
        print(f"日志清理完成：{result['before']} → {result['after']}")
        print(f"备份：{result.get('backup', '无')}")
        return

    if not log_path.exists():
        print("暂无日志文件。", file=sys.stderr)
        sys.exit(1)

    lines = log_path.read_text(encoding="utf-8").strip().split("\n")
    if not lines or lines == [""]:
        print("日志文件为空。", file=sys.stderr)
        sys.exit(1)

    # 默认只展示最近 N 条
    tail = args.tail if args.tail else len(lines)
    shown = 0
    for raw in reversed(lines):
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if shown >= tail:
            break
        shown += 1
        ts = entry.get("ts", "")
        task_id = entry.get("task_id", "")[:20]
        name = entry.get("tool_name", "")
        risk = entry.get("risk_level", "")
        status = entry.get("status", "")
        model = entry.get("model", "")
        tokens = entry.get("token_usage", {})
        total_tok = tokens.get("total_tokens", 0)
        dur = entry.get("duration_ms", 0)
        err = entry.get("error", "")
        print(f"[{ts}] task={task_id} tool={name} risk={risk} status={status} model={model} tokens={total_tok} dur={dur}ms")
        if err:
            print(f"  ERROR: {err}")
        if args.verbose:
            print(f"  input: {json.dumps(entry.get('tool_input'), ensure_ascii=False)}")
            print(f"  output: {json.dumps(entry.get('tool_output'), ensure_ascii=False)}")
        print()


def _memory(args: argparse.Namespace) -> None:
    action = args.action

    if action == "list":
        result = mem_module.list_memories(
            type=args.type or None,
            confirmed=0 if args.candidates else 1,
        )
        if not result:
            label = "候选记忆" if args.candidates else "已确认记忆"
            print(f"暂无{label}。")
            return
        label = "候选记忆" if args.candidates else "已确认记忆"
        print(f"── {label} ({len(result)} 条) ──")
        for m in result:
            imp = m.get("importance", 3)
            mtype = m.get("type", "")
            tags = m.get("tags") or ""
            print(f"  [{m['id'][:8]}…] [{mtype}] [★{imp}] {m['title']}")
            print(f"    {m['content'][:120]}")
            if tags:
                print(f"    tags: {tags}")
            print()

    elif action == "confirm":
        if not args.id:
            print("请提供记忆 ID：agent.py memory confirm <id>", file=sys.stderr)
            sys.exit(1)
        result = mem_module.confirm_memory(args.id)
        if result.get("ok"):
            print(f"已确认记忆 {args.id[:8]}…")
        else:
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)

    elif action == "reject":
        if not args.id:
            print("请提供记忆 ID：agent.py memory reject <id>", file=sys.stderr)
            sys.exit(1)
        result = mem_module.reject_memory(args.id)
        if result.get("ok"):
            print(f"已删除候选记忆 {args.id[:8]}…")
        else:
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)

    elif action == "forget":
        if not args.id:
            print("请提供记忆 ID：agent.py memory forget <id>", file=sys.stderr)
            sys.exit(1)
        confirm_input = input(f"确定永久删除记忆 {args.id[:8]}…？(yes/no) ").strip().lower()
        if confirm_input != "yes":
            print("已取消。")
            return
        result = mem_module.forget(args.id)
        if result.get("ok"):
            print(f"已删除记忆 {args.id[:8]}…")
        else:
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)


def _compress(args: argparse.Namespace) -> None:
    session_id = args.session or f"cli_{int(time.time())}"
    print(f"对话压缩中（session: {session_id}）…")
    result = mem_module.compress_history_to_facts(session_id)
    if result.get("ok"):
        print(f"完成。新增长期记忆 {result['facts_added']} 条。")
    else:
        note = result.get("note") or result.get("error") or "未知错误"
        print(f"未执行：{note}")


def _doctor(_args: argparse.Namespace) -> None:
    result = maintenance.doctor()
    print(f"状态：{result['status']}")
    for check in result["checks"]:
        status = check.get("status", "")
        name = check.get("name", "")
        message = check.get("message", "")
        detail = check.get("path") or check.get("model") or ""
        line = f"- [{status}] {name}"
        if detail:
            line += f": {detail}"
        if message:
            line += f" ({message})"
        print(line)
    if result["status"] != "ok":
        sys.exit(1)


def _usage(_args: argparse.Namespace) -> None:
    if getattr(_args, "db", False):
        result = accounting.usage_summary()
        print(f"模型调用：{result['calls']}")
    else:
        result = maintenance.usage_summary()
        print(f"工具调用：{result['calls']}")
    print(f"Prompt tokens：{result['prompt_tokens']}")
    print(f"Completion tokens：{result['completion_tokens']}")
    print(f"Total tokens：{result['total_tokens']}")
    if "cost" in result:
        print(f"Cost：{result['cost']:.6f}")
    if result["by_model"]:
        print("\n按模型：")
        for model, item in sorted(result["by_model"].items()):
            line = f"- {model}: calls={item['calls']} total={item['total_tokens']}"
            if "cost" in item:
                line += f" cost={item['cost']:.6f}"
            print(line)
    if result["by_tool"]:
        print("\n按工具：")
        for tool, item in sorted(result["by_tool"].items()):
            line = f"- {tool}: calls={item['calls']} total={item['total_tokens']}"
            if "cost" in item:
                line += f" cost={item['cost']:.6f}"
            print(line)
    if result.get("by_route"):
        print("\n按路由：")
        for route, item in sorted(result["by_route"].items()):
            print(f"- {route}: calls={item['calls']} total={item['total_tokens']} cost={item['cost']:.6f}")


def _cost_report(args: argparse.Namespace) -> None:
    try:
        report_day = date.fromisoformat(args.date) if args.date else None
    except ValueError:
        print("错误：--date 必须是 YYYY-MM-DD 格式", file=sys.stderr)
        sys.exit(1)
    result = accounting.write_daily_cost_report(day=report_day)
    report = result["report"]
    print(f"成本日报已生成：{result['path']}")
    print(f"日期：{report['date']}")
    print(f"模型调用：{report['calls']}")
    print(f"Total tokens：{report['total_tokens']}")
    print(f"Cost：{report['cost']:.6f}")


def _sleep(args: argparse.Namespace) -> None:
    result = maintenance.sleep_evolve(
        force=args.force,
        min_interval_hours=args.min_interval_hours,
        limit_messages=args.messages,
        limit_logs=args.logs,
    )
    if result.get("skipped"):
        print("sleep-time 自我进化已跳过：最近已经扫描过。")
        print(f"下次可运行时间戳：{result.get('next_run_at'):.0f}")
        return
    evolution_result = result.get("evolution", {})
    print(f"sleep-time 自我进化完成：新增候选 {evolution_result.get('candidates_added', 0)} 条")
    for candidate_id in evolution_result.get("ids", []):
        print(f"- {candidate_id}")
    if evolution_result.get("note"):
        print(evolution_result["note"])


def _stability_check(args: argparse.Namespace) -> None:
    result = maintenance.stability_check(snapshot=not args.no_snapshot)
    print(f"稳定性检查：{result['status']}")
    if result.get("snapshot_path"):
        print(f"快照：{result['snapshot_path']}")
    for item in result["checks"]:
        message = item.get("message") or item.get("quick_check") or ""
        suffix = f" ({message})" if message else ""
        print(f"- [{item['status']}] {item['name']}: {item.get('path', '')}{suffix}")


def _route(args: argparse.Namespace) -> None:
    if args.route:
        try:
            accounting.set_route(args.route)
        except ValueError as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
    print(f"当前 route：{accounting.current_route()}")
    print(f"当前 model：{settings.model}")


def _api_key(args: argparse.Namespace) -> None:
    if args.action == "list":
        keys = accounting.list_api_keys()
        if not keys:
            print("暂无 BYO API key。")
            return
        for item in keys:
            print(f"- {item['provider']}: {item['key_hint']}")
        return

    if args.action == "set":
        api_key = args.key or getpass.getpass(f"{args.provider} API key: ")
        try:
            item = accounting.set_api_key(args.provider, api_key)
        except ValueError as e:
            print(f"错误：{e}", file=sys.stderr)
            sys.exit(1)
        print(f"已保存 {item['provider']} key：{item['key_hint']}")
        return

    if args.action == "delete":
        result = accounting.delete_api_key(args.provider)
        print(f"已删除 {result['provider']} active key：{result['removed']}")
        return


def _relay(args: argparse.Namespace) -> None:
    if args.action == "status":
        print(f"Relay URL：{settings.relay_url}")
        print(f"Desktop ID：{settings.relay_desktop_id}")
        print(f"Token configured：{bool(settings.relay_shared_token)}")
        return
    if args.action == "connect":
        url = args.url or settings.relay_url
        token = args.token or settings.relay_shared_token
        print(f"连接 Relay：{url}")
        try:
            asyncio.run(relay.connect_desktop(url, token=token))
        except KeyboardInterrupt:
            print("\n已断开。")


def _evolve(args: argparse.Namespace) -> None:
    if args.action == "scan":
        result = evolution.scan(limit_messages=args.messages, limit_logs=args.logs)
        print(f"扫描完成：新增候选 {result['candidates_added']} 条")
        for candidate_id in result.get("ids", []):
            print(f"- {candidate_id}")
        if result.get("note"):
            print(result["note"])
        return

    if args.action == "list":
        items = evolution.list_candidates(status=args.status, limit=args.limit)
        if not items:
            print("暂无学习候选。")
            return
        for item in items:
            print(f"[{item['id'][:8]}…] [{item['status']}] [{item['type']}] {item['title']}")
            print(f"  confidence={item['confidence']:.2f}")
            print(f"  {item['content'][:180]}")
            if item.get("evidence"):
                print(f"  evidence: {item['evidence'][:180]}")
            print()
        return

    if args.action == "apply":
        if not args.id:
            print("请提供候选 ID：agent.py evolve apply <id>", file=sys.stderr)
            sys.exit(1)
        result = evolution.apply_candidate(args.id)
        if not result.get("ok"):
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"已应用学习项 {args.id[:8]}… → memory {result['memory_id'][:8]}…")
        return

    if args.action == "reject":
        if not args.id:
            print("请提供候选 ID：agent.py evolve reject <id>", file=sys.stderr)
            sys.exit(1)
        result = evolution.reject_candidate(args.id)
        if not result.get("ok"):
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"已拒绝学习项 {args.id[:8]}…")
        return

    if args.action == "disable":
        if not args.id:
            print("请提供候选 ID：agent.py evolve disable <id>", file=sys.stderr)
            sys.exit(1)
        result = evolution.disable_candidate(args.id)
        if not result.get("ok"):
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"已停用学习项 {args.id[:8]}…")
        return

    if args.action == "rollback":
        if not args.id:
            print("请提供候选 ID：agent.py evolve rollback <id>", file=sys.stderr)
            sys.exit(1)
        result = evolution.rollback_candidate(args.id)
        if not result.get("ok"):
            print(f"失败：{result.get('error')}", file=sys.stderr)
            sys.exit(1)
        print(f"已回滚学习项 {args.id[:8]}…，状态恢复为 pending")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agent", description="Auctus Local Agent CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="处理文件并执行任务")
    run_p.add_argument("file", help="输入文件，相对于 inputs/ 目录，例如 prd.md")
    run_p.add_argument("--task", required=True, help="任务描述")

    sub.add_parser("chat", help="交互式对话模式")

    logs_p = sub.add_parser("logs", help="查看工具调用日志")
    logs_p.add_argument("-n", "--tail", type=int, default=20, help="显示最近 N 条（默认 20）")
    logs_p.add_argument("-v", "--verbose", action="store_true", help="显示完整输入输出")
    logs_p.add_argument("--clean", action="store_true", help="清理旧日志，只保留最近 --keep 条")
    logs_p.add_argument("--keep", type=int, default=1000, help="清理时保留最近 N 条（默认 1000）")

    mem_p = sub.add_parser("memory", help="管理长期记忆")
    mem_p.add_argument(
        "action", choices=["list", "confirm", "reject", "forget"],
        help="list=列出记忆 confirm=确认候选 reject=拒绝候选 forget=永久删除",
    )
    mem_p.add_argument("id", nargs="?", help="记忆 UUID（confirm/reject/forget 时必填）")
    mem_p.add_argument("--type", choices=["preference", "project", "rule", "temporary"],
                       help="按类型过滤（list 时可用）")
    mem_p.add_argument("--candidates", action="store_true",
                       help="只显示待确认的候选记忆（list 时可用）")

    compress_p = sub.add_parser("compress", help="把会话历史压缩成长期记忆（sleep-time 任务）")
    compress_p.add_argument("--session", help="指定 session_id（默认新建）")

    sub.add_parser("doctor", help="检查 API key、workspace、日志、数据目录和模型配置")
    usage_p = sub.add_parser("usage", help="汇总模型 token 用量")
    usage_p.add_argument("--db", action="store_true", help="读取 usage 表（模型调用级别），默认读取工具日志")

    cost_p = sub.add_parser("cost-report", help="生成每日模型成本 Markdown 报告")
    cost_p.add_argument("--date", help="报告日期，格式 YYYY-MM-DD；默认今天")

    stability_p = sub.add_parser("stability-check", help="检查本地数据完整性并写入稳定性快照")
    stability_p.add_argument("--no-snapshot", action="store_true", help="只检查，不写入快照文件")

    sleep_p = sub.add_parser("sleep", help="运行 sleep-time 维护任务（自动提炼自我学习候选）")
    sleep_p.add_argument("--force", action="store_true", help="忽略时间间隔，立即扫描")
    sleep_p.add_argument("--min-interval-hours", type=float, default=12, help="两次自动扫描的最小间隔")
    sleep_p.add_argument("--messages", type=int, default=120, help="读取最近 N 条消息")
    sleep_p.add_argument("--logs", type=int, default=80, help="读取最近 N 条工具日志")

    route_p = sub.add_parser("route", help="查看或切换 LLM route（local/byo/proxy）")
    route_p.add_argument("route", nargs="?", choices=sorted(accounting.ROUTES), help="目标 route")

    key_p = sub.add_parser("api-key", help="管理 BYO API key")
    key_p.add_argument("action", choices=["list", "set", "delete"])
    key_p.add_argument("--provider", choices=sorted(accounting.PROVIDERS), default="deepseek")
    key_p.add_argument("--key", help="API key；不传则安全提示输入")

    relay_p = sub.add_parser("relay", help="管理 Cloud Relay 连接")
    relay_p.add_argument("action", choices=["status", "connect"])
    relay_p.add_argument("--url", help="Relay WebSocket URL，默认读取 RELAY_URL")
    relay_p.add_argument("--token", help="Relay token，默认读取 RELAY_SHARED_TOKEN")

    evolve_p = sub.add_parser("evolve", help="从历史对话和任务日志中生成、确认自我学习项")
    evolve_p.add_argument("action", choices=["scan", "list", "apply", "reject", "disable", "rollback"])
    evolve_p.add_argument("id", nargs="?", help="学习候选 ID（apply/reject/disable/rollback 时必填）")
    evolve_p.add_argument("--messages", type=int, default=120, help="scan 时读取最近 N 条消息")
    evolve_p.add_argument("--logs", type=int, default=80, help="scan 时读取最近 N 条工具日志")
    evolve_p.add_argument("--status", choices=["pending", "applied", "rejected", "disabled", "all"], default="pending")
    evolve_p.add_argument("--limit", type=int, default=50)

    args = parser.parse_args()
    if args.cmd == "run":
        _run(args)
    elif args.cmd == "chat":
        _chat(args)
    elif args.cmd == "logs":
        _logs(args)
    elif args.cmd == "memory":
        _memory(args)
    elif args.cmd == "compress":
        _compress(args)
    elif args.cmd == "doctor":
        _doctor(args)
    elif args.cmd == "usage":
        _usage(args)
    elif args.cmd == "cost-report":
        _cost_report(args)
    elif args.cmd == "stability-check":
        _stability_check(args)
    elif args.cmd == "sleep":
        _sleep(args)
    elif args.cmd == "route":
        _route(args)
    elif args.cmd == "api-key":
        _api_key(args)
    elif args.cmd == "relay":
        _relay(args)
    elif args.cmd == "evolve":
        _evolve(args)


if __name__ == "__main__":
    main()
