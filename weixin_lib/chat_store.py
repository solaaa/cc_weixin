"""
聊天记录持久化存储。

使用 SQLite 存储对话消息和每日摘要，支持两级检索：
    1. 搜索摘要（按关键词 / 日期范围）—— 相当于"看目录"
    2. 查看摘要对应的原始消息 —— 相当于"看内容"

表结构：
    messages  — 每条有价值的对话消息（用户问题 + Agent 回复）
    summaries — 每日（或自定义周期）的对话摘要
"""

import os
import sqlite3
import time
from datetime import datetime, date


_DB_NAME = "chat_history.db"


class ChatStore:
    """聊天记录 SQLite 存储。"""

    def __init__(self, db_path=None):
        if db_path is None:
            data_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
            )
            os.makedirs(data_dir, exist_ok=True)
            db_path = os.path.join(data_dir, _DB_NAME)
        self.db_path = db_path
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._connect()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    date_str    TEXT NOT NULL,
                    role        TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    session_id  TEXT,
                    summary_id  INTEGER
                );

                CREATE TABLE IF NOT EXISTS summaries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_str    TEXT NOT NULL,
                    summary     TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_date ON messages(date_str);
                CREATE INDEX IF NOT EXISTS idx_messages_summary ON messages(summary_id);
                CREATE INDEX IF NOT EXISTS idx_summaries_date ON summaries(date_str);
            """)
            conn.commit()
        finally:
            conn.close()

    # ── 消息写入 ──────────────────────────────────────────

    def add_message(self, role, content, session_id=None):
        """
        写入一条消息。

        role: "user" 或 "assistant"
        content: 消息文本
        """
        now = datetime.now()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO messages (timestamp, date_str, role, content, session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (now.isoformat(), now.strftime("%Y-%m-%d"), role, content, session_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── 摘要管理 ──────────────────────────────────────────

    def get_unsummarized_messages(self, date_str=None):
        """获取指定日期（默认今天）尚未关联摘要的消息。"""
        if date_str is None:
            date_str = date.today().isoformat()
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, timestamp, role, content FROM messages "
                "WHERE date_str = ? AND summary_id IS NULL "
                "ORDER BY id",
                (date_str,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def create_summary(self, date_str, summary_text, message_ids):
        """
        创建一条摘要，并将对应消息关联到该摘要。

        返回 summary_id。
        """
        now = datetime.now()
        conn = self._connect()
        try:
            cursor = conn.execute(
                "INSERT INTO summaries (date_str, summary, created_at) VALUES (?, ?, ?)",
                (date_str, summary_text, now.isoformat()),
            )
            summary_id = cursor.lastrowid
            if message_ids:
                placeholders = ",".join("?" * len(message_ids))
                conn.execute(
                    f"UPDATE messages SET summary_id = ? WHERE id IN ({placeholders})",
                    [summary_id] + list(message_ids),
                )
            conn.commit()
            return summary_id
        finally:
            conn.close()

    # ── 检索：第 1 步 — 搜索摘要 ──────────────────────────

    def search_summaries(self, query=None, date_from=None, date_to=None, limit=20):
        """
        搜索摘要。

        query: 关键词（模糊匹配）
        date_from / date_to: 日期范围（YYYY-MM-DD）
        """
        conn = self._connect()
        try:
            conditions = []
            params = []

            if query:
                conditions.append("summary LIKE ?")
                params.append(f"%{query}%")
            if date_from:
                conditions.append("date_str >= ?")
                params.append(date_from)
            if date_to:
                conditions.append("date_str <= ?")
                params.append(date_to)

            where = " AND ".join(conditions) if conditions else "1=1"
            rows = conn.execute(
                f"SELECT id, date_str, summary, created_at FROM summaries "
                f"WHERE {where} ORDER BY date_str DESC, id DESC LIMIT ?",
                params + [limit],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── 检索：第 2 步 — 查看摘要对应的消息 ─────────────────

    def get_messages_by_summary(self, summary_id):
        """获取某条摘要关联的所有原始消息。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, timestamp, role, content FROM messages "
                "WHERE summary_id = ? ORDER BY id",
                (summary_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_messages_by_date(self, date_str):
        """获取某天的所有消息。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT id, timestamp, role, content FROM messages "
                "WHERE date_str = ? ORDER BY id",
                (date_str,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # ── 统计 ──────────────────────────────────────────────

    def get_dates_with_messages(self, limit=30):
        """获取有消息记录的日期列表（最近 N 天）。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT date_str, COUNT(*) as count FROM messages "
                "GROUP BY date_str ORDER BY date_str DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_unsummarized_dates(self):
        """获取所有有未关联摘要消息的日期及消息数量。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT date_str, COUNT(*) as count FROM messages "
                "WHERE summary_id IS NULL "
                "GROUP BY date_str ORDER BY date_str",
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
