"""
Claude Code CLI 持久化管理模块。

基于双向 stream-json 协议，维持一个持久化 Claude Code 进程。
支持多轮对话、流式输出所有中间步骤、AskUserQuestion 回调。

事件分类：
    type="system",  subtype="init"           → 会话初始化
    type="assistant", content.type="thinking" → 思考过程
    type="assistant", content.type="text"     → 正文回复
    type="assistant", content.type="tool_use" → 工具调用（含 AskUserQuestion）
    type="user",    content.type="tool_result" → 工具结果
    type="result"                             → 本轮结束

用法:
    from CC_lib.claude_cli import ClaudeChat, format_event

    chat = ClaudeChat()
    chat.start()

    # 流式观察所有输出
    for event in chat.stream("帮我写一个快速排序"):
        print(format_event(event))

    # 简单模式，只拿最终结果
    reply = chat.send("你好")

    chat.stop()
"""

import subprocess
import json
import os
from threading import Thread, Event
from queue import Queue, Empty

# 默认权限配置文件路径（与本文件同目录）
_DEFAULT_PERMISSIONS_PATH = os.path.join(os.path.dirname(__file__), "permissions.json")


def load_permissions(path=None):
    """加载权限配置，返回 dict。"""
    path = path or _DEFAULT_PERMISSIONS_PATH
    if not os.path.exists(path):
        return {"skip_all_permissions": False, "tools": {}}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_permission_args(permissions):
    """将权限配置转换为 CLI 参数列表。"""
    args = []

    if permissions.get("skip_all_permissions"):
        args.append("--dangerously-skip-permissions")

    tools = permissions.get("tools", {})
    disabled = [name for name, enabled in tools.items() if not enabled]
    if disabled:
        args += ["--disallowedTools"] + disabled

    return args


# ── 斜杠命令定义 ──────────────────────────────────────────

def _parse_slash_command(text):
    """解析斜杠命令。返回 (command, args)，非命令返回 (None, None)。"""
    text = text.strip()
    if not text.startswith("/"):
        return None, None
    parts = text[1:].split(None, 1)
    if not parts:
        return None, None
    return parts[0].lower(), (parts[1] if len(parts) > 1 else "")


# type: "local"       → 本地处理
# type: "prompt"      → 转换为等效提示词发送给 Claude
# type: "unsupported" → stream-json 模式不支持
_BUILTIN_COMMANDS = {
    # 本地处理
    "clear":          {"type": "local",       "desc": "清空对话上下文"},
    "compact":        {"type": "local",       "desc": "压缩上下文（可附加说明）"},
    "cost":           {"type": "local",       "desc": "显示累计费用"},
    "help":           {"type": "local",       "desc": "显示可用命令列表"},
    "status":         {"type": "local",       "desc": "显示会话状态"},
    "model":          {"type": "local",       "desc": "显示或切换模型（如 /model sonnet）"},
    "permissions":    {"type": "local",       "desc": "显示当前工具权限"},
    # 转换为提示词（交给 Claude 执行）
    "init":           {"type": "prompt",      "desc": "初始化项目 CLAUDE.md",
                       "prompt": "请为当前项目创建一个 CLAUDE.md 文件，包含项目概述、技术栈、开发规范等信息。"},
    "review":         {"type": "prompt",      "desc": "代码审查",
                       "prompt": "请对当前项目的代码进行全面审查，包括代码质量、潜在 bug、安全问题和改进建议。"},
    "pr_comments":    {"type": "prompt",      "desc": "查看 PR 评论",
                       "prompt": "请查看当前分支的 PR 评论并逐一处理。"},
    "memory":         {"type": "prompt",      "desc": "查看/编辑 CLAUDE.md",
                       "prompt": "请显示当前项目的 CLAUDE.md 文件内容。如果不存在，请告知。"},
    # 不支持
    "doctor":         {"type": "unsupported", "desc": "环境诊断"},
    "login":          {"type": "unsupported", "desc": "账户登录"},
    "logout":         {"type": "unsupported", "desc": "账户登出"},
    "config":         {"type": "unsupported", "desc": "配置管理"},
    "terminal-setup": {"type": "unsupported", "desc": "终端集成"},
    "vim":            {"type": "unsupported", "desc": "Vim 模式"},
    "bug":            {"type": "unsupported", "desc": "反馈 Bug"},
    "resume":         {"type": "unsupported", "desc": "恢复会话"},
}


class ClaudeChat:
    """
    持久化 Claude Code 进程管理器。

    通过双向 stream-json 协议与 Claude CLI 通信：
    - stdin:  发送用户消息（JSON lines）
    - stdout: 接收事件流（JSON lines）
    """

    def __init__(self, cwd=None, permissions_path=None):
        self.cwd = cwd
        self.permissions = load_permissions(permissions_path)
        self.proc = None
        self.session_id = None
        self._event_queue = Queue()
        self._reader_thread = None
        self._alive = False
        self._model = None
        self._total_cost = 0.0
        self._total_turns = 0
        self._context_tokens = 0
        self._context_window = 0
        self._needs_compact = False
        # 自动压缩配置
        ac = self.permissions.get("auto_compact", {})
        self._auto_compact_enabled = ac.get("enabled", False)
        self._auto_compact_threshold = ac.get("threshold_tokens", 100000)

    def start(self):
        """启动 Claude Code 进程。"""
        if self.proc and self.proc.poll() is None:
            return  # 已在运行

        cmd = [
            "claude", "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
        ]
        cmd += _build_permission_args(self.permissions)
        if self._model:
            cmd += ["--model", self._model]

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            shell=True,
        )
        self._alive = True
        self._reader_thread = Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    def stop(self):
        """关闭 Claude Code 进程。"""
        self._alive = False
        if self.proc:
            try:
                self.proc.stdin.close()
            except OSError:
                pass
            self.proc.wait(timeout=10)
            self.proc = None

    def _read_loop(self):
        """后台线程：持续读 stdout，解析事件放入队列。"""
        try:
            for raw_line in self.proc.stdout:
                if not self._alive:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    event = {"type": "raw", "text": line}
                self._event_queue.put(event)
        except (OSError, ValueError):
            pass
        finally:
            self._event_queue.put({"type": "_eof"})

    def _write(self, msg_dict):
        """向 stdin 写入 JSON 消息。"""
        line = json.dumps(msg_dict, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line.encode("utf-8"))
        self.proc.stdin.flush()

    def _send_user_message(self, text):
        """发送用户消息到 Claude。"""
        self._write({
            "type": "user",
            "message": {"role": "user", "content": text},
        })

    def stream(self, message, _raw=False):
        """
        发送消息，以生成器 yield 每个事件，直到收到 result 事件。

        特殊事件：
            {"type": "ask_user", ...}           → Claude 调用 AskUserQuestion
            {"type": "permission_denied", ...}  → 工具权限被拒绝

        参数：
            _raw: 为 True 时跳过斜杠命令拦截（内部使用）。
        """
        # ── 惰性自动压缩（上轮标记的）──
        if not _raw and self._needs_compact:
            self._needs_compact = False
            yield {
                "type": "system", "subtype": "auto_compact",
                "message": f"上下文 {self._context_tokens:,} tokens 超过阈值 {self._auto_compact_threshold:,}，正在自动压缩...",
            }
            self._do_auto_compact()

        # ── 斜杠命令拦截 ──
        if not _raw:
            cmd, args = _parse_slash_command(message)
            if cmd is not None:
                spec = _BUILTIN_COMMANDS.get(cmd)
                if spec:
                    if spec["type"] == "local":
                        yield from self._handle_local_command(cmd, args)
                        return
                    elif spec["type"] == "prompt":
                        message = spec["prompt"] + (f"\n补充说明: {args}" if args else "")
                    elif spec["type"] == "unsupported":
                        yield self._make_result(f"/{cmd} 在 stream-json 模式下不可用")
                        return
                # 非内置命令（可能是自定义 skill）→ 原样发送

        if not self.proc or self.proc.poll() is not None:
            self.start()

        # 清空队列中的旧事件
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except Empty:
                break

        self._send_user_message(message)

        # 跟踪已 yield 的 AskUserQuestion，避免重复（流式中同一 tool_use 可能多次出现）
        yielded_ask_ids = set()

        while True:
            try:
                event = self._event_queue.get(timeout=300)
            except Empty:
                yield {"type": "error", "text": "等待 Claude 响应超时"}
                break

            if event.get("type") == "_eof":
                yield {"type": "error", "text": "Claude 进程已退出"}
                break

            # 提取 session_id
            if event.get("session_id"):
                self.session_id = event["session_id"]

            # 追踪 assistant 事件中的上下文 token 数
            if event.get("type") == "assistant":
                usage = event.get("message", {}).get("usage", {})
                ctx = usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                if ctx > 0:
                    self._context_tokens = ctx

            # 检测 AskUserQuestion 工具调用
            # 流式输出中 tool_use block 可能先到达空 input，后续事件才有完整 question，
            # 因此只在 question 非空时 yield，并按 tool_use_id 去重。
            if event.get("type") == "assistant":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_use" and block.get("name") == "AskUserQuestion":
                        inp = block.get("input", {})
                        tool_use_id = block.get("id", "")
                        # 兼容两种格式：
                        # 新格式: input.questions[0].question + options[{label, description}]
                        # 旧格式: input.question + options[str]
                        questions_list = inp.get("questions", [])
                        if questions_list and isinstance(questions_list, list):
                            q_obj = questions_list[0]
                            question = q_obj.get("question", "")
                            raw_opts = q_obj.get("options", [])
                            options = []
                            for opt in raw_opts:
                                if isinstance(opt, dict):
                                    label = opt.get("label", "")
                                    desc = opt.get("description", "")
                                    options.append(f"{label}（{desc}）" if desc else label)
                                else:
                                    options.append(str(opt))
                        else:
                            question = inp.get("question", "")
                            options = inp.get("options", [])

                        if question and tool_use_id not in yielded_ask_ids:
                            yielded_ask_ids.add(tool_use_id)
                            yield {
                                "type": "ask_user",
                                "question": question,
                                "options": options,
                                "tool_use_id": tool_use_id,
                            }

            # 检测权限拒绝
            if event.get("type") == "user":
                for block in event.get("message", {}).get("content", []):
                    if block.get("type") == "tool_result" and block.get("is_error"):
                        content = str(block.get("content", ""))
                        if "requested permissions" in content.lower() or "haven't granted" in content.lower():
                            yield {
                                "type": "permission_denied",
                                "tool_use_id": block.get("tool_use_id", ""),
                                "message": content,
                            }

            # result 事件：在 yield 前注入 token 信息
            if event.get("type") == "result":
                self._total_cost += event.get("total_cost_usd", 0)
                self._total_turns += event.get("num_turns", 0)
                for info in event.get("modelUsage", {}).values():
                    cw = info.get("contextWindow", 0)
                    if cw:
                        self._context_window = cw
                event["_context_tokens"] = self._context_tokens
                event["_context_window"] = self._context_window
                if (not _raw
                        and self._auto_compact_enabled
                        and self._context_tokens > self._auto_compact_threshold):
                    self._needs_compact = True
                yield event
                break

            yield event

    # ── 斜杠命令处理 ────────────────────────────────────

    @staticmethod
    def _make_result(text):
        """构造一个合成 result 事件。"""
        return {
            "type": "result",
            "result": text,
            "total_cost_usd": 0,
            "num_turns": 0,
            "duration_ms": 0,
        }

    def _handle_local_command(self, cmd, args):
        """分发本地命令到对应处理器。"""
        handler = getattr(self, f"_cmd_{cmd.replace('-', '_')}", None)
        if handler:
            yield from handler(args)
        else:
            yield self._make_result(f"/{cmd} 暂未实现")

    def _cmd_clear(self, args):
        """清空对话上下文，重启进程。"""
        self.stop()
        self._total_cost = 0.0
        self._total_turns = 0
        yield self._make_result("上下文已清空")

    def _cmd_compact(self, args):
        """压缩上下文：生成摘要 → 重启 → 注入摘要。"""
        if not self.proc or self.proc.poll() is not None:
            yield self._make_result("没有活跃会话，无需压缩")
            return

        prompt = ("请用简洁的要点总结到目前为止的对话内容和关键上下文，"
                  "以便在新会话中恢复。只输出摘要内容。")
        if args:
            prompt += f"\n重点关注: {args}"

        summary = ""
        for event in self.stream(prompt, _raw=True):
            if event.get("type") == "result":
                summary = event.get("result", "")

        self.stop()

        if summary:
            restore = f"[上下文恢复] 以下是之前对话的摘要:\n{summary}\n\n请确认你已了解。"
            for event in self.stream(restore, _raw=True):
                pass
            yield self._make_result("上下文已压缩并恢复")
        else:
            yield self._make_result("压缩失败，会话已重启")

    def _do_auto_compact(self):
        """静默执行压缩（不 yield 事件，供自动压缩调用）。"""
        if not self.proc or self.proc.poll() is not None:
            return

        prompt = ("请用简洁的要点总结到目前为止的对话内容和关键上下文，"
                  "以便在新会话中恢复。只输出摘要内容。")
        summary = ""
        for event in self.stream(prompt, _raw=True):
            if event.get("type") == "result":
                summary = event.get("result", "")

        self.stop()

        if summary:
            restore = f"[上下文恢复] 以下是之前对话的摘要:\n{summary}\n\n请确认你已了解。"
            for event in self.stream(restore, _raw=True):
                pass

    def _cmd_cost(self, args):
        """显示累计费用。"""
        yield self._make_result(
            f"累计费用: ${self._total_cost:.4f} | 累计轮数: {self._total_turns}")

    def _cmd_help(self, args):
        """列出所有可用命令。"""
        lines = ["可用命令:"]
        for name, spec in _BUILTIN_COMMANDS.items():
            tag = " (不可用)" if spec["type"] == "unsupported" else ""
            lines.append(f"  /{name} — {spec['desc']}{tag}")
        lines.append("\n其他 /xxx 将作为自定义 Skill 发送给 Claude。")
        yield self._make_result("\n".join(lines))

    def _cmd_status(self, args):
        """显示会话状态。"""
        alive = self.proc is not None and self.proc.poll() is None
        lines = [
            f"进程状态: {'运行中' if alive else '未启动'}",
            f"会话 ID: {self.session_id or '无'}",
            f"当前模型: {self._model or '(默认)'}",
            f"累计费用: ${self._total_cost:.4f}",
            f"累计轮数: {self._total_turns}",
        ]
        yield self._make_result("\n".join(lines))

    def _cmd_model(self, args):
        """显示或切换模型。"""
        if not args:
            yield self._make_result(f"当前模型: {self._model or '(默认)'}")
        else:
            self._model = args.strip()
            self.stop()
            yield self._make_result(f"模型已切换为: {self._model}，下次对话生效")

    def _cmd_permissions(self, args):
        """显示当前工具权限。"""
        lines = [f"跳过所有权限: {self.permissions.get('skip_all_permissions', False)}"]
        tools = self.permissions.get("tools", {})
        if tools:
            lines.append("工具权限:")
            for name, enabled in tools.items():
                lines.append(f"  {name}: {'✅' if enabled else '❌'}")
        yield self._make_result("\n".join(lines))

    def answer(self, text):
        """
        回答 Claude 的 AskUserQuestion，以新用户消息的形式发送。
        发送后继续 yield 后续事件。
        """
        return self.stream(text, _raw=True)

    def send(self, message):
        """发送消息，只返回最终回复文本。"""
        result_text = ""
        for event in self.stream(message):
            if event.get("type") == "result":
                result_text = event.get("result", "")
        return result_text

    @property
    def is_alive(self):
        return self.proc is not None and self.proc.poll() is None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


def format_event(event):
    """将事件格式化为可读的终端输出。"""
    t = event.get("type", "")
    sub = event.get("subtype", "")

    if t == "system" and sub == "init":
        sid = event.get("session_id", "?")
        tools = event.get("tools", [])
        return f"[系统] 会话启动 session={sid[:12]}... 工具数={len(tools)}"

    if t == "system" and sub == "auto_compact":
        return f"[系统] {event.get('message', '')}"

    if t == "ask_user":
        q = event.get("question", "")
        opts = event.get("options", [])
        parts = [f"  ❓ AI 提问: {q}"]
        if opts:
            for i, opt in enumerate(opts):
                parts.append(f"     {i+1}. {opt}")
        return "\n".join(parts)

    if t == "permission_denied":
        return f"  🔒 权限拒绝: {event.get('message', '')}"

    if t == "assistant":
        msg = event.get("message", {})
        parts = []
        for block in msg.get("content", []):
            if block.get("type") == "thinking":
                text = block.get("thinking", "")
                parts.append(f"  💭 {text[:200]}{'...' if len(text) > 200 else ''}")
            elif block.get("type") == "text":
                parts.append(f"  📝 {block.get('text', '')}")
            elif block.get("type") == "tool_use":
                name = block.get("name", "?")
                inp = json.dumps(block.get("input", {}), ensure_ascii=False)
                parts.append(f"  🔧 [{name}] {inp[:200]}")
        return "\n".join(parts) if parts else f"  [assistant] {json.dumps(msg, ensure_ascii=False)[:200]}"

    if t == "user":
        msg = event.get("message", {})
        parts = []
        for block in msg.get("content", []):
            if block.get("type") == "tool_result":
                content = str(block.get("content", ""))
                parts.append(f"  📋 工具结果: {content[:200]}")
        return "\n".join(parts) if parts else ""

    if t == "result":
        result = event.get("result", "")
        cost = event.get("total_cost_usd", 0)
        turns = event.get("num_turns", 0)
        dur = event.get("duration_ms", 0)
        ctx = event.get("_context_tokens", 0)
        ctx_str = f", ctx={ctx/1000:.0f}k" if ctx else ""
        return f"[结果] ({turns}轮, {dur}ms, ${cost:.4f}{ctx_str}) {result}"

    if t == "error":
        return f"[错误] {event.get('text', '')}"

    if t == "raw":
        return f"[原始] {event.get('text', '')}"

    return f"[{t}/{sub}] {json.dumps(event, ensure_ascii=False)[:300]}"
