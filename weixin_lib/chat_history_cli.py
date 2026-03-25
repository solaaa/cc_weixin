"""
聊天历史命令行工具 — 供 Claude Code 通过 Bash 工具调用。

两级检索流程：
    1. search-summaries  — 搜索摘要（看目录）
    2. get-messages       — 查看摘要对应的原始消息（看内容）

辅助命令：
    dates               — 列出有记录的日期
    messages-by-date    — 按日期查看消息
    create-summary      — 为指定日期创建/更新摘要

用法:
    python weixin_lib/chat_history_cli.py search-summaries --query "关键词"
    python weixin_lib/chat_history_cli.py search-summaries --from 2025-01-01 --to 2025-01-31
    python weixin_lib/chat_history_cli.py get-messages --summary-id 5
    python weixin_lib/chat_history_cli.py dates
    python weixin_lib/chat_history_cli.py messages-by-date --date 2025-05-03
    python weixin_lib/chat_history_cli.py create-summary --date 2025-05-03 --text "讨论了旅行计划和酒店预订"
"""

import argparse
import json
import os
import sys

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_DIR)

from weixin_lib.chat_store import ChatStore

_DATA_DIR = os.path.join(_PROJECT_DIR, "data")
_DB_PATH = os.path.join(_DATA_DIR, "chat_history.db")


def _store():
    return ChatStore(db_path=_DB_PATH)


def cmd_search_summaries(args):
    results = _store().search_summaries(
        query=args.query,
        date_from=getattr(args, "from", None),
        date_to=args.to,
        limit=args.limit,
    )
    if not results:
        print("没有找到匹配的摘要。")
        return
    print(f"找到 {len(results)} 条摘要：\n")
    for r in results:
        print(f"  [ID:{r['id']}] {r['date_str']} — {r['summary']}")


def cmd_get_messages(args):
    results = _store().get_messages_by_summary(args.summary_id)
    if not results:
        print(f"摘要 ID {args.summary_id} 没有关联的消息。")
        return
    print(f"摘要 ID {args.summary_id} 关联的 {len(results)} 条消息：\n")
    for m in results:
        role_label = "用户" if m["role"] == "user" else "Agent"
        ts = m["timestamp"][:16].replace("T", " ")
        content_preview = m["content"][:200]
        if len(m["content"]) > 200:
            content_preview += "..."
        print(f"  [{ts}] {role_label}: {content_preview}\n")


def cmd_dates(args):
    results = _store().get_dates_with_messages(limit=args.limit)
    if not results:
        print("没有聊天记录。")
        return
    print(f"最近 {len(results)} 天有聊天记录：\n")
    for d in results:
        print(f"  {d['date_str']}  ({d['count']} 条消息)")


def cmd_messages_by_date(args):
    results = _store().get_messages_by_date(args.date)
    if not results:
        print(f"{args.date} 没有消息记录。")
        return
    print(f"{args.date} 的 {len(results)} 条消息：\n")
    for m in results:
        role_label = "用户" if m["role"] == "user" else "Agent"
        ts = m["timestamp"][:16].replace("T", " ")
        content_preview = m["content"][:200]
        if len(m["content"]) > 200:
            content_preview += "..."
        print(f"  [{ts}] {role_label}: {content_preview}\n")


def cmd_create_summary(args):
    store = _store()
    # 获取该日期未关联摘要的消息 ID
    unsummarized = store.get_unsummarized_messages(args.date)
    message_ids = [m["id"] for m in unsummarized]
    summary_id = store.create_summary(args.date, args.text, message_ids)
    print(f"✅ 摘要已创建 (ID: {summary_id}, 关联 {len(message_ids)} 条消息)")


def main():
    parser = argparse.ArgumentParser(description="聊天历史检索工具")
    sub = parser.add_subparsers(dest="command", required=True)

    # search-summaries
    p_search = sub.add_parser("search-summaries", help="搜索摘要")
    p_search.add_argument("--query", help="关键词")
    p_search.add_argument("--from", dest="from_date", help="起始日期 (YYYY-MM-DD)")
    p_search.add_argument("--to", help="结束日期 (YYYY-MM-DD)")
    p_search.add_argument("--limit", type=int, default=20, help="最大结果数")

    # get-messages
    p_get = sub.add_parser("get-messages", help="查看摘要对应的原始消息")
    p_get.add_argument("--summary-id", type=int, required=True, help="摘要 ID")

    # dates
    p_dates = sub.add_parser("dates", help="列出有记录的日期")
    p_dates.add_argument("--limit", type=int, default=30, help="最大天数")

    # messages-by-date
    p_mbd = sub.add_parser("messages-by-date", help="按日期查看消息")
    p_mbd.add_argument("--date", required=True, help="日期 (YYYY-MM-DD)")

    # create-summary
    p_cs = sub.add_parser("create-summary", help="为指定日期创建摘要")
    p_cs.add_argument("--date", required=True, help="日期 (YYYY-MM-DD)")
    p_cs.add_argument("--text", required=True, help="摘要文本")

    args = parser.parse_args()

    # 修正 --from 参数名映射
    if hasattr(args, "from_date"):
        setattr(args, "from", args.from_date)

    handlers = {
        "search-summaries": cmd_search_summaries,
        "get-messages": cmd_get_messages,
        "dates": cmd_dates,
        "messages-by-date": cmd_messages_by_date,
        "create-summary": cmd_create_summary,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
