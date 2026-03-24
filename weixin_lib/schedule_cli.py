"""
定时任务命令行工具 — 供 Claude Code 通过 Bash 工具调用。

用法:
    python weixin_lib/schedule_cli.py add --time "15:00" --message "开会提醒"
    python weixin_lib/schedule_cli.py add --time "2025-05-03 15:00" --message "提交报告"
    python weixin_lib/schedule_cli.py add --cron "0 9 * * 1-5" --message "工作日早报"
    python weixin_lib/schedule_cli.py list
    python weixin_lib/schedule_cli.py delete --id abc12345

目标用户自动从 data/.current_user 文件读取（由 bridge 维护）。
"""

import argparse
import json
import os
import sys

# 项目根目录（weixin_lib 的上一级）
_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from weixin_lib.scheduler import add_task, list_tasks, delete_task

_DATA_DIR = os.path.join(_PROJECT_DIR, "data")
_TASKS_FILE = os.path.join(_DATA_DIR, "scheduled_tasks.json")
_CURRENT_USER_FILE = os.path.join(_DATA_DIR, ".current_user")


def _get_target_user():
    """从 .current_user 文件读取当前用户 ID。"""
    if not os.path.exists(_CURRENT_USER_FILE):
        return "unknown"
    with open(_CURRENT_USER_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("user_id", "unknown")


def cmd_add(args):
    os.makedirs(_DATA_DIR, exist_ok=True)
    if args.cron:
        task_id = add_task(
            _TASKS_FILE,
            task_type="cron",
            message=args.message,
            target_user=_get_target_user(),
            cron_expr=args.cron,
        )
        print(f"✅ 周期任务已创建 (ID: {task_id}, cron: {args.cron})")
    elif args.time:
        task_id = add_task(
            _TASKS_FILE,
            task_type="once",
            message=args.message,
            target_user=_get_target_user(),
            trigger_time=args.time,
        )
        print(f"✅ 定时任务已创建 (ID: {task_id}, 触发时间: {args.time})")
    else:
        print("❌ 需要 --time 或 --cron 参数")
        sys.exit(1)


def cmd_list(args):
    tasks = list_tasks(_TASKS_FILE)
    if not tasks:
        print("📋 没有待执行的任务")
        return
    print(f"📋 待执行任务 ({len(tasks)} 个):")
    for t in tasks:
        task_type = t.get("type", "once")
        if task_type == "once":
            time_info = t.get("trigger_time", "?")
        else:
            time_info = f"cron: {t.get('cron_expr', '?')}"
        print(f"  [{t['id']}] {time_info} → {t['message']}")


def cmd_delete(args):
    if delete_task(_TASKS_FILE, args.id):
        print(f"✅ 任务 {args.id} 已取消")
    else:
        print(f"❌ 未找到任务 {args.id}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="定时任务管理")
    sub = parser.add_subparsers(dest="command", required=True)

    # add
    p_add = sub.add_parser("add", help="添加定时任务")
    p_add.add_argument("--time", help="触发时间 (如 '15:00' 或 '2025-05-03 15:00')")
    p_add.add_argument("--cron", help="cron 表达式 (如 '0 9 * * 1-5')")
    p_add.add_argument("--message", required=True, help="提醒消息内容")

    # list
    sub.add_parser("list", help="列出待执行任务")

    # delete
    p_del = sub.add_parser("delete", help="取消任务")
    p_del.add_argument("--id", required=True, help="任务 ID")

    args = parser.parse_args()

    handlers = {"add": cmd_add, "list": cmd_list, "delete": cmd_delete}
    handlers[args.command](args)


if __name__ == "__main__":
    main()
