"""
定时任务调度器。

在后台线程中运行，定期检查 scheduled_tasks.json 中的待执行任务。
到期任务通过回调函数触发（通常是发送微信消息）。

任务类型：
    once  — 一次性定时任务（指定 trigger_time）
    cron  — 周期性任务（指定 cron_expr，如 "0 9 * * 1-5"）

用法：
    scheduler = Scheduler(tasks_file="scheduled_tasks.json")
    scheduler.start(callback=lambda task: send_message(task))
    ...
    scheduler.stop()
"""

import json
import os
import time
import uuid
import logging
from datetime import datetime, timedelta
from threading import Thread, Event

log = logging.getLogger("scheduler")

# cron 字段索引
_CRON_FIELDS = ("minute", "hour", "day", "month", "weekday")


def _parse_cron(expr):
    """
    解析简化版 cron 表达式（分 时 日 月 周几）。
    返回各字段的允许值集合列表。
    支持: *, 数字, 逗号分隔, 范围(1-5), 步进(*/5)。
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"cron 表达式需要 5 个字段，收到 {len(parts)}: {expr}")

    ranges = [
        (0, 59),   # minute
        (0, 23),   # hour
        (1, 31),   # day
        (1, 12),   # month
        (0, 6),    # weekday (0=Monday ... 6=Sunday, 与 datetime.weekday() 一致)
    ]

    result = []
    for part, (lo, hi) in zip(parts, ranges):
        values = set()
        for token in part.split(","):
            if token == "*":
                values.update(range(lo, hi + 1))
            elif "/" in token:
                base, step = token.split("/", 1)
                step = int(step)
                start = lo if base == "*" else int(base)
                values.update(range(start, hi + 1, step))
            elif "-" in token:
                a, b = token.split("-", 1)
                values.update(range(int(a), int(b) + 1))
            else:
                values.add(int(token))
        result.append(values)
    return result


def _cron_matches(parsed_cron, dt):
    """检查 datetime 是否匹配已解析的 cron 表达式。"""
    checks = [dt.minute, dt.hour, dt.day, dt.month, dt.weekday()]
    return all(val in allowed for val, allowed in zip(checks, parsed_cron))


def _next_cron_time(parsed_cron, after):
    """计算 cron 表达式在 after 之后的下一次触发时间（最多搜索 366 天）。"""
    # 从下一分钟开始
    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = after + timedelta(days=366)
    while candidate < limit:
        if _cron_matches(parsed_cron, candidate):
            return candidate
        candidate += timedelta(minutes=1)
    return None


class Scheduler:
    """定时任务调度器。"""

    def __init__(self, tasks_file="scheduled_tasks.json", check_interval=30):
        self.tasks_file = tasks_file
        self.check_interval = check_interval
        self._stop_event = Event()
        self._thread = None
        self._callback = None

    def start(self, callback):
        """
        启动调度器后台线程。

        callback(task_dict) 在任务到期时被调用，
        task_dict 包含 id, message, target_user 等字段。
        """
        self._callback = callback
        self._stop_event.clear()
        self._thread = Thread(target=self._loop, daemon=True)
        self._thread.start()
        log.info(f"⏰ 调度器已启动（检查间隔 {self.check_interval}s，文件 {self.tasks_file}）")

    def stop(self):
        """停止调度器。"""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        log.info("⏰ 调度器已停止")

    def _loop(self):
        """后台循环：定期检查并执行到期任务。"""
        while not self._stop_event.is_set():
            try:
                self._check_tasks()
            except Exception as e:
                log.error(f"调度器检查出错: {e}", exc_info=True)
            self._stop_event.wait(self.check_interval)

    def _check_tasks(self):
        """检查所有待执行任务，执行到期的任务。"""
        tasks = self._load_tasks()
        if not tasks:
            return

        now = datetime.now()
        changed = False

        for task in tasks:
            if task.get("status") != "pending":
                continue

            task_type = task.get("type", "once")

            if task_type == "once":
                trigger = datetime.fromisoformat(task["trigger_time"])
                if now >= trigger:
                    self._fire(task)
                    task["status"] = "done"
                    changed = True

            elif task_type == "cron":
                last_run = task.get("last_run")
                if last_run:
                    last_dt = datetime.fromisoformat(last_run)
                else:
                    last_dt = datetime.fromisoformat(task["created_at"]) - timedelta(minutes=1)

                parsed = _parse_cron(task["cron_expr"])
                next_time = _next_cron_time(parsed, last_dt)
                if next_time and now >= next_time:
                    self._fire(task)
                    task["last_run"] = now.isoformat()
                    changed = True

        if changed:
            self._save_tasks(tasks)

    def _fire(self, task):
        """触发一个任务。"""
        log.info(f"⏰ 触发任务: {task.get('id', '?')} → {task.get('message', '')[:50]}")
        if self._callback:
            try:
                self._callback(task)
            except Exception as e:
                log.error(f"任务回调出错: {e}", exc_info=True)

    def _load_tasks(self):
        """从 JSON 文件加载任务列表。"""
        if not os.path.exists(self.tasks_file):
            return []
        try:
            with open(self.tasks_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            log.error(f"加载任务文件失败: {e}")
            return []

    def _save_tasks(self, tasks):
        """保存任务列表到 JSON 文件。"""
        try:
            with open(self.tasks_file, "w", encoding="utf-8") as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except OSError as e:
            log.error(f"保存任务文件失败: {e}")


# ── 供 schedule_cli.py 使用的任务管理函数 ────────────────

def add_task(tasks_file, task_type, message, target_user,
             trigger_time=None, cron_expr=None):
    """添加一个新任务，返回任务 ID。"""
    tasks = []
    if os.path.exists(tasks_file):
        with open(tasks_file, "r", encoding="utf-8") as f:
            tasks = json.load(f)

    task_id = uuid.uuid4().hex[:8]
    now = datetime.now()

    task = {
        "id": task_id,
        "type": task_type,
        "message": message,
        "target_user": target_user,
        "created_at": now.isoformat(),
        "status": "pending",
    }

    if task_type == "once":
        if not trigger_time:
            raise ValueError("一次性任务需要 trigger_time")
        # 解析时间
        dt = _parse_time_str(trigger_time)
        task["trigger_time"] = dt.isoformat()
    elif task_type == "cron":
        if not cron_expr:
            raise ValueError("周期任务需要 cron_expr")
        _parse_cron(cron_expr)  # 检查语法
        task["cron_expr"] = cron_expr

    tasks.append(task)

    with open(tasks_file, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

    return task_id


def list_tasks(tasks_file):
    """列出所有 pending 状态的任务。"""
    if not os.path.exists(tasks_file):
        return []
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    return [t for t in tasks if t.get("status") == "pending"]


def delete_task(tasks_file, task_id):
    """删除（标记为 cancelled）指定任务。返回是否找到。"""
    if not os.path.exists(tasks_file):
        return False
    with open(tasks_file, "r", encoding="utf-8") as f:
        tasks = json.load(f)
    found = False
    for task in tasks:
        if task["id"] == task_id:
            task["status"] = "cancelled"
            found = True
            break
    if found:
        with open(tasks_file, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    return found


def _parse_time_str(s):
    """
    解析时间字符串。支持:
        "15:00"              → 今天 15:00（已过则明天）
        "2025-05-03 15:00"   → 指定日期时间
    """
    s = s.strip()
    now = datetime.now()

    # 尝试完整格式
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass

    # 仅时间
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            t = datetime.strptime(s, fmt).time()
            dt = now.replace(hour=t.hour, minute=t.minute, second=t.second, microsecond=0)
            if dt <= now:
                dt += timedelta(days=1)
            return dt
        except ValueError:
            pass

    raise ValueError(f"无法解析时间: {s}")
